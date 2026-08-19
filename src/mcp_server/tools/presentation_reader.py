"""Read existing PowerPoint files as text plus rendered slide images."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
from typing import TYPE_CHECKING, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from pypdf import PdfReader

from mcp_server.utils.errors import ToolError
from mcp_server.utils.logging import get_logger

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule

logger = get_logger("presentation_reader")

SUPPORTED_EXTENSIONS = {".ppt", ".pptx"}
MAX_PRESENTATION_BYTES = 100 * 1024 * 1024
MAX_SLIDES_PER_CALL = 6
DEFAULT_SLIDES_PER_CALL = 3
_CACHE_TTL_SECONDS = 60 * 60


def _find_command(*names: str) -> str:
    for name in names:
        executable = shutil.which(name)
        if executable:
            return executable
    raise ToolError(
        f"Required command not found: {' or '.join(names)}. "
        "Install LibreOffice Impress and Poppler utilities."
    )


def _slide_window(total: int, start_slide: int, limit: int) -> tuple[int, int, int | None]:
    """Return zero-based [start, end) indexes and the next one-based slide."""
    if total < 1:
        raise ToolError("The presentation contains no slides.")
    if start_slide < 1:
        raise ToolError("start_slide must be 1 or greater.")
    if start_slide > total:
        raise ToolError(
            f"start_slide {start_slide} is beyond the final slide ({total})."
        )
    if limit < 1:
        raise ToolError("limit must be 1 or greater.")

    bounded_limit = min(limit, MAX_SLIDES_PER_CALL)
    start_index = start_slide - 1
    end_index = min(start_index + bounded_limit, total)
    next_slide = end_index + 1 if end_index < total else None
    return start_index, end_index, next_slide


def _cache_key(source: pathlib.Path) -> str:
    stat = source.stat()
    identity = f"{source}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _cache_dir() -> pathlib.Path:
    path = pathlib.Path(tempfile.gettempdir()) / "mcp-presentation-cache"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _cleanup_cache(cache_dir: pathlib.Path, keep: pathlib.Path) -> None:
    cutoff = time.time() - _CACHE_TTL_SECONDS
    for candidate in cache_dir.glob("*.pdf"):
        if candidate == keep:
            continue
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            logger.debug("Could not clean cached presentation: %s", candidate)


def _convert_to_cached_pdf(source: pathlib.Path) -> pathlib.Path:
    cache_dir = _cache_dir()
    cached_pdf = cache_dir / f"{_cache_key(source)}.pdf"
    if cached_pdf.exists():
        return cached_pdf

    soffice = _find_command("soffice", "libreoffice", "soffice.exe")
    with tempfile.TemporaryDirectory(prefix="mcp-presentation-") as temp_dir:
        work_dir = pathlib.Path(temp_dir)
        profile_dir = work_dir / "libreoffice-profile"
        profile_dir.mkdir()
        result = subprocess.run(
            [
                soffice,
                f"-env:UserInstallation={profile_dir.as_uri()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(work_dir),
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise ToolError(f"LibreOffice could not convert the presentation: {detail}")

        converted = work_dir / f"{source.stem}.pdf"
        if not converted.exists():
            candidates = list(work_dir.glob("*.pdf"))
            if len(candidates) != 1:
                raise ToolError("LibreOffice reported success but produced no PDF file.")
            converted = candidates[0]

        os.replace(converted, cached_pdf)

    _cleanup_cache(cache_dir, cached_pdf)
    return cached_pdf


def _render_page(
    pdf_path: pathlib.Path,
    page_number: int,
    detail: Literal["low", "high"],
) -> bytes:
    pdftoppm = _find_command("pdftoppm", "pdftoppm.exe")
    dpi = "120" if detail == "low" else "180"

    with tempfile.TemporaryDirectory(prefix="mcp-slide-") as temp_dir:
        output_prefix = pathlib.Path(temp_dir) / "slide"
        result = subprocess.run(
            [
                pdftoppm,
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-singlefile",
                "-png",
                "-r",
                dpi,
                str(pdf_path),
                str(output_prefix),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            detail_text = (result.stderr or result.stdout or "unknown error").strip()
            raise ToolError(f"Could not render slide {page_number}: {detail_text}")

        output_path = output_prefix.with_suffix(".png")
        if not output_path.exists():
            raise ToolError(f"Renderer produced no image for slide {page_number}.")
        return output_path.read_bytes()


def register(mcp: FastMCP, cfg: "_CfgModule") -> None:

    @mcp.tool()
    def read_presentation(
        path: str,
        start_slide: int = 1,
        limit: int = DEFAULT_SLIDES_PER_CALL,
        detail: Literal["low", "high"] = "high",
    ) -> list[TextContent | ImageContent]:
        """Read a local PPT/PPTX as visible text and rendered slide images.

        Use this instead of read_file for PowerPoint files. The path must be inside
        a configured filesystem allowed_path. Each returned slide includes extracted
        text followed by a PNG of the complete rendered slide, allowing a vision-capable
        model to interpret photos, diagrams, charts, SmartArt, and spatial layout.

        Results are paged to control response size. If the metadata block contains a
        non-null next_slide, call this tool again with start_slide=next_slide and the
        same path/detail. Continue until next_slide is null to read the complete deck.
        Use detail="high" for small text and dense charts; use "low" to reduce payload.
        Static rendering does not preserve animation, video playback, or audio.
        """
        source = pathlib.Path(path).resolve()
        cfg.check_path(source)

        if not source.exists():
            raise ToolError(f"Presentation not found: {path}")
        if not source.is_file():
            raise ToolError(f"Path is not a file: {path}")
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ToolError("Only .ppt and .pptx presentations are supported.")

        size = source.stat().st_size
        if size > MAX_PRESENTATION_BYTES:
            raise ToolError(
                f"Presentation is too large ({size} bytes); maximum is "
                f"{MAX_PRESENTATION_BYTES} bytes."
            )

        pdf_path = _convert_to_cached_pdf(source)
        try:
            reader = PdfReader(str(pdf_path))
        except Exception as exc:
            raise ToolError(f"Could not open converted presentation: {exc}") from exc

        start_index, end_index, next_slide = _slide_window(
            len(reader.pages), start_slide, limit
        )
        metadata = {
            "filename": source.name,
            "slide_count": len(reader.pages),
            "returned_slides": [start_index + 1, end_index],
            "next_slide": next_slide,
            "detail": detail,
        }
        content: list[TextContent | ImageContent] = [
            TextContent(
                type="text",
                text=json.dumps(metadata, ensure_ascii=False),
            )
        ]

        for index in range(start_index, end_index):
            page_number = index + 1
            try:
                visible_text = (reader.pages[index].extract_text() or "").strip()
            except Exception as exc:
                logger.warning("Could not extract text from slide %d: %s", page_number, exc)
                visible_text = ""

            content.append(
                TextContent(
                    type="text",
                    text=(
                        f"--- Slide {page_number} of {len(reader.pages)} ---\n"
                        f"{visible_text or '[No extractable text; inspect the slide image.]'}"
                    ),
                )
            )
            image_bytes = _render_page(pdf_path, page_number, detail)
            content.append(
                ImageContent(
                    type="image",
                    mimeType="image/png",
                    data=base64.b64encode(image_bytes).decode("ascii"),
                )
            )

        logger.info(
            "read_presentation: %s slides %d-%d of %d",
            source,
            start_index + 1,
            end_index,
            len(reader.pages),
        )
        return content
