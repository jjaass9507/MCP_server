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


# ── Content quality (adapted from Presenton's proven density rules) ────────────
# Presenton (8.1k★) makes sparse content structurally impossible via per-field
# min/max length schemas + a ~40-word-per-slide budget. We can't run a multi-stage
# LLM pipeline against an external Gemma agent, so we instead (a) hand the model a
# filled-in outline scaffold and (b) audit the returned slides and report thinness.

# Minimum substance thresholds per content-bearing slide.
_MIN_BULLETS = 3          # content slides should have at least this many bullets
_MIN_BULLET_WORDS = 4     # each bullet should be a phrase, not a 1-2 word label
_MIN_BODY_WORDS = 15      # free-text body slides should have real sentences


def _word_count(text: str) -> int:
    # Works for both space-delimited and CJK text (count CJK chars as words).
    import re
    cjk = len(re.findall(r"[一-鿿぀-ヿ]", text))
    latin = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + latin


def _audit_slides(slides: list) -> list[str]:
    """Return a list of human-readable warnings about thin/sparse slides.

    Non-blocking: the presentation still generates. The warnings are appended to
    the success message so the agent can choose to enrich and regenerate.
    """
    warnings: list[str] = []
    content_slides = 0

    for i, s in enumerate(slides, 1):
        layout = s.get("layout", "blank")

        if layout in ("content",):
            content_slides += 1
            bullets = s.get("bullets") or []
            body = s.get("body") or ""
            if not bullets and not body:
                warnings.append(f"slide {i} ({layout}): empty — add 4-6 bullets.")
                continue
            if bullets:
                if len(bullets) < _MIN_BULLETS:
                    warnings.append(
                        f"slide {i}: only {len(bullets)} bullet(s) — aim for 4-6."
                    )
                thin = [b for b in bullets if _word_count(b.strip()) < _MIN_BULLET_WORDS]
                if thin:
                    warnings.append(
                        f"slide {i}: {len(thin)} vague bullet(s) "
                        f"(e.g. \"{thin[0].strip()[:30]}\") — expand to a full phrase."
                    )

        elif layout == "two_column":
            content_slides += 1
            for side in ("left", "right"):
                items = s.get(side) or []
                if 0 < len(items) < _MIN_BULLETS:
                    warnings.append(
                        f"slide {i} {side} column: only {len(items)} item(s) — aim for 3-5."
                    )

        elif layout == "stats":
            stats = s.get("stats") or []
            if not stats:
                warnings.append(f"slide {i} (stats): no stat cards defined.")

        elif layout in ("blank", "image_text"):
            body = s.get("body") or ""
            if body and _word_count(body) < _MIN_BODY_WORDS:
                warnings.append(
                    f"slide {i} ({layout}): body too short — write 3-6 full sentences."
                )

    if len(slides) < 6:
        warnings.append(
            f"only {len(slides)} slides total — a complete deck is usually 8-15."
        )
    if content_slides == 0 and len(slides) > 2:
        warnings.append("no content/two_column slides — the deck may be all dividers.")

    return warnings


# ── Deck-type outline frameworks (the 'outline-first' stage, done deterministically) ──
# Each entry: (layout, title_template, content_guidance). The guidance tells the
# model exactly WHAT to write on that slide — this is the cure for sparse content.

