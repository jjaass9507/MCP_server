import pathlib
import stat
from datetime import datetime
from typing import Literal

from mcp.server.fastmcp import FastMCP

from mcp_server.utils.errors import ToolError

MAX_READ_BYTES = 1 * 1024 * 1024  # 1 MB


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def read_file(path: str) -> str:
        """Read the text contents of a file.

        Returns the file contents as a string. Files larger than 1 MB are
        truncated with a warning appended at the end.
        """
        p = pathlib.Path(path).resolve()
        if not p.exists():
            raise ToolError(f"File not found: {path}")
        if not p.is_file():
            raise ToolError(f"Path is not a file: {path}")
        try:
            size = p.stat().st_size
            text = p.read_text(encoding="utf-8", errors="replace")
            if size > MAX_READ_BYTES:
                text = text[:MAX_READ_BYTES]
                text += f"\n\n[WARNING: file truncated — original size {size} bytes, showing first {MAX_READ_BYTES} bytes]"
            return text
        except OSError as e:
            raise ToolError(f"Could not read file: {e}") from e

    @mcp.tool()
    def write_file(
        path: str,
        content: str,
        mode: Literal["overwrite", "append"] = "overwrite",
    ) -> str:
        """Write or append text content to a file.

        Creates the file (and any parent directories) if it does not exist.
        Use mode='append' to add to an existing file without overwriting it.
        """
        p = pathlib.Path(path).resolve()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            write_mode = "a" if mode == "append" else "w"
            p.write_text(content, encoding="utf-8") if write_mode == "w" else p.open("a", encoding="utf-8").write(content)
            return f"Successfully wrote {len(content)} characters to {path}"
        except OSError as e:
            raise ToolError(f"Could not write file: {e}") from e

    @mcp.tool()
    def list_directory(path: str, recursive: bool = False) -> list[dict]:
        """List the contents of a directory.

        Returns a list of entries, each with: name, type ('file' or 'dir'),
        size (bytes, 0 for dirs), and modified (ISO 8601 timestamp).
        Set recursive=True to include all nested contents.
        """
        p = pathlib.Path(path).resolve()
        if not p.exists():
            raise ToolError(f"Path not found: {path}")
        if not p.is_dir():
            raise ToolError(f"Path is not a directory: {path}")
        try:
            iterator = p.rglob("*") if recursive else p.iterdir()
            entries = []
            for entry in sorted(iterator, key=lambda e: (e.is_file(), e.name)):
                s = entry.stat()
                entries.append({
                    "name": str(entry.relative_to(p)),
                    "type": "file" if entry.is_file() else "dir",
                    "size": s.st_size if entry.is_file() else 0,
                    "modified": datetime.fromtimestamp(s.st_mtime).isoformat(),
                })
            return entries
        except OSError as e:
            raise ToolError(f"Could not list directory: {e}") from e

    @mcp.tool()
    def search_files(directory: str, pattern: str, recursive: bool = True) -> list[str]:
        """Search for files matching a glob pattern within a directory.

        Pattern examples: '*.py', '**/*.json', 'data_*.csv'
        Returns a list of matching file paths as strings.
        Set recursive=False to search only the top-level directory.
        """
        p = pathlib.Path(directory).resolve()
        if not p.exists():
            raise ToolError(f"Directory not found: {directory}")
        if not p.is_dir():
            raise ToolError(f"Path is not a directory: {directory}")
        try:
            glob_fn = p.rglob if recursive else p.glob
            matches = [str(m) for m in sorted(glob_fn(pattern)) if m.is_file()]
            return matches
        except OSError as e:
            raise ToolError(f"Search failed: {e}") from e

    @mcp.tool()
    def file_info(path: str) -> dict:
        """Get metadata about a file or directory.

        Returns: path, type, size (bytes), modified, created, permissions (octal).
        """
        p = pathlib.Path(path).resolve()
        if not p.exists():
            raise ToolError(f"Path not found: {path}")
        try:
            s = p.stat()
            return {
                "path": str(p),
                "type": "file" if p.is_file() else "dir",
                "size": s.st_size,
                "modified": datetime.fromtimestamp(s.st_mtime).isoformat(),
                "created": datetime.fromtimestamp(s.st_ctime).isoformat(),
                "permissions": oct(stat.S_IMODE(s.st_mode)),
            }
        except OSError as e:
            raise ToolError(f"Could not get file info: {e}") from e

    @mcp.tool()
    def delete_file(path: str) -> str:
        """Delete a file at the given path.

        Only files can be deleted with this tool (not directories).
        This action is irreversible.
        """
        p = pathlib.Path(path).resolve()
        if not p.exists():
            raise ToolError(f"File not found: {path}")
        if not p.is_file():
            raise ToolError(f"Path is not a file (use a dedicated tool for directories): {path}")
        try:
            p.unlink()
            return f"Deleted: {path}"
        except OSError as e:
            raise ToolError(f"Could not delete file: {e}") from e
