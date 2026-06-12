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
from mcp_server.utils.logging import get_logger

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule

logger = get_logger("presentation")

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


# ── Deck-type outline frameworks ──────────────────────────────────────────────
# Each entry: (layout, title_template, content_guidance, priority)
# priority 1 = essential (never trimmed), 2 = important, 3 = optional

_DECK_FRAMEWORKS: dict[str, list[tuple[str, str, str, int]]] = {
    "general": [
        ("title",       "{topic}",      "Cover slide. Subtitle = one-line value proposition.", 1),
        ("agenda",      "今日議程",      "List 4-6 main sections/topics this deck covers.", 3),
        ("section",     "背景與目標",    "Why this topic matters now; the goal of this deck.", 3),
        ("content",     "現況概述",      "4-6 bullets describing the current situation/context.", 1),
        ("content",     "核心重點",      "4-6 bullets on the main ideas, each a full sentence.", 1),
        ("two_column",  "優勢 vs 挑戰", "Left: 3-5 strengths. Right: 3-5 challenges.", 2),
        ("stats",       "關鍵數據",      "3-4 KPI cards (value + label + 1-line description).", 2),
        ("content",     "建議做法",      "4-6 actionable recommendations.", 2),
        ("big_message", "核心結論",      "One powerful takeaway sentence (under 15 chars).", 2),
        ("blank",       "結語",          "3-6 sentences summarizing takeaways and next steps.", 1),
    ],
    "product_pitch": [
        ("title",       "{topic}",       "Product name + tagline.", 1),
        ("agenda",      "今日議程",       "List 4-6 pitch sections (Problem, Solution, Market, etc.).", 3),
        ("content",     "問題",           "4-6 bullets on the customer pain points.", 1),
        ("content",     "解決方案",       "4-6 bullets on how the product solves them.", 1),
        ("image_text",  "產品展示",       "Body: describe the product experience in 3-6 sentences.", 2),
        ("stats",       "市場機會",       "3-4 cards: market size, growth, target users, etc.", 2),
        ("two_column",  "我們 vs 競品",  "Left: our advantages. Right: competitor limits.", 2),
        ("content",     "商業模式",       "4-6 bullets: pricing, channels, revenue.", 2),
        ("timeline",    "產品藍圖",       "3-5 roadmap milestones with target dates.", 2),
        ("big_message", "核心訴求",       "One sentence capturing why investors/customers should care.", 1),
        ("blank",       "行動呼籲",       "Clear ask + contact/next step in 3-6 sentences.", 1),
    ],
    "technical": [
        ("title",       "{topic}",       "System/project name + one-line scope.", 1),
        ("section",     "背景",           "Problem statement and technical context.", 3),
        ("content",     "需求與限制",     "4-6 bullets: functional + non-functional requirements.", 1),
        ("content",     "架構設計",       "4-6 bullets describing components and data flow.", 1),
        ("image_text",  "架構圖",         "Body: explain the diagram in 3-6 sentences. image_path optional.", 2),
        ("process",     "部署流程",       "3-5 steps of the deployment/release process.", 2),
        ("two_column",  "方案比較",       "Left: chosen approach. Right: alternatives & trade-offs.", 2),
        ("stats",       "效能指標",       "3-4 cards: latency, throughput, scale, reliability.", 2),
        ("content",     "風險與緩解",     "4-6 bullets: risks and mitigation.", 2),
        ("blank",       "結論與後續",     "3-6 sentences: decision and roadmap.", 1),
    ],
    "project_status": [
        ("title",       "{topic} 專案進度", "Project name + reporting period.", 1),
        ("stats",       "整體進度",       "3-4 cards: % complete, on-time, budget, open issues.", 1),
        ("content",     "已完成項目",     "4-6 bullets of completed work this period.", 1),
        ("content",     "進行中項目",     "4-6 bullets of in-progress work + owners.", 1),
        ("timeline",    "專案時程",       "3-5 key milestones: completed, in-progress, upcoming.", 2),
        ("two_column",  "風險 vs 對策",  "Left: 3-5 risks/blockers. Right: 3-5 mitigations.", 2),
        ("content",     "下階段計畫",     "4-6 bullets: next milestones and dates.", 2),
        ("big_message", "本期重點",       "One sentence capturing the most important status update.", 2),
        ("blank",       "需要的支援",     "3-6 sentences: decisions/resources needed.", 1),
    ],
    "training": [
        ("title",       "{topic}",       "Course/module title + audience.", 1),
        ("agenda",      "今日議程",       "List 4-6 main topics covered in this training.", 3),
        ("section",     "學習目標",       "What learners will be able to do after this.", 3),
        ("content",     "核心概念",       "4-6 bullets explaining the key concepts.", 1),
        ("content",     "步驟說明",       "4-6 bullets: step-by-step procedure (use 2-space sub-bullets).", 1),
        ("image_text",  "範例示範",       "Body: a worked example in 3-6 sentences.", 2),
        ("content",     "常見錯誤",       "4-6 bullets: pitfalls and how to avoid them.", 2),
        ("two_column",  "Do vs Don't",   "Left: best practices. Right: anti-patterns.", 3),
        ("big_message", "一句話帶走",     "One key takeaway sentence (under 15 chars).", 2),
        ("blank",       "重點回顧",       "3-6 sentences recapping the key takeaways.", 1),
    ],
}

