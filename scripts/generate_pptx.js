"use strict";

/**
 * generate_pptx.js — pptxgenjs presentation generator for MCP server
 *
 * Usage:
 *   node generate_pptx.js <input.json> <output.pptx>
 *   node generate_pptx.js --test
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

// ── Design constraints ───────────────────────────────────────────────────────

const BANNED_BG_COLORS = new Set([
    "F5F5DC","FAF0E6","FAEBD7","FFF8DC","FFFACD",
    "f5f5dc","faf0e6","faebd7","fff8dc","fffacd",
]);
const BANNED_FONTS = new Set(["Aptos","aptos"]);
const QA_UNRELIABLE_FONTS = new Set(["Georgia","Trebuchet MS"]);

// ── Presets ──────────────────────────────────────────────────────────────────
// 5 original + 3 inspired by open-slide (aurora, bright_sans, warm)

const PRESETS = {
    corporate: {
        accentColor:"003366", accentText:"FFFFFF",
        bodyBg:"FFFFFF",  bodyText:"222222", subtitleColor:"555555", cardBg:"EEF2F7",
        titleFont:"Calibri", bodyFont:"Calibri",
    },
    modern: {
        accentColor:"E84545", accentText:"FFFFFF",
        bodyBg:"F0F0F0", bodyText:"111111", subtitleColor:"444444", cardBg:"E0E0E0",
        titleFont:"Arial", bodyFont:"Arial",
    },
    dark: {
        accentColor:"00B4D8", accentText:"0D0D0D",
        bodyBg:"1A1A2E", bodyText:"E0E0E0", subtitleColor:"AAAAAA", cardBg:"252540",
        titleFont:"Arial", bodyFont:"Arial",
    },
    minimal: {
        accentColor:"222222", accentText:"FFFFFF",
        bodyBg:"FFFFFF", bodyText:"222222", subtitleColor:"666666", cardBg:"F5F5F5",
        titleFont:"Calibri", bodyFont:"Calibri",
    },
    tech: {
        accentColor:"7B2FBE", accentText:"FFFFFF",
        bodyBg:"0D0D0D", bodyText:"E8E8E8", subtitleColor:"AAAAAA", cardBg:"1A1A1A",
        titleFont:"Noto Sans", bodyFont:"Noto Sans",
    },
    // Inspired by open-slide themes
    aurora: {
        accentColor:"A78BFA", accentText:"FFFFFF",
        bodyBg:"0E0E0E", bodyText:"F5F5F5", subtitleColor:"9CA3AF", cardBg:"1C1C28",
        titleFont:"Arial", bodyFont:"Arial",
    },
    bright_sans: {
        accentColor:"1A73E8", accentText:"FFFFFF",
        bodyBg:"FFFFFF", bodyText:"202124", subtitleColor:"5F6368", cardBg:"F1F3F4",
        titleFont:"Calibri", bodyFont:"Calibri",
    },
    warm: {
        accentColor:"FF3C00", accentText:"FFFFFF",
        bodyBg:"F8F4EF", bodyText:"2F3034", subtitleColor:"6B7280", cardBg:"EDE9E4",
        titleFont:"Arial", bodyFont:"Arial",
    },
};

// ── Icon bundle ───────────────────────────────────────────────────────────────

const ICONS_DIR = path.join(__dirname, "icons");
const ICONS = new Map();
if (fs.existsSync(ICONS_DIR)) {
    for (const f of fs.readdirSync(ICONS_DIR)) {
        if (f.endsWith(".svg"))
            ICONS.set(f.slice(0, -4), fs.readFileSync(path.join(ICONS_DIR, f), "utf8"));
    }
}

function recolorSvg(svgText, hexColor) {
    const c = hexColor.replace(/^#/, "");
    return svgText
        .replace(/currentColor/g, `#${c}`)
        .replace(/stroke="(#000000|#000|black)"/gi, `stroke="#${c}"`)
        .replace(/fill="(#000000|#000|black)"/gi, `fill="#${c}"`);
}

function svgToDataUri(svgText) {
    return `data:image/svg+xml;base64,${Buffer.from(svgText, "utf8").toString("base64")}`;
}

/** Render an icon from the bundle. Returns false if icon name not found. */
function addIcon(slide, iconName, x, y, sizein, hexColor) {
    if (!iconName) return false;
    const raw = ICONS.get(iconName);
    if (!raw) return false;
    slide.addImage({ data: svgToDataUri(recolorSvg(raw, hexColor)), x, y, w: sizein, h: sizein });
    return true;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function stripHash(c) { return c ? c.replace(/^#/, "").toUpperCase() : c; }

function validateStyle(style) {
    const warnings = [];
    const bg = stripHash(style.bodyBg || "");
    if (BANNED_BG_COLORS.has(bg)) {
        warnings.push(`Background #${bg} is banned (cream/beige). Falling back to FFFFFF.`);
        style.bodyBg = "FFFFFF";
    }
    for (const k of ["titleFont","bodyFont"]) {
        if (BANNED_FONTS.has(style[k])) {
            warnings.push(`Font "${style[k]}" is banned (Aptos). Falling back to Calibri.`);
            style[k] = "Calibri";
        }
        if (QA_UNRELIABLE_FONTS.has(style[k]))
            warnings.push(`Font "${style[k]}" may render differently in LibreOffice.`);
    }
    return warnings;
}

// ── Reusable components ───────────────────────────────────────────────────────

/** Solid accent title bar across the top — no decorative underlines or stripes. */
function addTitleBar(slide, pptx, style, titleText) {
    slide.addShape(pptx.ShapeType.rect, {
        x:0, y:0, w:"100%", h:1.1,
        fill:{ color:style.accentColor }, line:{ color:style.accentColor },
    });
    slide.addText(titleText || "", {
        x:0.4, y:0.12, w:9.2, h:0.86,
        fontFace:style.titleFont, fontSize:26, bold:true,
        color:style.accentText, valign:"middle", wrap:true,
    });
}

/** Optional small coloured label above body area (e.g. "KEY INSIGHT", "CHAPTER 1"). */
function addEyebrow(slide, style, text, yStart) {
    if (!text) return 0;
    slide.addText(text.toUpperCase(), {
        x:0.4, y:yStart, w:9.2, h:0.32,
        fontFace:style.bodyFont, fontSize:11, bold:true,
        color:style.accentColor, charSpacing:2,
    });
    return 0.38; // height consumed
}

/** Optional footer: thin separator line + [section label left] [page X/N right]. */
function addFooter(slide, pptx, style, sectionName, pageNum, totalPages) {
    if (!style.show_footer) return;
    const fy = 5.25;
    slide.addShape(pptx.ShapeType.line, {
        x:0.4, y:fy, w:9.2, h:0,
        line:{ color: style.bodyText === "222222" || style.bodyText === "202124" ? "DDDDDD" : "444444", width:0.5 },
    });
    if (sectionName) {
        slide.addText(sectionName, {
            x:0.4, y:fy+0.05, w:6.5, h:0.25,
            fontFace:style.bodyFont, fontSize:10, color:style.subtitleColor,
        });
    }
    if (pageNum && totalPages) {
        slide.addText(`${pageNum} / ${totalPages}`, {
            x:7.5, y:fy+0.05, w:2.1, h:0.25,
            fontFace:style.bodyFont, fontSize:10, color:style.subtitleColor, align:"right",
        });
    }
}

function makeBulletItems(bullets, bodyFont, bodyText, bodySize) {
    return (bullets || []).map(raw => {
        const indented = raw.startsWith("  ");
        return {
            text: raw.trimStart(),
            options:{
                bullet:{ code:"2022" },
                indentLevel: indented ? 1 : 0,
                fontFace: bodyFont,
                fontSize: indented ? bodySize - 2 : bodySize,
                color: bodyText,
                paraSpaceAfter: 6,
            },
        };
    });
}

// ── Slide builders ────────────────────────────────────────────────────────────

function buildSlide(pptx, slideDef, style, pageNum, totalPages) {
    const slide = pptx.addSlide();
    slide.background = { color: style.bodyBg };

    const bodySize    = style.bodySize || 18;
    const footerH     = style.show_footer ? 0.45 : 0;
    const contentMaxH = 5.625 - footerH;

    const { titleFont, bodyFont, bodyText, subtitleColor, accentColor, cardBg } = style;

    switch (slideDef.layout) {

        // ── title ─────────────────────────────────────────────────────────────
        case "title": {
            slide.addShape(pptx.ShapeType.rect, {
                x:0, y:0, w:"100%", h:3.2,
                fill:{ color:accentColor }, line:{ color:accentColor },
            });
            slide.addText(slideDef.title || "", {
                x:0.6, y:0.65, w:8.8, h:1.8,
                fontFace:titleFont, fontSize:38, bold:true,
                color:style.accentText, valign:"middle", align:"left", wrap:true,
            });
            if (slideDef.subtitle) {
                slide.addText(slideDef.subtitle, {
                    x:0.6, y:3.35, w:8.8, h:1.5,
                    fontFace:bodyFont, fontSize:21,
                    color:subtitleColor, valign:"top", wrap:true,
                });
            }
            break;
        }

        // ── section ───────────────────────────────────────────────────────────
        case "section": {
            // Left vertical accent stripe (avoids heavy full-width band)
            slide.addShape(pptx.ShapeType.rect, {
                x:0, y:0, w:0.35, h:"100%",
                fill:{ color:accentColor }, line:{ color:accentColor },
            });
            const hasIcon = slideDef.icon && ICONS.has(slideDef.icon);
            const titleY  = hasIcon ? 1.6 : 1.9;
            const titleW  = hasIcon ? 6.9 : 9.2;
            if (hasIcon) {
                // Large decorative icon on right side
                addIcon(slide, slideDef.icon, 7.3, 1.0, 1.5, accentColor);
            }
            slide.addText(slideDef.title || "", {
                x:0.6, y:titleY, w:titleW, h:1.7,
                fontFace:titleFont, fontSize:40, bold:true,
                color:accentColor, valign:"middle", align:"left", wrap:true,
            });
            if (slideDef.subtitle) {
                slide.addText(slideDef.subtitle, {
                    x:0.6, y:titleY + 1.75, w:titleW, h:0.85,
                    fontFace:bodyFont, fontSize:20,
                    color:subtitleColor, align:"left", wrap:true,
                });
            }
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── content ───────────────────────────────────────────────────────────
        case "content": {
            addTitleBar(slide, pptx, style, slideDef.title);
            let bodyY = 1.25;

            // Icon beside eyebrow label (shifts eyebrow text right if icon present)
            if (slideDef.eyebrow) {
                const iconRendered = slideDef.icon
                    ? addIcon(slide, slideDef.icon, 0.4, bodyY, 0.32, accentColor)
                    : false;
                const ebX = iconRendered ? 0.8 : 0.4;
                const ebW = iconRendered ? 8.8 : 9.2;
                slide.addText(slideDef.eyebrow.toUpperCase(), {
                    x:ebX, y:bodyY, w:ebW, h:0.32,
                    fontFace:style.bodyFont, fontSize:11, bold:true,
                    color:accentColor, charSpacing:2, valign:"middle",
                });
                bodyY += 0.38;
            } else if (slideDef.icon) {
                // icon without eyebrow: small icon at top-left of content area
                addIcon(slide, slideDef.icon, 0.4, bodyY, 0.35, accentColor);
                bodyY += 0.45;
            }

            const bodyH = contentMaxH - bodyY - 0.1;

            if (slideDef.bullets && slideDef.bullets.length > 0) {
                slide.addText(
                    makeBulletItems(slideDef.bullets, bodyFont, bodyText, bodySize),
                    { x:0.4, y:bodyY, w:9.2, h:bodyH, valign:"top" }
                );
            } else if (slideDef.body) {
                slide.addText(slideDef.body, {
                    x:0.4, y:bodyY, w:9.2, h:bodyH,
                    fontFace:bodyFont, fontSize:bodySize,
                    color:bodyText, valign:"top", wrap:true,
                });
            }
            if (slideDef.notes) slide.addNotes(slideDef.notes);
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── two_column ────────────────────────────────────────────────────────
        case "two_column": {
            addTitleBar(slide, pptx, style, slideDef.title);
            const colW = 4.35;
            const colBodySize = bodySize - 1;

            // Left
            let leftY = 1.25;
            if (slideDef.left_title) {
                const leftIconOk = addIcon(slide, slideDef.left_icon, 0.4, leftY + 0.05, 0.35, accentColor);
                slide.addText(slideDef.left_title, {
                    x: leftIconOk ? 0.82 : 0.4, y:leftY, w: leftIconOk ? colW - 0.42 : colW, h:0.45,
                    fontFace:titleFont, fontSize:16, bold:true, color:accentColor, valign:"middle",
                });
                leftY += 0.5;
            }
            if (slideDef.left && slideDef.left.length > 0) {
                slide.addText(
                    makeBulletItems(slideDef.left, bodyFont, bodyText, colBodySize),
                    { x:0.4, y:leftY, w:colW, h:contentMaxH - leftY - 0.1, valign:"top" }
                );
            }

            // Vertical divider — simple line, no card stripes
            slide.addShape(pptx.ShapeType.line, {
                x:5.0, y:1.3, w:0, h:contentMaxH - 1.4,
                line:{ color:"CCCCCC", width:1 },
            });

            // Right
            let rightY = 1.25;
            if (slideDef.right_title) {
                const rightIconOk = addIcon(slide, slideDef.right_icon, 5.25, rightY + 0.05, 0.35, accentColor);
                slide.addText(slideDef.right_title, {
                    x: rightIconOk ? 5.67 : 5.25, y:rightY, w: rightIconOk ? colW - 0.42 : colW, h:0.45,
                    fontFace:titleFont, fontSize:16, bold:true, color:accentColor, valign:"middle",
                });
                rightY += 0.5;
            }
            if (slideDef.right && slideDef.right.length > 0) {
                slide.addText(
                    makeBulletItems(slideDef.right, bodyFont, bodyText, colBodySize),
                    { x:5.25, y:rightY, w:colW, h:contentMaxH - rightY - 0.1, valign:"top" }
                );
            }

            if (slideDef.notes) slide.addNotes(slideDef.notes);
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── stats (KPI cards — inspired by open-slide) ────────────────────────
        // stats: [{value:"99%", label:"Uptime", desc:"last 30 days"}, ...]
        case "stats": {
            addTitleBar(slide, pptx, style, slideDef.title);
            const stats = (slideDef.stats || []).slice(0, 4);
            const count = stats.length || 1;
            const gap   = 0.2;
            const totalW = 9.2;
            const cardW  = (totalW - gap * (count - 1)) / count;
            const cardY  = 1.3;
            const cardH  = contentMaxH - cardY - 0.15;

            stats.forEach((stat, i) => {
                const cx = 0.4 + i * (cardW + gap);

                // Card background (no border — cleaner look)
                slide.addShape(pptx.ShapeType.rect, {
                    x:cx, y:cardY, w:cardW, h:cardH,
                    fill:{ color: cardBg },
                    line:{ color: cardBg },
                });

                // Accent top stripe (thicker: 0.12in)
                slide.addShape(pptx.ShapeType.rect, {
                    x:cx, y:cardY, w:cardW, h:0.12,
                    fill:{ color:accentColor }, line:{ color:accentColor },
                });

                // Icon above value (optional)
                const iconSize = 0.42;
                const iconRendered = stat.icon
                    ? addIcon(slide, stat.icon, cx + (cardW - iconSize) / 2, cardY + 0.18, iconSize, accentColor)
                    : false;
                const valueY = cardY + (iconRendered ? 0.72 : 0.55);

                // Big value
                slide.addText(stat.value || "—", {
                    x:cx+0.1, y:valueY, w:cardW-0.2, h:1.2,
                    fontFace:titleFont, fontSize:44, bold:true,
                    color:accentColor, align:"center", valign:"middle",
                });

                // Label
                slide.addText(stat.label || "", {
                    x:cx+0.1, y:valueY+1.2, w:cardW-0.2, h:0.5,
                    fontFace:bodyFont, fontSize:14, bold:true,
                    color:bodyText, align:"center",
                });

                // Description
                if (stat.desc) {
                    slide.addText(stat.desc, {
                        x:cx+0.1, y:valueY+1.7, w:cardW-0.2, h:cardH - (valueY - cardY) - 1.85,
                        fontFace:bodyFont, fontSize:11,
                        color:subtitleColor, align:"center", wrap:true,
                    });
                }
            });

            if (slideDef.notes) slide.addNotes(slideDef.notes);
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── agenda ────────────────────────────────────────────────────────────
        // agenda: {layout:"agenda", title:"...", items:["章節一", ...], active_item:2}
        case "agenda": {
            // Left accent bar
            slide.addShape(pptx.ShapeType.rect, {
                x:0, y:0, w:0.18, h:"100%",
                fill:{ color:accentColor }, line:{ color:accentColor },
            });
            slide.addText(slideDef.title || "", {
                x:0.45, y:0.22, w:9.1, h:0.65,
                fontFace:titleFont, fontSize:26, bold:true, color:accentColor,
            });
            const items = (slideDef.items || []).slice(0, 7);
            const count = items.length || 1;
            const areaH = contentMaxH - 1.0;
            const itemH = Math.min(0.75, areaH / count);
            items.forEach((item, i) => {
                const iy = 1.0 + i * itemH;
                const isActive = (slideDef.active_item === i + 1);
                if (isActive) {
                    slide.addShape(pptx.ShapeType.rect, {
                        x:0.45, y:iy - 0.04, w:9.1, h:itemH - 0.04,
                        fill:{ color:accentColor, transparency:88 },
                        line:{ color:accentColor, width:0.5 },
                    });
                }
                const circR = 0.19;
                slide.addShape(pptx.ShapeType.ellipse, {
                    x:0.55, y:iy + (itemH/2 - circR) - 0.02, w:circR*2, h:circR*2,
                    fill:{ color: isActive ? accentColor : "FFFFFF", transparency: isActive ? 0 : 100 },
                    line:{ color: isActive ? accentColor : subtitleColor, width:1.5 },
                });
                slide.addText(String(i + 1), {
                    x:0.55, y:iy + (itemH/2 - circR) - 0.02, w:circR*2, h:circR*2,
                    fontFace:bodyFont, fontSize:11, bold:true,
                    color: isActive ? style.accentText : subtitleColor,
                    align:"center", valign:"middle",
                });
                slide.addText(item, {
                    x:1.05, y:iy, w:8.45, h:itemH - 0.04,
                    fontFace:bodyFont, fontSize: count <= 4 ? 20 : 17,
                    color: isActive ? bodyText : subtitleColor,
                    bold: isActive, valign:"middle", wrap:true,
                });
            });
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── big_message ───────────────────────────────────────────────────────
        // big_message: {layout:"big_message", message:"...", supporting:"...", icon:"optional", bg:"subtle"}
        case "big_message": {
            const useAccentBg = slideDef.bg !== "subtle";
            const msgBg    = useAccentBg ? accentColor : style.bodyBg;
            const msgColor = useAccentBg ? style.accentText : accentColor;
            const supColor = useAccentBg ? style.accentText : subtitleColor;
            const iconColor = useAccentBg ? style.accentText : accentColor;
            slide.background = { color: msgBg };
            const hasIcon = slideDef.icon && ICONS.has(slideDef.icon);
            let msgY = hasIcon ? 1.65 : 1.3;
            if (hasIcon) {
                addIcon(slide, slideDef.icon, 4.55, 0.45, 0.95, iconColor);
            }
            slide.addText(slideDef.message || "", {
                x:0.5, y:msgY, w:9.0, h:1.9,
                fontFace:titleFont, fontSize:48, bold:true,
                color:msgColor, align:"center", valign:"middle", wrap:true,
            });
            if (slideDef.supporting) {
                slide.addText(slideDef.supporting, {
                    x:1.2, y:msgY + 2.0, w:7.6, h:0.9,
                    fontFace:bodyFont, fontSize:19,
                    color:supColor, align:"center", valign:"top", wrap:true,
                });
            }
            if (slideDef.notes) slide.addNotes(slideDef.notes);
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── timeline ──────────────────────────────────────────────────────────
        // timeline: {layout:"timeline", title:"...", events:[{date,label,desc,icon}, ...]}
        case "timeline": {
            addTitleBar(slide, pptx, style, slideDef.title);
            const events = (slideDef.events || []).slice(0, 6);
            const evCount = events.length || 1;
            const lineY  = 3.05;
            const sx = 0.65, ex = 9.45;
            const span = ex - sx;
            // Horizontal axis line
            slide.addShape(pptx.ShapeType.line, {
                x:sx, y:lineY, w:span, h:0,
                line:{ color:accentColor, width:2.5 },
            });
            events.forEach((evt, i) => {
                const nx = evCount === 1 ? sx + span/2 : sx + (span / (evCount-1)) * i;
                const above = i % 2 === 0;
                // Node circle
                slide.addShape(pptx.ShapeType.ellipse, {
                    x:nx - 0.12, y:lineY - 0.12, w:0.24, h:0.24,
                    fill:{ color:accentColor }, line:{ color:accentColor },
                });
                const labelW = Math.min(1.75, evCount > 3 ? span/evCount + 0.1 : 2.2);
                const lx = nx - labelW/2;
                if (above) {
                    // connector up
                    slide.addShape(pptx.ShapeType.line, {
                        x:nx, y:lineY - 0.12, w:0, h:-0.5,
                        line:{ color:accentColor, width:1 },
                    });
                    if (evt.icon) addIcon(slide, evt.icon, nx - 0.18, 1.25, 0.36, accentColor);
                    if (evt.date) slide.addText(evt.date, {
                        x:lx, y:1.68, w:labelW, h:0.3,
                        fontFace:bodyFont, fontSize:10, bold:true, color:accentColor, align:"center",
                    });
                    if (evt.label) slide.addText(evt.label, {
                        x:lx, y:2.0, w:labelW, h:0.42,
                        fontFace:bodyFont, fontSize:12, bold:true, color:bodyText, align:"center", wrap:true,
                    });
                    if (evt.desc) slide.addText(evt.desc, {
                        x:lx, y:2.44, w:labelW, h:0.52,
                        fontFace:bodyFont, fontSize:10, color:subtitleColor, align:"center", wrap:true,
                    });
                } else {
                    // connector down
                    slide.addShape(pptx.ShapeType.line, {
                        x:nx, y:lineY + 0.12, w:0, h:0.5,
                        line:{ color:accentColor, width:1 },
                    });
                    if (evt.icon) addIcon(slide, evt.icon, nx - 0.18, lineY + 0.72, 0.36, accentColor);
                    if (evt.date) slide.addText(evt.date, {
                        x:lx, y:lineY + 0.68, w:labelW, h:0.3,
                        fontFace:bodyFont, fontSize:10, bold:true, color:accentColor, align:"center",
                    });
                    if (evt.label) slide.addText(evt.label, {
                        x:lx, y:lineY + 1.0, w:labelW, h:0.42,
                        fontFace:bodyFont, fontSize:12, bold:true, color:bodyText, align:"center", wrap:true,
                    });
                    if (evt.desc) slide.addText(evt.desc, {
                        x:lx, y:lineY + 1.44, w:labelW, h:0.52,
                        fontFace:bodyFont, fontSize:10, color:subtitleColor, align:"center", wrap:true,
                    });
                }
            });
            if (slideDef.notes) slide.addNotes(slideDef.notes);
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── process ───────────────────────────────────────────────────────────
        // process: {layout:"process", title:"...", steps:[{label,desc,icon}, ...]}
        case "process": {
            addTitleBar(slide, pptx, style, slideDef.title);
            const steps = (slideDef.steps || []).slice(0, 5);
            const sCount  = steps.length || 1;
            const arrowW  = 0.28;
            const gap     = 0.12;
            const totalW  = 9.2;
            const cardW   = sCount > 1
                ? (totalW - (sCount - 1) * (arrowW + gap * 2)) / sCount
                : totalW;
            const cardH   = contentMaxH - 1.5;
            const cardY   = 1.38;
            steps.forEach((step, i) => {
                const cx = 0.4 + i * (cardW + arrowW + gap * 2);
                // Card
                slide.addShape(pptx.ShapeType.rect, {
                    x:cx, y:cardY, w:cardW, h:cardH,
                    fill:{ color:cardBg }, line:{ color:cardBg },
                });
                // Step number circle
                const cr = 0.2;
                const ccx = cx + cardW/2 - cr;
                slide.addShape(pptx.ShapeType.ellipse, {
                    x:ccx, y:cardY + 0.18, w:cr*2, h:cr*2,
                    fill:{ color:accentColor }, line:{ color:accentColor },
                });
                slide.addText(String(i + 1), {
                    x:ccx, y:cardY + 0.18, w:cr*2, h:cr*2,
                    fontFace:bodyFont, fontSize:14, bold:true,
                    color:style.accentText, align:"center", valign:"middle",
                });
                let cy = cardY + 0.7;
                if (step.icon) {
                    const iconSz = 0.42;
                    addIcon(slide, step.icon, cx + cardW/2 - iconSz/2, cy, iconSz, accentColor);
                    cy += 0.52;
                }
                if (step.label) {
                    slide.addText(step.label, {
                        x:cx + 0.1, y:cy, w:cardW - 0.2, h:0.5,
                        fontFace:bodyFont, fontSize:13, bold:true,
                        color:bodyText, align:"center", wrap:true,
                    });
                    cy += 0.54;
                }
                if (step.desc) {
                    slide.addText(step.desc, {
                        x:cx + 0.12, y:cy, w:cardW - 0.24, h:cardH - (cy - cardY) - 0.18,
                        fontFace:bodyFont, fontSize:11,
                        color:subtitleColor, align:"center", valign:"top", wrap:true,
                    });
                }
                // Arrow to next card
                if (i < sCount - 1) {
                    slide.addText("→", {
                        x:cx + cardW + gap, y:cardY + cardH/2 - 0.22,
                        w:arrowW, h:0.44,
                        fontFace:"Arial", fontSize:20, bold:true,
                        color:accentColor, align:"center", valign:"middle",
                    });
                }
            });
            if (slideDef.notes) slide.addNotes(slideDef.notes);
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── quote ─────────────────────────────────────────────────────────────
        // quote: {layout:"quote", quote:"...", attribution:"Name, Title"}
        case "quote": {
            if (slideDef.title) addTitleBar(slide, pptx, style, slideDef.title);
            const qY = slideDef.title ? 1.1 : 0.3;

            // Large decorative quote mark
            slide.addText("“", {
                x:0.25, y:qY, w:1.2, h:1.2,
                fontFace:titleFont, fontSize:96, bold:true,
                color:accentColor, alpha:30,
                valign:"top",
            });

            // Quote body
            slide.addText(slideDef.quote || "", {
                x:0.7, y:qY + 0.7, w:8.6, h:contentMaxH - qY - 1.5,
                fontFace:bodyFont, fontSize:23, italic:true,
                color:bodyText, valign:"middle", align:"center", wrap:true,
            });

            // Attribution
            if (slideDef.attribution) {
                slide.addText("— " + slideDef.attribution, {
                    x:0.7, y:contentMaxH - 0.7, w:8.6, h:0.45,
                    fontFace:bodyFont, fontSize:16, bold:true,
                    color:accentColor, align:"right",
                });
            }

            if (slideDef.notes) slide.addNotes(slideDef.notes);
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── image_text ────────────────────────────────────────────────────────
        case "image_text": {
            addTitleBar(slide, pptx, style, slideDef.title);
            if (slideDef.image_path && fs.existsSync(slideDef.image_path)) {
                slide.addImage({ path:slideDef.image_path, x:0.4, y:1.3, w:4.5, h:contentMaxH - 1.45 });
            } else if (slideDef.image_path) {
                slide.addText(`[Image: ${path.basename(slideDef.image_path)}]`, {
                    x:0.4, y:1.3, w:4.5, h:contentMaxH - 1.45,
                    fontFace:bodyFont, fontSize:14, color:"999999",
                    align:"center", valign:"middle",
                });
            }
            if (slideDef.body) {
                let tY = 1.3;
                tY += addEyebrow(slide, style, slideDef.eyebrow, tY);
                slide.addText(slideDef.body, {
                    x:5.15, y:tY, w:4.5, h:contentMaxH - tY - 0.1,
                    fontFace:bodyFont, fontSize:bodySize,
                    color:bodyText, valign:"top", wrap:true,
                });
            }
            if (slideDef.notes) slide.addNotes(slideDef.notes);
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }

        // ── blank / default ───────────────────────────────────────────────────
        case "blank":
        default: {
            if (slideDef.title) addTitleBar(slide, pptx, style, slideDef.title);
            let bY = slideDef.title ? 1.25 : 0.4;
            bY += addEyebrow(slide, style, slideDef.eyebrow, bY);
            const bH = contentMaxH - bY - 0.1;
            if (slideDef.body) {
                slide.addText(slideDef.body, {
                    x:0.4, y:bY, w:9.2, h:bH,
                    fontFace:bodyFont, fontSize:bodySize,
                    color:bodyText, valign:"top", wrap:true,
                });
            }
            addFooter(slide, pptx, style, slideDef.section, pageNum, totalPages);
            break;
        }
    }

    return slide;
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function generate(inputPath, outputPath) {
    const raw = JSON.parse(fs.readFileSync(inputPath, "utf8"));

    const presetName = (raw.style && raw.style.preset) || "corporate";
    if (!PRESETS[presetName])
        throw new Error(`Unknown preset "${presetName}". Valid: ${Object.keys(PRESETS).join(", ")}`);

    const style = Object.assign({}, PRESETS[presetName], raw.style || {});
    for (const k of ["accentColor","accentText","bodyBg","bodyText","subtitleColor","cardBg"])
        if (style[k]) style[k] = stripHash(style[k]);

    const warnings = validateStyle(style);
    if (warnings.length) console.warn("WARNINGS:\n" + warnings.map(w => "  " + w).join("\n"));

    const pptx = new PptxGenJS();
    pptx.layout = "LAYOUT_WIDE";
    pptx.author = "MCP Server";
    pptx.title  = raw.title || "";

    const slides = raw.slides || [];
    const total  = slides.length;

    slides.forEach((slideDef, idx) => buildSlide(pptx, slideDef, style, idx + 1, total));

    await pptx.writeFile({ fileName: outputPath });

    if (warnings.length) console.log(`WARNINGS:${warnings.join("|")}`);
    console.log(`OK:${outputPath}:${total}:${presetName}`);
}

// ── Test mode ─────────────────────────────────────────────────────────────────

async function runTest() {
    const testInput = {
        title: "MCP Server 簡報測試",
        style: {
            preset: "aurora",
            titleFont: "Arial",
            bodyFont: "Arial",
            show_footer: true,
        },
        slides: [
            { layout:"title", title:"MCP Server 功能展示", subtitle:"12 種版型 · 8 種 Preset · pptxgenjs OOXML · 文字永遠可編輯" },
            { layout:"agenda", title:"今日議程", section:"目錄",
              items:["系統架構總覽","效能數據","方案比較","新版型展示：agenda / big_message / timeline / process"],
              active_item:1 },
            { layout:"section", title:"第一章：系統架構", subtitle:"資料庫 · 檔案系統 · 簡報工具", icon:"server", section:"架構總覽" },
            { layout:"content", title:"工具清單",
              eyebrow:"核心功能", icon:"settings",
              section:"架構總覽",
              bullets:[
                  "資料庫查詢（db_query / db_execute）支援 SQLite 與 PostgreSQL",
                  "  自動偵測 schema，支援多資料庫切換",
                  "檔案系統工具：讀寫、搜尋、刪除，限制在允許路徑內",
                  "簡報生成：直接寫入 OOXML，文字永遠可編輯",
                  "  12 種版型，8 種 preset，支援 icon · footer · eyebrow",
              ],
              notes:"演講者備忘：強調可編輯 OOXML 是關鍵優勢，不同於截圖式方案。" },
            { layout:"stats", title:"系統效能指標",
              section:"效能數據",
              stats:[
                  { value:"20",  label:"MCP 工具數",   icon:"zap",          desc:"涵蓋 DB、FS、簡報、通用四大類" },
                  { value:"8",   label:"簡報 Preset",  icon:"star",         desc:"aurora · bright_sans · warm 等" },
                  { value:"63",  label:"內建圖示",      icon:"check",        desc:"Lucide ISC 授權 SVG bundle" },
                  { value:"<1s", label:"生成時間",      icon:"trending-up",  desc:"單次呼叫完成整份簡報" },
              ] },
            { layout:"two_column", title:"方案比較",
              section:"設計決策",
              left_title:"pptxgenjs（現行）", left_icon:"check",
              left:["直接寫 OOXML，文字可編輯","精確字型與座標控制","不依賴螢幕截圖","Node.js 環境即可執行"],
              right_title:"截圖式方案", right_icon:"x",
              right:["文字變成圖片，無法搜尋","字型渲染依賴系統環境","檔案體積大","需要完整瀏覽器環境"],
              notes:"截圖式方案的最大問題是無法在 PowerPoint 內編輯文字。" },
            { layout:"big_message", title:"", section:"核心訴求",
              message:"文字永遠可編輯", supporting:"OOXML 結構直接產生，PowerPoint 開啟即可修改每個元素。" },
            { layout:"timeline", title:"功能演進時程",
              section:"發展歷程",
              events:[
                  { date:"2025 Q4", label:"基礎架構",    desc:"DB + FS 工具上線",         icon:"server" },
                  { date:"2026 Q1", label:"簡報工具",    desc:"8 種版型、icon 支援",       icon:"star" },
                  { date:"2026 Q2", label:"版型升級",    desc:"12 種版型含 timeline/process", icon:"rocket" },
                  { date:"2026 Q3", label:"多語言支援",  desc:"CJK 字型自動偵測",          icon:"globe" },
              ] },
            { layout:"process", title:"簡報產出流程",
              section:"使用流程",
              steps:[
                  { label:"選擇主題",   desc:"確認受眾、頁數、deck_type", icon:"target" },
                  { label:"規劃大綱",   desc:"呼叫 plan_presentation_outline()", icon:"clipboard" },
                  { label:"填入內容",   desc:"依 guidance 填寫每頁完整內容", icon:"file-text" },
                  { label:"產出簡報",   desc:"呼叫 create_presentation()", icon:"rocket" },
              ] },
            { layout:"quote",
              quote:"The slide framework built for agents — but we export real OOXML, not screenshots.",
              attribution:"MCP Server · 2026",
              section:"設計理念" },
            { layout:"blank", title:"結語", section:"結尾",
              body:`本次展示涵蓋所有 8 種 preset 主題及 12 種版型（含新增的 agenda / big_message / timeline / process）。footer、eyebrow 標籤、stats KPI 卡片、icon（${ICONS.size} 個 Lucide SVG）均已實作。文字全部可在 PowerPoint 內直接編輯。` },
        ],
    };
    const tmp = path.join(process.cwd(), "_test_input.json");
    const out = path.join(process.cwd(), "test_output.pptx");
    fs.writeFileSync(tmp, JSON.stringify(testInput, null, 2));
    await generate(tmp, out);
    fs.unlinkSync(tmp);
    console.log(`Test file written: ${out}`);
}

// ── Entry ─────────────────────────────────────────────────────────────────────

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
