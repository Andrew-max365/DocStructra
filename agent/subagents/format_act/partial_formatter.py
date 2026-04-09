# core/partial_formatter.py
"""
局部/定向排版模块（Feature 2）。

apply_partial_format(doc, overrides) → report_dict
  - 只将 overrides 中指定的属性应用到文档，其他格式保持不变。
  - 适用于"只改行间距"、"只改正文字号"等精准指令。
"""
from __future__ import annotations

from typing import Any, Dict, List

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_LINE_SPACING, WD_ALIGN_PARAGRAPH, WD_BREAK

from agent.subagents.ingest_parse.docx_utils import iter_paragraph_runs, iter_all_paragraphs, set_run_fonts
from agent.subagents.format_act.formatter import detect_role

_DEFAULT_FONT_SIZE_PT = 12.0  # 默认正文字号（pt）


# ─────────────────────────────────────────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_alignment(align_str: str):
    mapping = {
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    return mapping.get((align_str or "justify").strip().lower(), None)


def _apply_line_spacing(paragraph, line_spacing: float) -> None:
    """应用行距：<5 为倍数行距，≥5 为固定值（pt）。"""
    pf = paragraph.paragraph_format
    if line_spacing < 5.0:
        pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
        pf.line_spacing = line_spacing
    else:
        pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        pf.line_spacing = Pt(line_spacing)


def _apply_font_to_runs(
    paragraph,
    zh_font: str = None,
    en_font: str = None,
    size_pt: float = None,
    bold: bool = None,
    italic: bool = None,
    color_hex: str = None,
) -> None:
    """对段落内的所有 run 应用字体属性（只改指定属性，不覆盖未指定的）。"""
    from docx.shared import RGBColor
    for run in iter_paragraph_runs(paragraph):
        if size_pt is not None:
            run.font.size = Pt(size_pt)
        if bold is not None:
            run.font.bold = bold
        if italic is not None:
            run.font.italic = italic
        if color_hex:
            try:
                h = color_hex.lstrip("#")
                run.font.color.rgb = RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            except Exception:
                pass
        if zh_font or en_font:
            set_run_fonts(
                run,
                zh_font=zh_font or None,
                en_font=en_font or None,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 公共接口
# ─────────────────────────────────────────────────────────────────────────────

def apply_partial_format(
    doc: Document,
    overrides: Dict[str, Any],
) -> Dict[str, Any]:
    """
    只将 overrides 中明确指定的属性应用到对应角色的段落，其余格式保持不变。

    :param doc:       python-docx Document 对象
    :param overrides: 与 spec overrides 格式相同，只包含要修改的字段
    :return:          简单的操作报告字典
    """
    report: Dict[str, Any] = {
        "mode": "partial",
        "applied": {},
        "counts": {},
    }

    if not overrides:
        report["applied"]["nothing"] = True
        return report

    body_cfg = overrides.get("body", {})
    heading_cfg = overrides.get("heading", {})
    page_cfg = overrides.get("page", {})
    fonts_cfg = overrides.get("fonts", {})

    # 全局字体配置
    global_zh = fonts_cfg.get("zh")
    global_en = fonts_cfg.get("en")

    # 计数器
    counts: Dict[str, int] = {}

    all_paras_list = list(iter_all_paragraphs(doc))
    has_body_tag = any("{body}" in (p.text or "") for p in all_paras_list)
    in_body_range = not has_body_tag # 如果没有标签，默认就在正文中
    
    to_delete_tags = []

    for p in all_paras_list:
        text_clean = (p.text or "").strip()
        
        # 识别边界标签
        if "{body}" in text_clean:
            in_body_range = True
            to_delete_tags.append(p)
            continue
        if "{/body}" in text_clean:
            in_body_range = False
            to_delete_tags.append(p)
            continue

        if not in_body_range:
            continue

        role = detect_role(p)
        text = (p.text or "").strip()
        if not text:
            continue

        # ── 正文段落 ────────────────────────────────────────────────────────
        if role in ("body", "unknown", "list_item", "abstract", "keyword",
                    "reference", "footer", "caption"):
            target_cfg = dict(body_cfg)
            # 对于非正文角色，从 overrides 中获取对应角色的配置（若有）
            role_override = overrides.get(role, {})
            target_cfg.update(role_override)

            if not target_cfg:
                continue

            changed = False
            if "line_spacing" in target_cfg:
                _apply_line_spacing(p, float(target_cfg["line_spacing"]))
                changed = True
            if "space_before_pt" in target_cfg:
                p.paragraph_format.space_before = Pt(float(target_cfg["space_before_pt"]))
                changed = True
            if "space_after_pt" in target_cfg:
                p.paragraph_format.space_after = Pt(float(target_cfg["space_after_pt"]))
                changed = True
            if "first_line_chars" in target_cfg:
                flc = int(target_cfg["first_line_chars"])
                size = float(target_cfg.get("font_size_pt", _DEFAULT_FONT_SIZE_PT))
                if flc:
                    p.paragraph_format.first_line_indent = Pt(flc * size)
                else:
                    p.paragraph_format.first_line_indent = Pt(0)
                changed = True
            if "alignment" in target_cfg:
                al = _resolve_alignment(target_cfg["alignment"])
                if al is not None:
                    p.paragraph_format.alignment = al
                changed = True

            run_kwargs: Dict[str, Any] = {}
            if "font_size_pt" in target_cfg:
                run_kwargs["size_pt"] = float(target_cfg["font_size_pt"])
            if "font_name" in target_cfg:
                run_kwargs["zh_font"] = target_cfg["font_name"]
                run_kwargs["en_font"] = target_cfg["font_name"]
            if "bold" in target_cfg:
                run_kwargs["bold"] = bool(target_cfg["bold"])
            if "italic" in target_cfg:
                run_kwargs["italic"] = bool(target_cfg["italic"])
            if "color" in target_cfg:
                run_kwargs["color_hex"] = target_cfg["color"]
            if global_zh:
                run_kwargs.setdefault("zh_font", global_zh)
            if global_en:
                run_kwargs.setdefault("en_font", global_en)

            if run_kwargs:
                _apply_font_to_runs(p, **run_kwargs)
                changed = True

            if changed:
                counts[role] = counts.get(role, 0) + 1

        # ── 标题段落 ────────────────────────────────────────────────────────
        elif role in ("h1", "h2", "h3"):
            h_cfg = heading_cfg.get(role, {}) if isinstance(heading_cfg, dict) else {}
            # 若 heading 配置中有通配符，也合并
            h_all = heading_cfg.get("all", {}) if isinstance(heading_cfg, dict) else {}
            merged = {**h_all, **h_cfg}

            if not merged:
                continue

            changed = False
            if "line_spacing" in merged:
                _apply_line_spacing(p, float(merged["line_spacing"]))
                changed = True
            if "space_before_pt" in merged:
                p.paragraph_format.space_before = Pt(float(merged["space_before_pt"]))
                changed = True
            if "space_after_pt" in merged:
                p.paragraph_format.space_after = Pt(float(merged["space_after_pt"]))
                changed = True
            if "alignment" in merged:
                al = _resolve_alignment(merged["alignment"])
                if al is not None:
                    p.paragraph_format.alignment = al
                changed = True

            run_kwargs = {}
            if "font_size_pt" in merged:
                run_kwargs["size_pt"] = float(merged["font_size_pt"])
            if "font_name" in merged:
                run_kwargs["zh_font"] = merged["font_name"]
                run_kwargs["en_font"] = merged["font_name"]
            if "bold" in merged:
                run_kwargs["bold"] = bool(merged["bold"])
            if "italic" in merged:
                run_kwargs["italic"] = bool(merged["italic"])
            if "color" in merged:
                run_kwargs["color_hex"] = merged["color"]
            if global_zh:
                run_kwargs.setdefault("zh_font", global_zh)
            if global_en:
                run_kwargs.setdefault("en_font", global_en)

            if run_kwargs:
                _apply_font_to_runs(p, **run_kwargs)
                changed = True

            if changed:
                counts[role] = counts.get(role, 0) + 1

    # ── 页面属性 ─────────────────────────────────────────────────────────────
    if page_cfg:
        from docx.shared import Cm
        for sec in doc.sections:
            margins = page_cfg.get("margins_cm", {})
            if isinstance(margins, dict):
                if "top" in margins:
                    sec.top_margin = Cm(float(margins["top"]))
                if "bottom" in margins:
                    sec.bottom_margin = Cm(float(margins["bottom"]))
                if "left" in margins:
                    sec.left_margin = Cm(float(margins["left"]))
                if "right" in margins:
                    sec.right_margin = Cm(float(margins["right"]))
            if "header_distance_cm" in page_cfg:
                sec.header_distance = Cm(float(page_cfg["header_distance_cm"]))
            if "footer_distance_cm" in page_cfg:
                sec.footer_distance = Cm(float(page_cfg["footer_distance_cm"]))
        counts["page_sections"] = len(doc.sections)

    report["counts"] = counts
    report["applied"] = {k: True for k in overrides.keys()}
    # 清理标记段落并插入分页符
    final_paras = list(iter_all_paragraphs(doc))
    from agent.subagents.ingest_parse.docx_utils import delete_paragraph
    for p in final_paras:
        try:
            t = (p.text or "").strip()
            if "{body}" in t:
                delete_paragraph(p)
            elif "{/body}" in t:
                # 在正文结束处确保分页
                p.text = ""
                p.add_run().add_break(WD_BREAK.PAGE)
        except Exception:
            pass

    return report