# Meaningful expansion slides per deck type — used when n_slides > framework length.
# Ordered by usefulness; used in sequence before falling back to generic.
_EXPANSION_SLIDES: dict[str, list[tuple[str, str, str, int]]] = {
    "training": [
        ("content",  "實作練習",    "4-6 bullets: hands-on exercise steps and expected outcome.", 2),
        ("content",  "進階主題",    "4-6 bullets: deeper material for fast learners.", 2),
        ("stats",    "學習成效指標", "2-4 cards: how mastery is measured (completion rate, test score, etc.).", 3),
    ],
    "technical": [
        ("content",  "安全性考量",   "4-6 bullets: security threats, mitigations, compliance requirements.", 2),
        ("content",  "容量與擴展性", "4-6 bullets: expected load, scaling approach, known limits.", 2),
        ("process",  "維運流程",     "3-5 steps of the monitoring/incident-response process.", 2),
    ],
    "product_pitch": [
        ("content",  "客戶案例",    "4-6 bullets: real customer story with before/after metrics.", 1),
        ("stats",    "市場驗證",    "2-4 cards: pilot results, NPS, retention, revenue.", 1),
        ("content",  "團隊介紹",    "4-6 bullets: key team members and their relevant experience.", 2),
    ],
    "project_status": [
        ("content",  "預算摘要",    "4-6 bullets: budget status, burn rate, forecast vs actual.", 2),
        ("content",  "品質指標",    "4-6 bullets: defect rate, test coverage, SLA compliance.", 2),
        ("stats",    "本期成果",    "2-4 cards: key metrics achieved this reporting period.", 2),
    ],
    "general": [
        ("content",  "深入分析",      "4-6 bullets: detailed evidence supporting the main argument.", 2),
        ("two_column", "替代方案比較", "Left: this approach pros. Right: alternative cons.", 2),
        ("stats",    "補充數據",      "2-4 cards: additional metrics or supporting evidence.", 2),
    ],
}

# Recommended slide counts per deck type (for user guidance)
_DECK_SLIDE_GUIDANCE: dict[str, str] = {
    "general":        "8-12 slides",
    "product_pitch":  "8-11 slides",
    "technical":      "9-12 slides",
    "project_status": "7-9 slides",
    "training":       "8-10 slides",
}


