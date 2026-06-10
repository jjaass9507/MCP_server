"use strict";

/**
 * generate_pptx.js — pptxgenjs-based presentation generator for MCP server
 *
 * Usage:
 *   node generate_pptx.js <input.json> <output.pptx>
 *   node generate_pptx.js --test          # writes test_output.pptx in cwd
 */

const fs   = require("fs");
const path = require("path");

let PptxGenJS;
try {
    PptxGenJS = require("pptxgenjs");
} catch (e) {
    console.error("pptxgenjs not found. Run: npm install pptxgenjs");
    process.exit(2);
}

// ── Design constraints (hard-coded, cannot be overridden by style) ──────────

const BANNED_BG_COLORS = new Set([
    "F5F5DC", "FAF0E6", "FAEBD7", "FFF8DC", "FFFACD",  // cream/beige
    "f5f5dc", "faf0e6", "faebd7", "fff8dc", "fffacd",
]);

const BANNED_FONTS = new Set(["Aptos", "aptos"]);

// LibreOffice substitutes these with different glyph widths — QA unreliable
const QA_UNRELIABLE_FONTS = new Set(["Georgia", "Trebuchet MS"]);

const SAFE_FONTS = [
    "Arial", "Calibri", "Helvetica Neue",
    "Noto Sans", "Noto Sans TC",
    "Microsoft JhengHei", "Microsoft YaHei",
];

// ── Presets ──────────────────────────────────────────────────────────────────

const PRESETS = {
    corporate: {
        accentColor:   "003366",
        accentText:    "FFFFFF",
        bodyBg:        "FFFFFF",
        bodyText:      "222222",
        subtitleColor: "555555",
        titleFont:     "Calibri",
        bodyFont:      "Calibri",
    },
    modern: {
        accentColor:   "E84545",
        accentText:    "FFFFFF",
        bodyBg:        "F0F0F0",
        bodyText:      "111111",
        subtitleColor: "444444",
        titleFont:     "Arial",
        bodyFont:      "Arial",
    },
    dark: {
        accentColor:   "00B4D8",
        accentText:    "0D0D0D",
        bodyBg:        "1A1A2E",
        bodyText:      "E0E0E0",
        subtitleColor: "AAAAAA",
        titleFont:     "Arial",
        bodyFont:      "Arial",
    },
    minimal: {
        accentColor:   "222222",
        accentText:    "FFFFFF",
        bodyBg:        "FFFFFF",
        bodyText:      "222222",
        subtitleColor: "666666",
        titleFont:     "Calibri",
        bodyFont:      "Calibri",
    },
    tech: {
        accentColor:   "7B2FBE",
        accentText:    "FFFFFF",
        bodyBg:        "0D0D0D",
        bodyText:      "E8E8E8",
        subtitleColor: "AAAAAA",
        titleFont:     "Noto Sans",
        bodyFont:      "Noto Sans",
    },
};

// ── Validation helpers ───────────────────────────────────────────────────────

