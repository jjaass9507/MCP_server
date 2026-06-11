"""Presentation generation tools using pptxgenjs (Node.js).

Requires Node.js and pptxgenjs installed in the project directory.
Run scripts/setup_presentation.ps1 once to install pptxgenjs.
"""

import json
import pathlib
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from mcp_server.utils.errors import ToolError

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule

_GENERATE_SCRIPT_NAME = pathlib.Path("scripts") / "generate_pptx.js"

QA_UNRELIABLE_FONTS = {"Georgia", "Trebuchet MS"}


def _find_node() -> str:
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        raise ToolError(
            "Node.js not found. Install from https://nodejs.org/ and ensure it is on PATH."
        )
    return node


def _find_script() -> pathlib.Path:
    script = pathlib.Path.cwd() / _GENERATE_SCRIPT_NAME
    if not script.exists():
        raise ToolError(
            f"generate_pptx.js not found at {script}. "
            "Ensure you are running the server from the project root directory."
        )
    return script


def register(mcp: FastMCP, cfg: "_CfgModule") -> None:

    @mcp.tool()
    def list_presentation_styles() -> str:
        """List available presentation presets, safe fonts, and design constraints.

        Call this before create_presentation to discover valid style options.
        Returns preset names, recommended fonts, and design rules that are enforced.
        """
        return (
            "=== Presentation Presets ===\n"
            "  corporate  — navy (#003366) accent, white background, Calibri font\n"
            "  modern     — red (#E84545) accent, light grey background, Arial font\n"
            "  dark       — cyan (#00B4D8) accent, dark navy background, Arial font\n"
            "  minimal    — dark grey (#222222) accent, white background, Calibri font\n"
            "  tech       — purple (#7B2FBE) accent, near-black background, Noto Sans font\n\n"
            "=== Safe Fonts (LibreOffice-reliable) ===\n"
            "  Arial, Calibri, Helvetica Neue, Noto Sans, Noto Sans TC,\n"
            "  Microsoft JhengHei, Microsoft YaHei\n\n"
            "=== Available Slide Layouts ===\n"
            "  title        — large centered title + subtitle (use for first slide only)\n"
            "  content      — title bar + bullet list (supports 2-space indent for sub-bullets)\n"
            "  two_column   — title bar + left/right columns with optional column titles\n"
            "  image_text   — title bar + image on left + text on right\n"
            "  section      — full-accent-color section divider with title + subtitle\n"
            "  blank        — no title bar, free body text only\n\n"
            "=== Content Density Guidelines (IMPORTANT) ===\n"
            "  Slides:   Aim for 8-15 slides for a complete presentation.\n"
            "            Use: 1 title + 1-2 sections + 5-10 content + 1 closing.\n"
            "  Bullets:  Each content slide should have 4-6 bullets.\n"
            "            Each bullet should be a complete, informative phrase (not just 1-2 words).\n"
            "            Use 2-space indent for sub-bullets: '  sub-point here'.\n"
            "  Two-col:  Each column should have 3-5 items.\n"
            "  Body:     For blank/image_text, write 3-6 sentences of detailed text.\n"
            "  Notes:    Add speaker notes to content slides (notes field) for extra context.\n\n"
            "=== slides_json Format ===\n"
            '  {\n'
            '    "title": "Document title",\n'
            '    "style": { "preset": "corporate", "title_font": "Microsoft JhengHei", "body_font": "Microsoft JhengHei" },\n'
            '    "slides": [\n'
            '      { "layout": "title", "title": "主標題", "subtitle": "副標題說明" },\n'
            '      { "layout": "section", "title": "第一章", "subtitle": "章節說明" },\n'
            '      { "layout": "content", "title": "投影片標題",\n'
            '        "bullets": ["要點一的完整說明", "要點二的完整說明", "  子要點（兩個空格縮排）", "要點三"],\n'
            '        "notes": "演講者備忘錄" },\n'
            '      { "layout": "two_column", "title": "比較標題",\n'
            '        "left_title": "優點", "left": ["優點一說明", "優點二說明", "優點三說明"],\n'
            '        "right_title": "缺點", "right": ["缺點一說明", "缺點二說明"] },\n'
            '      { "layout": "blank", "body": "結語或自由文字內容，可以寫多行。" }\n'
            "    ]\n"
            "  }"
        )

    @mcp.tool()
    def create_presentation(
        slides_json: str,
        output_path: str,
        style_preset: str = "",
        title_font: str = "",
        body_font: str = "",
    ) -> str:
        """Generate a PowerPoint (.pptx) file from structured slide data.

        Produces an OOXML .pptx file where all text is editable (not images).
        Call list_presentation_styles() first to discover presets, layouts, and the JSON format.

        CONTENT QUALITY REQUIREMENTS — follow these to avoid a sparse presentation:
        - Include 8-15 slides total (title + sections + content + closing).
        - Each content slide needs 4-6 bullets; each bullet must be a full informative phrase.
        - Two-column slides need 3-5 items per column.
        - Add speaker notes (notes field) to each content slide.
        - Do NOT use vague 1-2 word bullets like "Introduction" or "Summary".

        Args:
            slides_json:   JSON string containing 'title', 'style', and 'slides' array.
                           See list_presentation_styles() for the full format and examples.
            output_path:   Absolute path for the output .pptx file (must be in an allowed directory).
            style_preset:  Optional preset override: corporate|modern|dark|minimal|tech.
                           Ignored if slides_json already contains a style.preset field.
            title_font:    Optional title font override (e.g. 'Microsoft JhengHei').
            body_font:     Optional body font override.
        """
        out = pathlib.Path(output_path).resolve()
        cfg.check_path(out, write=True)

        try:
            payload = json.loads(slides_json)
        except json.JSONDecodeError as e:
            raise ToolError(f"slides_json is not valid JSON: {e}") from e

        if not isinstance(payload.get("slides"), list) or not payload["slides"]:
            raise ToolError("slides_json must contain a non-empty 'slides' array.")

        # Apply top-level style overrides if provided and not already set
        style = payload.setdefault("style", {})
        if style_preset and not style.get("preset"):
            style["preset"] = style_preset
        if title_font and not style.get("title_font"):
            style["title_font"] = title_font
        if body_font and not style.get("body_font"):
            style["body_font"] = body_font

        node = _find_node()
        script = _find_script()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(payload, tmp, ensure_ascii=False)
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                [node, str(script), tmp_path, str(out)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "unknown error").strip()
            raise ToolError(f"Presentation generation failed: {err}")

        stdout = result.stdout.strip()
        for line in stdout.splitlines():
            if line.startswith("OK:"):
                parts = line[3:].split(":")
                n_slides = parts[1] if len(parts) > 1 else "?"
                preset = parts[2] if len(parts) > 2 else style.get("preset", "default")
                # Verify the file actually exists and report its real size.
                # This makes it impossible for the agent to hallucinate success —
                # a genuine .pptx is always at least 10 KB.
                if not out.exists():
                    raise ToolError(
                        f"Generator reported success but file not found: {out}. "
                        "Check that the output directory is writable."
                    )
                size_kb = out.stat().st_size // 1024
                return (
                    f"Presentation saved: {out} "
                    f"({n_slides} slides, style: {preset}, size: {size_kb} KB). "
                    "File confirmed on disk. "
                    "Use verify_presentation() to render PNG previews for visual QA."
                )

        raise ToolError(
            f"Unexpected output from generator: {stdout or result.stderr or '(empty)'}"
        )

    @mcp.tool()
    def verify_presentation(pptx_path: str, qa_output_dir: str = "") -> str:
        """Render a .pptx file to PNG images for visual quality assurance.

        Requires LibreOffice to be installed and accessible on PATH (soffice or libreoffice).
        Returns the list of generated PNG file paths so you can inspect each slide visually.

        Args:
            pptx_path:     Absolute path to the .pptx file to render.
            qa_output_dir: Directory for PNG output. Defaults to the same directory as the .pptx.
        """
        pptx = pathlib.Path(pptx_path).resolve()
        cfg.check_path(pptx)
        if not pptx.exists():
            raise ToolError(f"File not found: {pptx_path}")
        if pptx.suffix.lower() != ".pptx":
            raise ToolError("verify_presentation only accepts .pptx files.")

        # Also check common Windows install path
        _common = r"C:\Program Files\LibreOffice\program\soffice.exe"
        soffice = (
            shutil.which("soffice")
            or shutil.which("libreoffice")
            or shutil.which("soffice.exe")
            or (_common if pathlib.Path(_common).exists() else None)
        )
        if not soffice:
            raise ToolError(
                "LibreOffice is not installed or not on PATH. "
                "verify_presentation is optional — you can open the .pptx directly in "
                "PowerPoint or WPS Office for visual review instead. "
                "To enable PNG rendering, install LibreOffice from https://www.libreoffice.org/ "
                r"and ensure soffice.exe is on PATH (default: C:\Program Files\LibreOffice\program)."
            )

        if qa_output_dir:
            out_dir = pathlib.Path(qa_output_dir).resolve()
            cfg.check_path(out_dir, write=True)
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = pptx.parent

        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "png", "--outdir", str(out_dir), str(pptx)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "unknown error").strip()
            raise ToolError(f"LibreOffice conversion failed: {err}")

        stem = pptx.stem
        pngs = sorted(out_dir.glob(f"{stem}*.png"))
        if not pngs:
            # LibreOffice may name them differently
            pngs = sorted(out_dir.glob("*.png"))

        if not pngs:
            return (
                "LibreOffice ran successfully but no PNG files were found in "
                f"{out_dir}. Check LibreOffice output: {result.stdout}"
            )

        paths_str = "\n".join(str(p) for p in pngs)
        return (
            f"Rendered {len(pngs)} PNG(s) to {out_dir}:\n{paths_str}\n\n"
            "Note: if title/body fonts are Georgia or Trebuchet MS, "
            "LibreOffice may substitute them — visual width may differ from Windows."
        )