def _build_outline(topic: str, n_slides: int, deck_type: str) -> tuple[list[dict], str]:
    """Return (outline, note) where note describes any compression or expansion applied."""
    framework = _DECK_FRAMEWORKS.get(deck_type, _DECK_FRAMEWORKS["general"])
    full_count = len(framework)
    note = ""

    if n_slides >= full_count:
        core: list[tuple[str, str, str, int]] = list(framework)
        if n_slides > full_count:
            extra_needed = n_slides - full_count
            expansion = _EXPANSION_SLIDES.get(deck_type, _EXPANSION_SLIDES["general"])
            pad: list[tuple[str, str, str, int]] = list(expansion[:extra_needed])
            k = 1
            while len(pad) < extra_needed:
                pad.append(("content", f"補充重點 {k}", "4-6 bullets expanding on a sub-topic from earlier in the deck.", 3))
                k += 1
            core = core[:-1] + pad + [core[-1]]
    else:
        # TRIM: remove by priority (3 first, then 2); priority 1 is never removed.
        p1_count = sum(1 for e in framework if e[3] == 1)

        if n_slides < p1_count:
            note = (
                f"NOTE: {n_slides} slides is below the minimum for {deck_type} "
                f"(priority-1 essential slides = {p1_count}). "
                f"Returning {p1_count} essential slides instead. "
                f"Consider slide_count={full_count} for the full {_DECK_SLIDE_GUIDANCE.get(deck_type, 'recommended')} structure."
            )
            n_slides = p1_count
        else:
            note = (
                f"NOTE: {n_slides} slides is compact for a {deck_type} deck "
                f"(full structure = {full_count} slides). "
                f"Essential slides kept; omitted topics are folded into neighboring slides' guidance. "
                f"Consider slide_count={full_count} for full coverage."
            )

        to_remove = full_count - n_slides
        removed: set[int] = set()
        dropped_by_prev: dict[int, list[tuple[str, str]]] = {}

        for target_p in [3, 2]:
            if to_remove <= 0:
                break
            candidates = [i for i, e in enumerate(framework) if e[3] == target_p]
            for idx in reversed(candidates):
                if to_remove <= 0:
                    break
                removed.add(idx)
                to_remove -= 1
                # Find the nearest preceding non-removed slide to absorb this one's topic.
                prev = idx - 1
                while prev >= 0 and prev in removed:
                    prev -= 1
                if prev >= 0:
                    dropped_by_prev.setdefault(prev, []).append((framework[idx][1], framework[idx][2]))

        core = []
        for i, entry in enumerate(framework):
            if i in removed:
                continue
            layout, title, guidance, priority = entry
            if i in dropped_by_prev:
                hints = "; ".join(
                    f"Also mention briefly: {t} — {g[:60]}"
                    for t, g in dropped_by_prev[i]
                )
                guidance = f"{guidance} ({hints})"
            core.append((layout, title, guidance, priority))

    outline: list[dict] = []
    k = 1
    for entry in core:
        layout, title_tpl, guidance = entry[0], entry[1], entry[2]
        title = title_tpl.replace("{topic}", topic).replace("{k}", str(k))
        outline.append({"layout": layout, "title": title, "_guidance": guidance})
        if "{k}" in title_tpl:
            k += 1
    return outline, note


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
            "=== CUSTOM STYLE — honor the user's style description ===\n"
            "  If the user describes a style (colors, fonts, mood — in YAML, prose, or a spec),\n"
            "  DO NOT just pick a preset and hope. Translate their description into explicit\n"
            "  style fields. A preset is only the BASE; every field below can be overridden:\n"
            '    "style": {\n'
            '      "preset":         "corporate",      // closest base preset\n'
            '      "accent_color":   "#0F6CBD",        // main brand/accent colour (hex)\n'
            '      "accent_text":    "#FFFFFF",        // text colour on accent background\n'
            '      "body_bg":        "#FFFFFF",        // slide background\n'
            '      "body_text":      "#1A1A1A",        // main text colour\n'
            '      "subtitle_color": "#555555",        // secondary text colour\n'
            '      "card_bg":        "#EEF2F7",        // stats/process card background\n'
            '      "title_font":     "Microsoft JhengHei",\n'
            '      "body_font":      "Microsoft JhengHei",\n'
            '      "show_footer":    true\n'
            '    }\n'
            "  Mapping guide: user says '深藍色企業風' → preset corporate + accent_color from\n"
            "  their palette; '橘色活潑' → preset warm + their orange as accent_color;\n"
            "  dark theme → preset dark/aurora + their colours. Always carry over EVERY\n"
            "  colour and font the user specified — never silently drop one.\n\n"
            "=== Slide Layouts (12 total) ===\n"
            "  title        — hero title + subtitle (use for first slide only)\n"
            "  agenda       — table-of-contents with numbered items; optional active_item highlight\n"
            "  section      — left-stripe section divider with title + subtitle + optional icon\n"
            "  content      — title bar + bullet list; supports eyebrow label and speaker notes\n"
            "  two_column   — title bar + left/right columns with optional column titles\n"
            "  stats        — title bar + up to 4 KPI cards (value + label + description)\n"
            "  process      — title bar + 3-5 step cards connected by arrows (SOP / flow)\n"
            "  timeline     — title bar + horizontal milestone timeline (roadmap / schedule)\n"
            "  big_message  — full-page bold statement (accent bg by default) + supporting text\n"
            "  quote        — featured pull-quote with large quotation mark + attribution\n"
            "  image_text   — title bar + image left + text right\n"
            "  blank        — optional title bar + free body text\n\n"
            "=== New Layout Fields ===\n"
            "  agenda:      items:[\"章節一\", \"章節二\", ...], active_item:2 (optional, highlights that item)\n"
            "  big_message: message:\"核心訊息（15字內）\", supporting:\"說明\", icon:\"optional\", bg:\"subtle\" (optional, uses body bg)\n"
            "  timeline:    events:[{date:\"Q1\", label:\"里程碑\", desc:\"說明\", icon:\"optional\"}, ...] (3-6 events)\n"
            "  process:     steps:[{label:\"步驟名\", desc:\"說明\", icon:\"optional\"}, ...] (3-5 steps)\n\n"
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
        outline, outline_note = _build_outline(topic, slide_count, deck_type)

        lines = [
            f"OUTLINE for \"{topic}\" ({deck_type}, {len(outline)} slides).",
        ]
        if outline_note:
            lines.append(outline_note)
        lines += [
            "STEP 2: Fill each slide below with REAL content per its guidance,",
            "then pass the completed slides to create_presentation().",
            "",
            f"RECOMMENDED SLIDE COUNT for {deck_type}: {_DECK_SLIDE_GUIDANCE.get(deck_type, '8-12')}",
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
            "STYLE: if the user described a style (colors / fonts / mood / a YAML spec),",
            "translate EVERY value into explicit style fields — pick the closest preset as",
            "base, then override accent_color, body_bg, title_font, etc. with the user's",
            "exact values (see CUSTOM STYLE in list_presentation_styles()).",
            "Otherwise pick a preset and set \"show_footer\": true for page numbers.",
            "",
            "ICON HINTS (optional but recommended):",
            "  • section slides: add \"icon\" field (e.g. \"server\", \"database\", \"users\").",
            "  • big_message: add \"icon\" above the main message.",
            "  • agenda: no icons (numbered list handles hierarchy).",
            "  • timeline events: add \"icon\" per event (e.g. \"rocket\" for launch).",
            "  • process steps: add \"icon\" per step (e.g. \"settings\" for config step).",
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

        *** STYLE MUST GO INSIDE slides_json["style"] ***
        If the user gave you a style spec (YAML, colour codes, fonts), put EVERY value
        into the "style" object inside slides_json. The separate style_preset / title_font /
        body_font parameters can ONLY pass a preset name — they cannot carry colours.
        Anything not inside slides_json["style"] will be silently discarded.

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

        STYLE RULE — CRITICAL:
            If the user described a visual style (YAML spec, colour codes, fonts, mood),
            you MUST encode it inside slides_json["style"], NOT in the style_preset param.
            The style_preset param only accepts a preset NAME (no colours, no fonts).
            Anything not in slides_json["style"] is silently ignored.

            Correct — custom style inside slides_json:
              slides_json = '{"style": {"preset":"corporate","accent_color":"#0F4C81",
                              "title_font":"Microsoft JhengHei","body_font":"Microsoft JhengHei",
                              "body_bg":"#F5F7FA","show_footer":true}, "slides":[...]}'

            Wrong — style info passed as separate parameters (colours/fonts are lost):
              create_presentation(slides_json='{"slides":[...]}', style_preset="modern",
                                  title_font="...")  ← only the preset name reaches the renderer

            Overridable fields inside slides_json["style"]:
              preset, accent_color, accent_text, body_bg, body_text,
              subtitle_color, card_bg, title_font, body_font, show_footer

        Args:
            slides_json:   JSON string with 'title', 'style' (see above), and 'slides' array.
            output_path:   Absolute Windows path inside an allowed directory
                           (e.g. 'D:/FAC_Job/Agent_test/output.pptx').
                           NEVER use /tmp or Linux paths — they will be rejected.
                           Ask the user for the output directory if unsure.
            style_preset:  Fallback preset name used ONLY when slides_json contains no
                           style object at all. Ignored otherwise.
            title_font:    Fallback font used only when slides_json has no title_font.
            body_font:     Fallback font used only when slides_json has no body_font.
        """
        logger.info(
            "create_presentation called: output=%s, slides_json=%d chars, preset_param=%s",
            output_path, len(slides_json), style_preset or "(none)",
        )
        out = pathlib.Path(output_path).resolve()
        try:
            cfg.check_path(out, write=True)
        except ToolError as e:
            logger.error("create_presentation: output path rejected: %s", e)
            raise

        try:
            payload = json.loads(slides_json)
        except json.JSONDecodeError as e:
            # Log the offending region so malformed agent JSON can be diagnosed
            # from the server log (the agent platform may truncate this error).
            start = max(0, e.pos - 80)
            logger.error(
                "create_presentation: invalid JSON at pos %d: %s | context: ...%s...",
                e.pos, e.msg, slides_json[start:e.pos + 80],
            )
            raise ToolError(f"slides_json is not valid JSON: {e}") from e

        if not isinstance(payload.get("slides"), list) or not payload["slides"]:
            logger.error(
                "create_presentation: missing/empty 'slides' array (top-level keys: %s)",
                list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
            )
            raise ToolError("slides_json must contain a non-empty 'slides' array.")

        # Non-blocking content audit — flags sparse slides but still generates.
        content_warnings = _audit_slides(payload["slides"])

        # Apply style: slides_json.style > tool params > config.toml defaults
        style = payload.setdefault("style", {})
        defaults = cfg.presentation_defaults
        # preset
        if not style.get("preset") and not style.get("accent_color"):
            # Only fall back to param/defaults when slides_json has no style at all
            if style_preset:
                style["preset"] = style_preset
            elif defaults["preset"]:
                style["preset"] = defaults["preset"]
        elif style_preset and not style.get("preset"):
            style["preset"] = style_preset
        # fonts — params fill gaps; slides_json always wins
        if title_font and not style.get("title_font") and not style.get("titleFont"):
            style["title_font"] = title_font
        elif defaults["title_font"] and not style.get("title_font") and not style.get("titleFont"):
            style["title_font"] = defaults["title_font"]
        if body_font and not style.get("body_font") and not style.get("bodyFont"):
            style["body_font"] = body_font
        elif defaults["body_font"] and not style.get("body_font") and not style.get("bodyFont"):
            style["body_font"] = defaults["body_font"]
        if defaults["show_footer"] is not None and "show_footer" not in style:
            style["show_footer"] = defaults["show_footer"]
        logger.info("create_presentation resolved style: %s", style)

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
            logger.error(
                "create_presentation: generator exited %d: %s", result.returncode, err
            )
            raise ToolError(f"Presentation generation failed: {err}")

        stdout = result.stdout.strip()
        for line in stdout.splitlines():
            if line.startswith("OK:"):
                # Format: OK:{path}:{count}:{preset} — the path may itself contain
                # colons (Windows drive letters), so split from the right.
                parts = line[3:].rsplit(":", 2)
                n_slides = parts[1] if len(parts) == 3 else "?"
                preset = parts[2] if len(parts) == 3 else style.get("preset", "default")
                # Verify the file actually exists and report its real size.
                # This makes it impossible for the agent to hallucinate success —
                # a genuine .pptx is always at least 10 KB.
                if not out.exists():
                    raise ToolError(
                        f"Generator reported success but file not found: {out}. "
                        "Check that the output directory is writable."
                    )
                size_kb = out.stat().st_size // 1024
                logger.info(
                    "create_presentation OK: %s (%s slides, %s KB)", out, n_slides, size_kb
                )
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

        logger.error(
            "create_presentation: unexpected generator output: %s",
            stdout or result.stderr or "(empty)",
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