function stripHash(color) {
    return color ? color.replace(/^#/, "").toUpperCase() : color;
}

function validateStyle(style) {
    const warnings = [];
    const bg = stripHash(style.bodyBg || "");
    if (BANNED_BG_COLORS.has(bg)) {
        warnings.push(`Background color #${bg} is banned (cream/beige). Falling back to FFFFFF.`);
        style.bodyBg = "FFFFFF";
    }
    for (const key of ["titleFont", "bodyFont"]) {
        if (BANNED_FONTS.has(style[key])) {
            warnings.push(`Font "${style[key]}" is banned (Aptos causes width issues). Falling back to Calibri.`);
            style[key] = "Calibri";
        }
        if (QA_UNRELIABLE_FONTS.has(style[key])) {
            warnings.push(`Font "${style[key]}" may render differently in LibreOffice QA vs PowerPoint.`);
        }
    }
    return warnings;
}

// ── Slide builders ───────────────────────────────────────────────────────────

function addTitleBar(slide, pptx, style, titleText) {
    // Solid accent rectangle — NO decorative underline, NO full-width stripes
    slide.addShape(pptx.ShapeType.rect, {
        x: 0, y: 0, w: "100%", h: 1.1,
        fill: { color: style.accentColor },
        line: { color: style.accentColor },  // no visible border
    });
    slide.addText(titleText || "", {
        x: 0.4, y: 0.12, w: 9.2, h: 0.86,
        fontFace: style.titleFont,
        fontSize: 26,
        bold: true,
        color: style.accentText,
        valign: "middle",
        wrap: true,
    });
}

function makeBulletItems(bullets, bodyFont, bodyText, bodySize) {
    const items = [];
    for (const raw of (bullets || [])) {
        const isIndented = raw.startsWith("  ");
        const text = raw.trimStart();
        items.push({
            text,
            options: {
                bullet:      { code: "2022" },
                indentLevel: isIndented ? 1 : 0,
                fontFace:    bodyFont,
                fontSize:    isIndented ? bodySize - 2 : bodySize,
                color:       bodyText,
                paraSpaceAfter: 6,
            },
        });
    }
    return items;
}

function buildSlide(pptx, slideDef, style) {
    const slide = pptx.addSlide();
    slide.background = { color: style.bodyBg };

    const bodySize  = style.bodySize  || 18;
    const { titleFont, bodyFont, bodyText, subtitleColor, accentColor } = style;

    switch (slideDef.layout) {

        case "title": {
            // Full-bleed accent background on top half
            slide.addShape(pptx.ShapeType.rect, {
                x: 0, y: 0, w: "100%", h: 3.2,
                fill: { color: accentColor },
                line: { color: accentColor },
            });
            slide.addText(slideDef.title || "", {
                x: 0.6, y: 0.7, w: 8.8, h: 1.5,
                fontFace: titleFont, fontSize: 36, bold: true,
                color: style.accentText, valign: "middle", align: "left", wrap: true,
            });
            if (slideDef.subtitle) {
                slide.addText(slideDef.subtitle, {
                    x: 0.6, y: 3.4, w: 8.8, h: 1.4,
                    fontFace: bodyFont, fontSize: 20,
                    color: subtitleColor, valign: "top", wrap: true,
                });
            }
            break;
        }

        case "section": {
            slide.addShape(pptx.ShapeType.rect, {
                x: 0, y: 1.8, w: "100%", h: 2.0,
                fill: { color: accentColor },
                line: { color: accentColor },
            });
            slide.addText(slideDef.title || "", {
                x: 0.6, y: 1.9, w: 8.8, h: 1.5,
                fontFace: titleFont, fontSize: 32, bold: true,
                color: style.accentText, valign: "middle", align: "center", wrap: true,
            });
            if (slideDef.subtitle) {
                slide.addText(slideDef.subtitle, {
                    x: 0.6, y: 3.9, w: 8.8, h: 0.9,
                    fontFace: bodyFont, fontSize: 18,
                    color: subtitleColor, align: "center", wrap: true,
                });
            }
            break;
        }

        case "content": {
            addTitleBar(slide, pptx, style, slideDef.title);
            if (slideDef.bullets && slideDef.bullets.length > 0) {
                const items = makeBulletItems(slideDef.bullets, bodyFont, bodyText, bodySize);
                slide.addText(items, {
                    x: 0.4, y: 1.25, w: 9.2, h: 4.1,
                    valign: "top",
                });
            } else if (slideDef.body) {
                slide.addText(slideDef.body, {
                    x: 0.4, y: 1.25, w: 9.2, h: 4.1,
                    fontFace: bodyFont, fontSize: bodySize,
                    color: bodyText, valign: "top", wrap: true,
                });
            }
            if (slideDef.notes) slide.addNotes(slideDef.notes);
            break;
        }

        case "two_column": {
            addTitleBar(slide, pptx, style, slideDef.title);
            const colW = 4.35;
            // Left column
            if (slideDef.left_title) {
                slide.addText(slideDef.left_title, {
                    x: 0.4, y: 1.25, w: colW, h: 0.45,
                    fontFace: titleFont, fontSize: 16, bold: true,
                    color: accentColor, valign: "middle",
                });
            }
            const leftY = slideDef.left_title ? 1.75 : 1.25;
            const leftH = slideDef.left_title ? 3.6  : 4.1;
            if (slideDef.left && slideDef.left.length > 0) {
                slide.addText(makeBulletItems(slideDef.left, bodyFont, bodyText, bodySize - 1), {
                    x: 0.4, y: leftY, w: colW, h: leftH, valign: "top",
                });
            }
            // Vertical divider — NO decorative card stripes
            slide.addShape(pptx.ShapeType.line, {
                x: 5.0, y: 1.3, w: 0, h: 4.0,
                line: { color: "CCCCCC", width: 1 },
            });
            // Right column
            if (slideDef.right_title) {
                slide.addText(slideDef.right_title, {
                    x: 5.25, y: 1.25, w: colW, h: 0.45,
                    fontFace: titleFont, fontSize: 16, bold: true,
                    color: accentColor, valign: "middle",
                });
            }
            const rightY = slideDef.right_title ? 1.75 : 1.25;
            const rightH = slideDef.right_title ? 3.6  : 4.1;
            if (slideDef.right && slideDef.right.length > 0) {
                slide.addText(makeBulletItems(slideDef.right, bodyFont, bodyText, bodySize - 1), {
                    x: 5.25, y: rightY, w: colW, h: rightH, valign: "top",
                });
            }
            if (slideDef.notes) slide.addNotes(slideDef.notes);
            break;
        }

        case "image_text": {
            addTitleBar(slide, pptx, style, slideDef.title);
            // Image on left half
            if (slideDef.image_path && fs.existsSync(slideDef.image_path)) {
                slide.addImage({ path: slideDef.image_path, x: 0.4, y: 1.3, w: 4.5, h: 3.9 });
            } else if (slideDef.image_path) {
                slide.addText(`[Image: ${path.basename(slideDef.image_path)}]`, {
                    x: 0.4, y: 1.3, w: 4.5, h: 3.9,
                    fontFace: bodyFont, fontSize: 14, color: "999999",
                    align: "center", valign: "middle",
                });
            }
            // Text on right half
            if (slideDef.body) {
                slide.addText(slideDef.body, {
                    x: 5.2, y: 1.3, w: 4.5, h: 3.9,
                    fontFace: bodyFont, fontSize: bodySize,
                    color: bodyText, valign: "top", wrap: true,
                });
            }
            if (slideDef.notes) slide.addNotes(slideDef.notes);
            break;
        }

        case "blank":
        default: {
            if (slideDef.title) addTitleBar(slide, pptx, style, slideDef.title);
            if (slideDef.body) {
                const bodyY = slideDef.title ? 1.25 : 0.4;
                const bodyH = slideDef.title ? 4.1  : 5.0;
                slide.addText(slideDef.body, {
                    x: 0.4, y: bodyY, w: 9.2, h: bodyH,
                    fontFace: bodyFont, fontSize: bodySize,
                    color: bodyText, valign: "top", wrap: true,
                });
            }
            break;
        }
    }

    return slide;
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function generate(inputPath, outputPath) {
    const raw = JSON.parse(fs.readFileSync(inputPath, "utf8"));

    // Resolve style
    const presetName = (raw.style && raw.style.preset) || "corporate";
    if (!PRESETS[presetName]) {
        throw new Error(`Unknown preset "${presetName}". Valid: ${Object.keys(PRESETS).join(", ")}`);
    }
    const style = Object.assign({}, PRESETS[presetName], raw.style || {});
    // Strip # from any color overrides
    for (const k of ["accentColor","accentText","bodyBg","bodyText","subtitleColor"]) {
        if (style[k]) style[k] = stripHash(style[k]);
    }

    const warnings = validateStyle(style);
    if (warnings.length) console.warn("WARNINGS:\n" + warnings.map(w => "  " + w).join("\n"));

    // Build presentation
    const pptx = new PptxGenJS();
    pptx.layout   = "LAYOUT_WIDE";   // 10" × 5.625"
    pptx.author   = "MCP Server";
    pptx.title    = raw.title || "";

    for (const slideDef of (raw.slides || [])) {
        buildSlide(pptx, slideDef, style);
    }

    await pptx.writeFile({ fileName: outputPath });

    const count = (raw.slides || []).length;
    if (warnings.length) {
        console.log(`WARNINGS:${warnings.join("|")}`);
    }
    console.log(`OK:${outputPath}:${count}:${presetName}`);
}

// ── Test mode ────────────────────────────────────────────────────────────────

async function runTest() {
    const testInput = {
        title: "MCP Server 簡報測試",
        style: { preset: "corporate", titleFont: "Microsoft JhengHei", bodyFont: "Microsoft JhengHei" },
        slides: [
            { layout: "title", title: "MCP Server 簡報測試", subtitle: "pptxgenjs 生成驗證" },
            { layout: "content", title: "功能清單",
              bullets: ["資料庫查詢工具", "  PostgreSQL 支援", "  自動 schema 偵測", "檔案系統工具", "簡報生成工具"],
              notes: "這是演講者備忘" },
            { layout: "two_column", title: "比較",
              left_title: "優點", left: ["精確 OOXML", "文字可編輯", "字型精確"],
              right_title: "限制", right: ["需要 Node.js", "需要 pptxgenjs"] },
            { layout: "section", title: "第二章節", subtitle: "進階功能" },
            { layout: "blank", title: "自由版面", body: "這裡可以放任意文字內容，不帶 bullets 格式。" },
        ],
    };
    const tmp = path.join(process.cwd(), "_test_input.json");
    const out = path.join(process.cwd(), "test_output.pptx");
    fs.writeFileSync(tmp, JSON.stringify(testInput));
    await generate(tmp, out);
    fs.unlinkSync(tmp);
    console.log(`Test file written: ${out}`);
}

// ── Entry point ───────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
if (args[0] === "--test") {
    runTest().catch(e => { console.error(e.message); process.exit(1); });
} else if (args.length >= 2) {
    generate(args[0], args[1]).catch(e => { console.error(e.message); process.exit(1); });
} else {
    console.error("Usage: node generate_pptx.js <input.json> <output.pptx>");
    console.error("       node generate_pptx.js --test");
    process.exit(1);
}
