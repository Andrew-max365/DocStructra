# core/locate_formatter.py
"""
定位并重排特定内容模块（Feature 4）。

locate_and_reformat(doc, locate_text, format_action, overrides) → dict
  - 在文档中搜索包含 locate_text 的段落
  - 根据 format_action 决定：
      "match_context"  → 取周围段落的多数格式，应用到匹配段落
      "explicit"       → 使用 overrides 中指定的格式
  - 返回操作报告
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_LINE_SPACING, WD_ALIGN_PARAGRAPH

_EMU_PER_PT = 12700  # EMU（English Metric Units）每磅的转换系数


# ─────────────────────────────────────────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────────────────────────────────────────

def _text_similarity(a: str, b: str) -> float:
    """简单的文本相似度（基于公共字符覆盖率）。"""
    if not a or not b:
        return 0.0
    a_clean = re.sub(r"\s+", "", a)
    b_clean = re.sub(r"\s+", "", b)
    if not a_clean or not b_clean:
        return 0.0
    shorter = a_clean if len(a_clean) <= len(b_clean) else b_clean
    longer = b_clean if len(a_clean) <= len(b_clean) else a_clean
    matches = sum(1 for ch in shorter if ch in longer)
    return matches / max(len(shorter), 1)


def _find_matching_paragraphs(
    paragraphs: List[Any],
    locate_text: str,
    threshold: float = 0.4,
) -> List[Tuple[int, Any]]:
    """
    在段落列表中查找包含 locate_text 的段落，返回 (index, paragraph) 列表。
    先精确子串匹配，再模糊相似度匹配。
    """
    results: List[Tuple[int, Any]] = []
    locate_clean = re.sub(r"\s+", "", locate_text or "")

    for idx, p in enumerate(paragraphs):
        text = (p.text or "").strip()
        text_clean = re.sub(r"\s+", "", text)
        if not text_clean:
            continue
        # 精确子串匹配
        if locate_clean and locate_clean in text_clean:
            results.append((idx, p))
        # 模糊相似度匹配（用于处理引号/换行差异）
        elif _text_similarity(locate_clean, text_clean) >= threshold:
            results.append((idx, p))

    return results


def _get_context_format(
    paragraphs: List[Any],
    target_indices: List[int],
    context_window: int = 5,
) -> Dict[str, Any]:
    """
    从目标段落周围（window 段内）提取正文段落的格式作为"上下文格式"。
    返回 {"font_size_pt": ..., "line_spacing": ..., "bold": ..., "alignment": ...}
    """
    from collections import Counter

    context_paras = []
    for idx in target_indices:
        start = max(0, idx - context_window)
        end = min(len(paragraphs), idx + context_window + 1)
        for i in range(start, end):
            if i not in target_indices:
                p = paragraphs[i]
                role = detect_role(p)
                if role in ("body", "h1", "h2", "h3", "list_item"):
                    context_paras.append((i, p, role))

    if not context_paras:
        return {}

    # 找到与目标段落 role 最接近的上下文段落
    # 取众数角色的段落
    role_counter = Counter(role for _, _, role in context_paras)
    dominant_role = role_counter.most_common(1)[0][0]
    dominant_paras = [(i, p) for i, p, role in context_paras if role == dominant_role]

    # 从这些段落提取格式
    font_sizes = []
    line_spacings = []
    bold_vals = []
    alignments = []

    for _, p in dominant_paras:
        for run in p.runs:
            if run.text.strip():
                if run.font.size:
                    font_sizes.append(run.font.size.pt)
                if run.font.bold is not None:
                    bold_vals.append(run.font.bold)
                break
        pf = p.paragraph_format
        if pf.line_spacing is not None:
            try:
                ls = float(pf.line_spacing)
                if pf.line_spacing_rule == WD_LINE_SPACING.MULTIPLE:
                    line_spacings.append(ls)
                elif pf.line_spacing_rule == WD_LINE_SPACING.EXACTLY:
                    line_spacings.append(ls / _EMU_PER_PT)  # EMU → pt
            except Exception:
                pass
        if p.alignment is not None:
            align_map = {
                WD_ALIGN_PARAGRAPH.CENTER: "center",
                WD_ALIGN_PARAGRAPH.LEFT: "left",
                WD_ALIGN_PARAGRAPH.RIGHT: "right",
                WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
            }
            al = align_map.get(p.alignment)
            if al:
                alignments.append(al)

    fmt: Dict[str, Any] = {}
    if font_sizes:
        from statistics import median
        fmt["font_size_pt"] = median(font_sizes)
    if line_spacings:
        from statistics import median
        fmt["line_spacing"] = median(line_spacings)
    if bold_vals:
        fmt["bold"] = Counter(bold_vals).most_common(1)[0][0]
    if alignments:
        fmt["alignment"] = Counter(alignments).most_common(1)[0][0]

    # 同时检查 role，便于报告
    fmt["_context_role"] = dominant_role
    return fmt


def _apply_format_to_paragraph(
    p: Any,
    fmt: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    将 fmt 字典中的格式应用到段落 p。
    :param fmt:       上下文格式或用户指定格式
    :param overrides: 额外 overrides（优先级高于 fmt）
    :return: 是否有实际改动
    """
    from docx.shared import RGBColor

    merged = dict(fmt)
    if overrides:
        merged.update(overrides)

    changed = False

    if "line_spacing" in merged:
        ls = float(merged["line_spacing"])
        pf = p.paragraph_format
        if ls < 5.0:
            pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
            pf.line_spacing = ls
        else:
            pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
            pf.line_spacing = Pt(ls)
        changed = True

    if "space_before_pt" in merged:
        p.paragraph_format.space_before = Pt(float(merged["space_before_pt"]))
        changed = True

    if "space_after_pt" in merged:
        p.paragraph_format.space_after = Pt(float(merged["space_after_pt"]))
        changed = True

    if "alignment" in merged:
        mapping = {
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
            "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
        }
        al = mapping.get(str(merged["alignment"]).lower())
        if al is not None:
            p.paragraph_format.alignment = al
            changed = True

    # run-level 属性
    for run in iter_paragraph_runs(p):
        if "font_size_pt" in merged:
            run.font.size = Pt(float(merged["font_size_pt"]))
            changed = True
        if "bold" in merged and merged["bold"] is not None:
            run.font.bold = bool(merged["bold"])
            changed = True
        if "italic" in merged and merged["italic"] is not None:
            run.font.italic = bool(merged["italic"])
            changed = True
        if "color" in merged and merged["color"]:
            try:
                h = str(merged["color"]).lstrip("#")
                run.font.color.rgb = RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                changed = True
            except Exception:
                pass
        zh = merged.get("font_name") or merged.get("zh_font")
        en = merged.get("font_name") or merged.get("en_font")
        if zh or en:
            set_run_fonts(run, zh_font=zh, en_font=en)
            changed = True

    return changed


