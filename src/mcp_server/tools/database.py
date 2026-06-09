import contextlib
import sqlite3
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server.utils.errors import ToolError


@contextlib.contextmanager
def _get_conn(db_path: str):
    """Context manager: open a SQLite connection, commit on success, rollback on error."""
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


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def db_query(db_path: str, sql: str, params: list[Any] = []) -> list[dict]:
        """Execute a SELECT query against a SQLite database.

        Returns rows as a list of dicts (column name → value).
        Only SELECT statements are allowed; use db_execute for writes.

        Args:
            db_path: Path to the .db file (created if it does not exist).
            sql:     A SELECT SQL statement.
            params:  Optional list of positional parameters for the query (? placeholders).
        """
        if not sql.strip().upper().startswith("SELECT"):
            raise ToolError("db_query only accepts SELECT statements. Use db_execute for INSERT/UPDATE/DELETE.")
        with _get_conn(db_path) as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    @mcp.tool()
    def db_execute(db_path: str, sql: str, params: list[Any] = []) -> dict:
        """Execute an INSERT, UPDATE, or DELETE statement against a SQLite database.

        Returns {"rows_affected": int, "last_insert_id": int}.

        Args:
            db_path: Path to the .db file (created if it does not exist).
            sql:     An INSERT, UPDATE, or DELETE SQL statement.
            params:  Optional list of positional parameters (? placeholders).
        """
        first_word = sql.strip().upper().split()[0] if sql.strip() else ""
        if first_word == "SELECT":
            raise ToolError("db_execute does not accept SELECT statements. Use db_query for reads.")
        with _get_conn(db_path) as conn:
            cursor = conn.execute(sql, params)
            return {
                "rows_affected": cursor.rowcount,
                "last_insert_id": cursor.lastrowid or 0,
            }

    @mcp.tool()
    def db_list_tables(db_path: str) -> list[str]:
        """List all table names in a SQLite database.

        Args:
            db_path: Path to the .db file.
        """
        with _get_conn(db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [row["name"] for row in cursor.fetchall()]

    @mcp.tool()
    def db_table_schema(db_path: str, table_name: str) -> list[dict]:
        """Get the column definitions for a SQLite table.

        Returns a list of dicts with: cid, name, type, notnull, default_value, is_primary_key.

        Args:
            db_path:    Path to the .db file.
            table_name: Name of the table to inspect.
        """
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
    def db_execute_script(db_path: str, script: str) -> str:
        """Execute a multi-statement SQL script (e.g., schema migrations or bulk inserts).

        Statements are separated by semicolons. The script runs in a single transaction.

        Args:
            db_path: Path to the .db file (created if it does not exist).
            script:  One or more SQL statements separated by semicolons.
        """
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript(script)
            conn.close()
            return f"Script executed successfully against {db_path}"
        except sqlite3.Error as e:
            raise ToolError(f"Script execution failed: {e}") from e