_DECK_FRAMEWORKS: dict[str, list[tuple[str, str, str]]] = {
    "general": [
        ("title",   "{topic}", "Cover slide. Subtitle = one-line value proposition."),
        ("section", "背景與目標", "Why this topic matters now; the goal of this deck."),
        ("content", "現況概述", "4-6 bullets describing the current situation/context."),
        ("content", "核心重點", "4-6 bullets on the main ideas, each a full sentence."),
        ("two_column", "優勢 vs 挑戰", "Left: 3-5 strengths. Right: 3-5 challenges."),
        ("stats",   "關鍵數據", "3-4 KPI cards (value + label + 1-line description)."),
        ("content", "建議做法", "4-6 actionable recommendations."),
        ("quote",   "", "A memorable quote or guiding principle + attribution."),
        ("blank",   "結語", "3-6 sentences summarizing takeaways and next steps."),
    ],
    "product_pitch": [
        ("title",   "{topic}", "Product name + tagline."),
        ("content", "問題", "4-6 bullets on the customer pain points."),
        ("content", "解決方案", "4-6 bullets on how the product solves them."),
        ("image_text", "產品展示", "Body: describe the product experience in 3-6 sentences."),
        ("stats",   "市場機會", "3-4 cards: market size, growth, target users, etc."),
        ("two_column", "我們 vs 競品", "Left: our advantages. Right: competitor limits."),
        ("content", "商業模式", "4-6 bullets: pricing, channels, revenue."),
        ("quote",   "", "Customer testimonial + attribution."),
        ("blank",   "行動呼籲", "Clear ask + contact/next step in 3-6 sentences."),
    ],
    "technical": [
        ("title",   "{topic}", "System/project name + one-line scope."),
        ("section", "背景", "Problem statement and technical context."),
        ("content", "需求與限制", "4-6 bullets: functional + non-functional requirements."),
        ("content", "架構設計", "4-6 bullets describing components and data flow."),
        ("image_text", "架構圖", "Body: explain the diagram in 3-6 sentences. image_path optional."),
        ("two_column", "方案比較", "Left: chosen approach. Right: alternatives & trade-offs."),
        ("stats",   "效能指標", "3-4 cards: latency, throughput, scale, reliability."),
        ("content", "風險與緩解", "4-6 bullets: risks and mitigation."),
        ("blank",   "結論與後續", "3-6 sentences: decision and roadmap."),
    ],
    "project_status": [
        ("title",   "{topic} 專案進度", "Project name + reporting period."),
        ("stats",   "整體進度", "3-4 cards: % complete, on-time, budget, open issues."),
        ("content", "已完成項目", "4-6 bullets of completed work this period."),
        ("content", "進行中項目", "4-6 bullets of in-progress work + owners."),
        ("two_column", "風險 vs 對策", "Left: 3-5 risks/blockers. Right: 3-5 mitigations."),
        ("content", "下階段計畫", "4-6 bullets: next milestones and dates."),
        ("blank",   "需要的支援", "3-6 sentences: decisions/resources needed."),
    ],
    "training": [
        ("title",   "{topic}", "Course/module title + audience."),
        ("section", "學習目標", "What learners will be able to do after this."),
        ("content", "核心概念", "4-6 bullets explaining the key concepts."),
        ("content", "步驟說明", "4-6 bullets: step-by-step procedure (use 2-space sub-bullets)."),
        ("image_text", "範例", "Body: a worked example in 3-6 sentences."),
        ("content", "常見錯誤", "4-6 bullets: pitfalls and how to avoid them."),
        ("two_column", "Do vs Don't", "Left: best practices. Right: anti-patterns."),
        ("blank",   "重點回顧", "3-6 sentences recapping the key takeaways."),
    ],
}


def _build_outline(topic: str, n_slides: int, deck_type: str) -> list[dict]:
    """Build a per-slide scaffold sized to roughly n_slides for the given deck type.

    Core slides come from the framework; if more slides are requested than the
    framework defines, extra 'content' slides are inserted before the closing slide.
    """
    framework = _DECK_FRAMEWORKS.get(deck_type, _DECK_FRAMEWORKS["general"])
    core = list(framework)

    # Scale: if caller wants more slides, pad with content slides before the last.
    if n_slides > len(core):
        extra = n_slides - len(core)
        pad = [("content", "補充重點 {k}", "4-6 bullets expanding on a sub-topic.")
               for _ in range(extra)]
        core = core[:-1] + pad + core[-1:]
    elif n_slides < len(core) and n_slides >= 3:
        # Keep first (title), last (closing), and trim from the middle.
        keep_mid = n_slides - 2
        core = [core[0]] + core[1:-1][:keep_mid] + [core[-1]]

    outline = []
    k = 1
    for layout, title_tpl, guidance in core:
        title = title_tpl.replace("{topic}", topic).replace("{k}", str(k))
        outline.append({"layout": layout, "title": title, "_guidance": guidance})
        if "{k}" in title_tpl:
            k += 1
    return outline


