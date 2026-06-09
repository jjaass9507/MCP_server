import contextlib
import sqlite3
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from mcp_server.utils.errors import ToolError

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule


@contextlib.contextmanager
def _get_conn(db_path: str):
    """Open a SQLite connection; commit on success, rollback on error."""
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


def register(mcp: FastMCP, cfg: "_CfgModule") -> None:

    @mcp.tool()
    def db_list_databases() -> list[str]:
        """List the names of all databases configured in config.toml.

        Use one of these names as the db_name parameter in other database tools.
        """
        names = cfg.list_db_names()
        if not names:
            raise ToolError(
                "No databases are configured. "
                "Add entries under [database.connections] in config.toml."
            )
        return names

    @mcp.tool()
    def db_query(db_name: str, sql: str, params: list[Any] = []) -> list[dict]:
        """Execute a SELECT query against a named database.

        Returns rows as a list of dicts (column name → value).
        Only SELECT statements are allowed; use db_execute for writes.

        Args:
            db_name: Database name from config.toml (e.g. 'mydb'). Use db_list_databases() to see options.
            sql:     A SELECT SQL statement.
            params:  Optional positional parameters matching ? placeholders.
        """
        if not sql.strip().upper().startswith("SELECT"):
            raise ToolError("db_query only accepts SELECT statements. Use db_execute for INSERT/UPDATE/DELETE.")
        db_path = cfg.resolve_db(db_name)
        with _get_conn(db_path) as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    @mcp.tool()
    def db_execute(db_name: str, sql: str, params: list[Any] = []) -> dict:
        """Execute an INSERT, UPDATE, or DELETE statement against a named database.

        Returns {"rows_affected": int, "last_insert_id": int}.

        Args:
            db_name: Database name from config.toml. Use db_list_databases() to see options.
            sql:     An INSERT, UPDATE, or DELETE SQL statement.
            params:  Optional positional parameters matching ? placeholders.
        """
        first_word = sql.strip().upper().split()[0] if sql.strip() else ""
        if first_word == "SELECT":
            raise ToolError("db_execute does not accept SELECT. Use db_query for reads.")
        db_path = cfg.resolve_db(db_name)
        with _get_conn(db_path) as conn:
            cursor = conn.execute(sql, params)
            return {
                "rows_affected": cursor.rowcount,
                "last_insert_id": cursor.lastrowid or 0,
            }

    @mcp.tool()
    def db_list_tables(db_name: str) -> list[str]:
        """List all table names in a named database.

        Args:
            db_name: Database name from config.toml. Use db_list_databases() to see options.
        """
        db_path = cfg.resolve_db(db_name)
        with _get_conn(db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [row["name"] for row in cursor.fetchall()]

    @mcp.tool()
    def db_table_schema(db_name: str, table_name: str) -> list[dict]:
        """Get the column definitions for a table in a named database.

        Returns columns with: cid, name, type, notnull, default_value, is_primary_key.

        Args:
            db_name:    Database name from config.toml. Use db_list_databases() to see options.
            table_name: Name of the table to inspect.
        """
        db_path = cfg.resolve_db(db_name)
        with _get_conn(db_path) as conn:
            cursor = conn.execute(f"PRAGMA table_info({table_name})")
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

        Statements are separated by semicolons. Use for schema migrations or bulk inserts.

        Args:
            db_name: Database name from config.toml. Use db_list_databases() to see options.
            script:  One or more SQL statements separated by semicolons.
        """
        db_path = cfg.resolve_db(db_name)
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript(script)
            conn.close()
            return f"Script executed successfully on database '{db_name}'"
        except sqlite3.Error as e:
            raise ToolError(f"Script execution failed: {e}") from e
