import pathlib
import stat
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from mcp.server.fastmcp import FastMCP

from mcp_server.utils.errors import ToolError

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule

MAX_READ_BYTES = 1 * 1024 * 1024  # 1 MB


def register(mcp: FastMCP, cfg: "_CfgModule") -> None:

    @mcp.tool()
    def read_file(path: str) -> str:
        """Read the text contents of a file.

        The path must be inside an allowed directory configured in config.toml.
        Files larger than 1 MB are truncated with a warning appended.
        """
        p = pathlib.Path(path).resolve()
        cfg.check_path(p)
        if not p.exists():
            raise ToolError(f"File not found: {path}")
        if not p.is_file():
            raise ToolError(f"Path is not a file: {path}")
        try:
            size = p.stat().st_size
            text = p.read_text(encoding="utf-8", errors="replace")
            if size > MAX_READ_BYTES:
                text = text[:MAX_READ_BYTES]
                text += (
                    f"\n\n[WARNING: file truncated — original size {size} bytes, "
                    f"showing first {MAX_READ_BYTES} bytes]"
                )
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

        The path must be inside an allowed directory and write access must be
        enabled in config.toml. Creates the file (and parent directories) if
        they do not exist.
        """
        p = pathlib.Path(path).resolve()
        cfg.check_path(p, write=True)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append":
                with p.open("a", encoding="utf-8") as f:
                    f.write(content)
            else:
                p.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} characters to {path}"
        except OSError as e:
            raise ToolError(f"Could not write file: {e}") from e

    @mcp.tool()
    def list_directory(path: str, recursive: bool = False) -> list[dict]:
        """List the contents of a directory.

        The path must be inside an allowed directory configured in config.toml.
        Returns entries with: name, type ('file'|'dir'), size (bytes), modified (ISO 8601).
        Set recursive=True to include all nested contents.
        """
        p = pathlib.Path(path).resolve()
        cfg.check_path(p)
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
        """Search for files matching a glob pattern within an allowed directory.

        Pattern examples: '*.py', '**/*.json', 'data_*.csv'
        Returns matching file paths as strings.
        """
        p = pathlib.Path(directory).resolve()
        cfg.check_path(p)
        if not p.exists():
            raise ToolError(f"Directory not found: {directory}")
        if not p.is_dir():
            raise ToolError(f"Path is not a directory: {directory}")
        try:
            glob_fn = p.rglob if recursive else p.glob
            return [str(m) for m in sorted(glob_fn(pattern)) if m.is_file()]
        except OSError as e:
            raise ToolError(f"Search failed: {e}") from e

    @mcp.tool()
    def file_info(path: str) -> dict:
        """Get metadata about a file or directory.

        The path must be inside an allowed directory.
        Returns: path, type, size (bytes), modified, created, permissions (octal).
        """
        p = pathlib.Path(path).resolve()
        cfg.check_path(p)
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
        """Delete a file. Write access must be enabled in config.toml.

        The path must be inside an allowed directory.
        Only files can be deleted (not directories). This action is irreversible.
        """
        p = pathlib.Path(path).resolve()
        cfg.check_path(p, write=True)
        if not p.exists():
            raise ToolError(f"File not found: {path}")
        if not p.is_file():
            raise ToolError(f"Path is not a file: {path}")
        try:
            p.unlink()
            return f"Deleted: {path}"
        except OSError as e:
            raise ToolError(f"Could not delete file: {e}") from e