def register(mcp: FastMCP, cfg: "_CfgModule") -> None:

    @mcp.tool()
    def list_presentation_styles() -> str:
        """List available presentation presets, safe fonts, and design constraints.

        Call this before create_presentation to discover valid style options.
        Returns preset names, recommended fonts, and design rules that are enforced.
        """
        return (
            "=== Presentation Presets (8 total) ===\n"
            "  corporate  — navy accent, white bg, Calibri  [formal / business]\n"
            "  modern     — red accent, light grey bg, Arial  [energetic]\n"
            "  dark       — cyan accent, dark navy bg, Arial  [tech / dark mode]\n"
            "  minimal    — dark grey accent, white bg, Calibri  [clean / simple]\n"
            "  tech       — purple accent, near-black bg, Noto Sans  [developer]\n"
            "  aurora     — violet accent, near-black bg, Arial  [open-slide inspired, premium dark]\n"
            "  bright_sans — blue accent, white bg, Calibri  [open-slide inspired, Google style]\n"
            "  warm       — orange accent, warm-white bg, Arial  [open-slide inspired, friendly]\n\n"
            "=== Safe Fonts (LibreOffice-reliable) ===\n"
            "  Arial, Calibri, Noto Sans, Noto Sans TC, Microsoft JhengHei, Microsoft YaHei\n\n"
            "=== Slide Layouts (8 total) ===\n"
            "  title        — hero title + subtitle (use for first slide only)\n"
            "  section      — full-accent section divider with title + subtitle\n"
            "  content      — title bar + bullet list; supports eyebrow label and speaker notes\n"
            "  two_column   — title bar + left/right columns with optional column titles\n"
            "  stats        — title bar + up to 4 KPI cards (value + label + description)\n"
            "  quote        — featured pull-quote with large quotation mark + attribution\n"
            "  image_text   — title bar + image left + text right\n"
            "  blank        — optional title bar + free body text\n\n"
            "=== Extra Fields (any layout) ===\n"
            "  eyebrow  — small all-caps accent label above body (e.g. 'KEY INSIGHT', '第二章')\n"
            "  section  — section name shown in footer (requires style.show_footer: true)\n"
            "  notes    — speaker notes text (not visible on slide)\n"
            "  icon     — Lucide icon name (see icon list below); rendered in accent colour\n\n"
            "=== Icons (Lucide SVG bundle, ISC license) ===\n"
            "  Where icons appear:\n"
            "    section:    large decorative icon above the accent band (use slide.icon)\n"
            "    content:    small icon beside eyebrow label, or above content area (use slide.icon)\n"
            "    two_column: icon beside column title (use left_icon / right_icon)\n"
            "    stats:      icon above KPI value in each card (use stats[].icon)\n"
            "  Available icon names (63 total):\n"
            "    Navigation:  arrow-right arrow-left arrow-up arrow-down\n"
            "                 chevron-right chevron-left chevron-up chevron-down\n"
            "    Controls:    check x plus minus search\n"
            "    Data/Infra:  database cloud server network cpu hard-drive\n"
            "                 download upload refresh-cw git-branch\n"
            "    Charts:      bar-chart-2 trending-up trending-down chart-pie activity\n"
            "    People:      users user user-check\n"
            "    Security:    shield shield-check lock key\n"
            "    Ideas/Work:  rocket lightbulb settings wrench target\n"
            "    Files:       file file-text folder clipboard\n"
            "    Time:        calendar clock\n"
            "    Comms:       mail phone message-square bell\n"
            "    Web/Code:    globe code terminal\n"
            "    Status:      triangle-alert info circle-help zap\n"
            "    Business:    briefcase building dollar-sign award star thumbs-up\n\n"
            "=== Footer ===\n"
            "  Add  \"show_footer\": true  to the style object to enable footer on all slides.\n"
            "  Footer shows: [section name left]  [page X / N right]\n\n"
            "=== Content Density Guidelines (IMPORTANT) ===\n"
            "  Slides:   8-15 slides total (1 title + 1-2 sections + 5-10 content + 1 closing).\n"
            "  Bullets:  4-6 bullets per content slide; each must be a full informative phrase.\n"
            "            Use 2-space indent for sub-bullets: '  sub detail here'.\n"
            "  Stats:    2-4 cards per stats slide; value should be a number/% with unit.\n"
            "  Quote:    1-3 sentences; attribution = 'Name, Title'.\n"
            "  Body:     3-6 sentences for blank/image_text slides.\n"
            "  Notes:    Add speaker notes to every content slide.\n\n"
            "=== slides_json Format ===\n"
            '{\n'
            '  "title": "文件標題",\n'
            '  "style": { "preset": "aurora", "title_font": "Microsoft JhengHei",\n'
            '             "body_font": "Microsoft JhengHei", "show_footer": true },\n'
            '  "slides": [\n'
            '    { "layout": "title",   "title": "主標題", "subtitle": "副標題" },\n'
            '    { "layout": "section", "title": "第一章", "subtitle": "章節說明", "icon": "server", "section": "第一章" },\n'
            '    { "layout": "content", "title": "要點標題", "eyebrow": "核心功能", "icon": "settings", "section": "第一章",\n'
            '      "bullets": ["完整說明的要點一", "完整說明的要點二", "  子要點（兩格縮排）", "要點三"],\n'
            '      "notes": "演講者備忘" },\n'
            '    { "layout": "stats",   "title": "KPI 數據", "section": "數據",\n'
            '      "stats": [{"value":"99%","label":"可用率","icon":"shield-check","desc":"近30天"},\n'
            '                {"value":"<1s","label":"回應時間","icon":"zap","desc":"平均"},\n'
            '                {"value":"20", "label":"工具數量","icon":"settings","desc":"四大類別"}] },\n'
            '    { "layout": "two_column", "title": "比較", "section": "分析",\n'
            '      "left_title": "優點", "left_icon": "check", "left": ["優點一完整說明","優點二","優點三"],\n'
            '      "right_title": "缺點", "right_icon": "x",    "right": ["缺點一完整說明","缺點二"] },\n'
            '    { "layout": "quote", "quote": "引言內容...", "attribution": "姓名，職稱",\n'
            '      "section": "結語" },\n'
            '    { "layout": "blank",   "title": "結語", "body": "完整結論文字。", "section": "結語" }\n'
            '  ]\n'
            '}'
        )

    @mcp.tool()
    def plan_presentation_outline(
        topic: str,
        slide_count: int = 9,
        deck_type: str = "general",
    ) -> str:
        """STEP 1 of presentation creation — get a per-slide content scaffold to fill in.

        This is the recommended FIRST step before create_presentation. It returns a
        slide-by-slide outline telling you exactly WHAT content to write on each slide,
        so the final deck is substantive instead of sparse. This mirrors the proven
        two-stage approach used by popular AI slide generators (outline first, then
        fill content).

        Workflow:
          1. Call plan_presentation_outline(topic, slide_count, deck_type)  ← you are here
          2. For each slide in the returned outline, WRITE REAL CONTENT following the
             '_guidance' note (4-6 full-sentence bullets, real numbers for stats, etc.)
          3. Call create_presentation(slides_json=...) with the filled-in slides.

        Args:
            topic:       The presentation subject (e.g. '2026 Q1 產品上線計畫').
            slide_count: Desired number of slides (default 9; typical 8-15).
            deck_type:   One of: general | product_pitch | technical | project_status | training.
                         Picks a slide sequence tailored to that purpose.
        """
        deck_type = deck_type if deck_type in _DECK_FRAMEWORKS else "general"
        slide_count = max(3, min(slide_count, 20))
        outline = _build_outline(topic, slide_count, deck_type)

        lines = [
            f"OUTLINE for \"{topic}\" ({deck_type}, {len(outline)} slides).",
            "STEP 2: Fill each slide below with REAL content per its guidance,",
            "then pass the completed slides to create_presentation().",
            "",
            "CONTENT RULES (from proven AI-slide generators):",
            "  • ~40 words of substance per content slide (concise but complete).",
            "  • Each bullet = one full sentence (8-20 words); never 1-2 word labels.",
            "  • For lists/visuals: 5 or fewer items — synthesize detail INTO items,",
            "    don't pad with filler.",
            "  • Use real, specific facts/numbers; do not invent data not given to you.",
            "  • Add speaker notes to every content slide.",
            "",
            "HOW TO WRITE STRONG SLIDES (assertion-evidence method):",
            "  • Make each slide TITLE a complete claim, not a topic label.",
            "    Bad:  \"系統架構\"   Good:  \"三層架構讓查詢延遲降到 200ms 以下\"",
            "  • Body bullets are the EVIDENCE for that claim (data, examples, reasons).",
            "  • Lead with the conclusion first (pyramid principle), then support it.",
            "  • Every bullet must pass the \"so what?\" test — if it states the obvious,",
            "    cut it or replace with a specific consequence/number.",
            "  • Prefer concrete specifics over generic phrasing:",
            "    Bad:  \"提升效能\"   Good:  \"批次寫入將吞吐量從 1k 提升到 8k TPS\"",
            "  • Use parallel grammatical structure across bullets in the same slide.",
            "  • One idea per slide — if a slide needs 3 ideas, split it into 3 slides.",
            "",
            "Per-slide scaffold:",
        ]
        for i, s in enumerate(outline, 1):
            title = s["title"] or "(no title — see guidance)"
            lines.append(f"  {i}. [{s['layout']}] {title}")
            lines.append(f"       → {s['_guidance']}")

        lines += [
            "",
            "Recommended style: pick a preset from list_presentation_styles()",
            "and set \"show_footer\": true for page numbers.",
            "",
            "ICON HINTS (optional but recommended):",
            "  • section slides: add \"icon\" field (e.g. \"server\", \"database\", \"users\").",
            "  • stats cards: add \"icon\" to each card object (e.g. \"trending-up\", \"zap\", \"shield-check\").",
            "  • two_column: add \"left_icon\" / \"right_icon\" to column headings (e.g. \"check\" / \"x\").",
            "  • content: add \"icon\" with eyebrow to brand the slide theme.",
            "  Icon names: check x arrow-right database cloud server network cpu trending-up",
            "              trending-down shield shield-check zap rocket lightbulb users target",
            "              bar-chart-2 briefcase calendar clock settings wrench star thumbs-up",
        ]
        return "\n".join(lines)

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

        TIP: Call plan_presentation_outline() FIRST to get a per-slide content
        scaffold — it makes the deck far less sparse.

        CONTENT QUALITY REQUIREMENTS — follow these to avoid a sparse presentation:
        - Include 8-15 slides total (title + sections + content + closing).
        - Each content slide needs 4-6 bullets; each bullet must be a full sentence.
        - Make each slide TITLE a complete claim, not a label
          (Bad: "效能"  Good: "批次寫入將吞吐量提升 8 倍").
        - Bullets are the EVIDENCE for the title's claim; use specific numbers/examples.
        - Every bullet must pass the "so what?" test — cut anything obvious.
        - Two-column slides need 3-5 items per column.
        - Add speaker notes (notes field) to each content slide.
        - Do NOT use vague 1-2 word bullets like "Introduction" or "Summary".
        - Use eyebrow field for small accent labels (e.g. "KEY INSIGHT", "章節名").
        - Use stats layout for KPI/number slides (up to 4 cards).
        - Use quote layout for featured quotes or testimonials.
        - Enable footer with show_footer=true in style for professional page numbering.
        - Add icons to enhance visual clarity:
            section slides:  "icon": "server"  (large decorative icon above title)
            stats cards:     "stats": [{"value":"99%","label":"Uptime","icon":"shield-check"}]
            two_column:      "left_icon": "check", "right_icon": "x"
            content slides:  "icon": "rocket"  (icon beside eyebrow or above content)
          Available names: check x arrow-right database cloud server network cpu trending-up
            trending-down shield shield-check zap rocket lightbulb users target bar-chart-2
            briefcase calendar settings wrench star thumbs-up (63 total — see list_presentation_styles).

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

        # Non-blocking content audit — flags sparse slides but still generates.
        content_warnings = _audit_slides(payload["slides"])

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
                msg = (
                    f"Presentation saved: {out} "
                    f"({n_slides} slides, style: {preset}, size: {size_kb} KB). "
                    "File confirmed on disk. "
                    "Use verify_presentation() to render PNG previews for visual QA."
                )
                if content_warnings:
                    msg += (
                        "\n\nCONTENT QUALITY NOTES (the deck generated fine, but these "
                        "slides look thin — consider enriching and regenerating):\n  - "
                        + "\n  - ".join(content_warnings)
                    )
                return msg

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
