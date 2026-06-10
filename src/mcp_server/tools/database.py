import contextlib
import json
import sqlite3
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from mcp_server.utils.errors import ToolError

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule


# ── connection context managers ───────────────────────────────────────────────

@contextlib.contextmanager
def _sqlite_conn(db_path: str):
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        raise ToolError(f"Database error: {e}") from e
    finally:
        conn.close()


@contextlib.contextmanager
def _pg_conn(dsn: str):
    try:
        import psycopg
    except ImportError as e:
        raise ToolError(
            "psycopg is not installed. "
            "Run: pip install 'psycopg[binary]'"
        ) from e
    try:
        with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as cur:
                yield cur
                conn.commit()
    except psycopg.Error as e:
        raise ToolError(f"Database error: {e}") from e


@contextlib.contextmanager
def _get_conn(dsn: str, cfg: "_CfgModule"):
    """Yield a unified cursor-like object for either SQLite or PostgreSQL."""
    if cfg.is_postgres(dsn):
        with _pg_conn(dsn) as cur:
            yield cur
    else:
        with _sqlite_conn(dsn) as conn:
            yield conn


# ── tool registration ─────────────────────────────────────────────────────────

def register(mcp: FastMCP, cfg: "_CfgModule") -> None:

    @mcp.tool()
    def db_list_databases() -> str:
        """List the names of all databases configured in config.toml.

        Use one of the returned names as the db_name parameter in other database tools.
        """
        names = cfg.list_db_names()
        if not names:
            raise ToolError(
                "No databases are configured. "
                "Add entries under [database.connections] in config.toml."
            )
        return f"Available databases: {', '.join(names)}. Use one of these as the db_name parameter."

    def _resolve_db_name(db_name: str) -> str:
        if db_name:
            return db_name
        names = cfg.list_db_names()
        if not names:
            raise ToolError("No databases configured.")
        if len(names) == 1:
            return names[0]
        raise ToolError(f"Please specify db_name. Available: {', '.join(names)}")

    @mcp.tool()
    def db_query(db_name: str = "", sql: str = "", params: list[Any] = []) -> str:
        """Execute a SELECT query against a named database.

        Returns query results as a JSON string (list of row objects).
        Only SELECT statements are allowed; use db_execute for writes.
        Supports both SQLite (file path) and PostgreSQL (DSN) connections.

        Args:
            db_name: Database name from config.toml (e.g. 'mydb'). Use db_list_databases() to see options.
            sql:     A SELECT SQL statement.
            params:  Optional positional parameters (%s for PostgreSQL, ? for SQLite).
        """
        if not sql.strip().upper().startswith("SELECT"):
            raise ToolError("db_query only accepts SELECT statements. Use db_execute for INSERT/UPDATE/DELETE.")
        dsn = cfg.resolve_db(_resolve_db_name(db_name))
        with _get_conn(dsn, cfg) as cur:
            if cfg.is_postgres(dsn):
                cur.execute(sql, params or None)
                rows = cur.fetchall()
            else:
                cursor = cur.execute(sql, params)
                rows = [dict(row) for row in cursor.fetchall()]
        return json.dumps(rows, ensure_ascii=False, default=str)

    @mcp.tool()
    def db_execute(db_name: str = "", sql: str = "", params: list[Any] = []) -> dict:
        """Execute an INSERT, UPDATE, or DELETE statement against a named database.

        Returns {"rows_affected": int, "last_insert_id": int}.
        Supports both SQLite and PostgreSQL connections.

        Args:
            db_name: Database name from config.toml. Use db_list_databases() to see options.
            sql:     An INSERT, UPDATE, or DELETE SQL statement.
            params:  Optional positional parameters (%s for PostgreSQL, ? for SQLite).
        """
        first_word = sql.strip().upper().split()[0] if sql.strip() else ""
        if first_word == "SELECT":
            raise ToolError("db_execute does not accept SELECT. Use db_query for reads.")
        dsn = cfg.resolve_db(_resolve_db_name(db_name))
        with _get_conn(dsn, cfg) as cur:
            if cfg.is_postgres(dsn):
                cur.execute(sql, params or None)
                return {
                    "rows_affected": cur.rowcount,
                    "last_insert_id": 0,
                }
            else:
                cursor = cur.execute(sql, params)
                return {
                    "rows_affected": cursor.rowcount,
                    "last_insert_id": cursor.lastrowid or 0,
                }

    @mcp.tool()
    def db_list_tables(db_name: str = "") -> str:
        """List all table names in a named database.

        Args:
            db_name: Database name from config.toml. If omitted and only one database
                     is configured, it is selected automatically.
                     Use db_list_databases() to see available names.
        """
        if not db_name:
            names = cfg.list_db_names()
            if not names:
                raise ToolError("No databases configured. Add entries under [database.connections] in config.toml.")
            if len(names) == 1:
                db_name = names[0]
            else:
                return f"Please specify db_name. Available databases: {', '.join(names)}"
        dsn = cfg.resolve_db(db_name)
        with _get_conn(dsn, cfg) as cur:
            if cfg.is_postgres(dsn):
                cur.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
                )
                tables = [row["tablename"] for row in cur.fetchall()]
            else:
                cursor = cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                tables = [row["name"] for row in cursor.fetchall()]
        if not tables:
            return f"Database '{db_name}' has no tables."
        return f"Tables in '{db_name}' ({len(tables)} total): {', '.join(tables)}"

    @mcp.tool()
    def db_table_schema(db_name: str = "", table_name: str = "") -> list[dict]:
        """Get the column definitions for a table in a named database.

        For SQLite: returns cid, name, type, notnull, default_value, is_primary_key.
        For PostgreSQL: returns name, type, notnull, default_value, is_primary_key.

        Args:
            db_name:    Database name from config.toml. Use db_list_databases() to see options.
            table_name: Name of the table to inspect.
        """
        dsn = cfg.resolve_db(_resolve_db_name(db_name))
        with _get_conn(dsn, cfg) as cur:
            if cfg.is_postgres(dsn):
                cur.execute(
                    """
                    SELECT
                        c.column_name          AS name,
                        c.data_type            AS type,
                        c.is_nullable = 'NO'   AS notnull,
                        c.column_default        AS default_value,
                        EXISTS (
                            SELECT 1 FROM information_schema.table_constraints tc
                            JOIN information_schema.key_column_usage kcu
                              ON tc.constraint_name = kcu.constraint_name
                             AND tc.table_name = kcu.table_name
                            WHERE tc.constraint_type = 'PRIMARY KEY'
                              AND tc.table_name = c.table_name
                              AND kcu.column_name = c.column_name
                        ) AS is_primary_key
                    FROM information_schema.columns c
                    WHERE c.table_name = %s AND c.table_schema = 'public'
                    ORDER BY c.ordinal_position
                    """,
                    (table_name,),
                )
                rows = cur.fetchall()
                if not rows:
                    raise ToolError(f"Table not found or empty schema: {table_name}")
                return rows
            else:
                cursor = cur.execute(f"PRAGMA table_info({table_name})")
                rows = cursor.fetchall()
                if not rows:
                    raise ToolError(f"Table not found or empty schema: {table_name}")
                return [
                    {
                        "cid": row["cid"],
                        "name": row["name"],
                        "type": row["type"],
                        "notnull": bool(row["notnull"]),
                        "default_value": row["dflt_value"],
                        "is_primary_key": bool(row["pk"]),
                    }
                    for row in rows
                ]

    @mcp.tool()
    def db_execute_script(db_name: str, script: str) -> str:
        """Execute a multi-statement SQL script against a named database.

        For SQLite, statements are separated by semicolons.
        For PostgreSQL, the entire script is sent as-is.

        Args:
            db_name: Database name from config.toml. Use db_list_databases() to see options.
            script:  One or more SQL statements.
        """
        dsn = cfg.resolve_db(db_name)
        try:
            if cfg.is_postgres(dsn):
                with _pg_conn(dsn) as cur:
                    cur.execute(script)
            else:
                conn = sqlite3.connect(dsn)
                conn.executescript(script)
                conn.close()
            return f"Script executed successfully on database '{db_name}'"
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Script execution failed: {e}") from e