# ─────────────────────────────────────────────────────────────────────────────
# 公共接口
# ─────────────────────────────────────────────────────────────────────────────

def locate_and_reformat(
    doc: Document,
    locate_text: str,
    format_action: str = "match_context",
    overrides: Optional[Dict[str, Any]] = None,
    context_window: int = 5,
) -> Dict[str, Any]:
    """
    在文档中定位包含 locate_text 的段落，并应用新格式。

    :param doc:            python-docx Document 对象
    :param locate_text:    要搜索的文字片段
    :param format_action:  "match_context"（匹配上下文格式）或 "explicit"（显式指定格式）
    :param overrides:      显式格式 overrides（format_action=="explicit" 时使用）
    :param context_window: 上下文窗口大小（查找周围几段的格式）
    :return: 操作报告 dict
    """
    report: Dict[str, Any] = {
        "locate_text": locate_text,
        "matched_paragraphs": [],
        "context_format": {},
        "applied_format": {},
        "changed_count": 0,
        "message": "",
    }

    paragraphs = iter_all_paragraphs(doc)

    # 1. 搜索匹配段落
    matched = _find_matching_paragraphs(paragraphs, locate_text)
    if not matched:
        report["message"] = f"未在文档中找到包含「{locate_text[:30]}」的段落。"
        return report

    matched_indices = [idx for idx, _ in matched]
    report["matched_paragraphs"] = [
        {"index": idx, "text": (p.text or "")[:80]}
        for idx, p in matched
    ]

    # 2. 确定要应用的格式
    if format_action == "match_context":
        ctx_fmt = _get_context_format(paragraphs, matched_indices, context_window)
        apply_fmt = ctx_fmt
        report["context_format"] = {k: v for k, v in ctx_fmt.items() if not k.startswith("_")}
    else:
        apply_fmt = {}

    # 合并 overrides（用户显式指定的优先）
    extra_fmt: Dict[str, Any] = {}
    if overrides:
        # 将 spec overrides 结构展平到 apply_fmt 格式
        body_ov = overrides.get("body", {})
        extra_fmt.update(body_ov)

    report["applied_format"] = {**apply_fmt, **extra_fmt}
    if not report["applied_format"]:
        # 提取失败或没有可应用的格式
        report["message"] = (
            f"找到 {len(matched)} 个匹配段落，但无法从上下文中推断目标格式，"
            "请提供具体的格式参数（如字号、行距等）。"
        )
        return report

    # 3. 应用格式
    changed = 0
    for idx, p in matched:
        if _apply_format_to_paragraph(p, apply_fmt, extra_fmt):
            changed += 1

    report["changed_count"] = changed
    report["message"] = (
        f"已在文档中找到 {len(matched)} 个匹配段落，"
        f"成功应用了 {changed} 处格式调整。"
        + (f" 参考上下文角色：{apply_fmt.get('_context_role', '未知')}" if format_action == "match_context" else "")
    )
    return report
