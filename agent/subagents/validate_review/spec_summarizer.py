# agent/spec_summarizer.py
"""
Spec 智能摘要器：将 YAML 排版规范自动提炼为自然语言，
供视觉审查的多模态 LLM 理解"应该按什么标准打分"。
"""
from __future__ import annotations

from typing import Any, Dict

from core.spec import Spec


# ---------------------------------------------------------------------------
# 对齐方式映射
# ---------------------------------------------------------------------------

_ALIGNMENT_ZH = {
    "left": "左对齐",
    "right": "右对齐",
    "center": "居中",
    "justify": "两端对齐",
}


def _align(name: str) -> str:
    return _ALIGNMENT_ZH.get(name.strip().lower(), name)


# ---------------------------------------------------------------------------
# 核心摘要函数
# ---------------------------------------------------------------------------

def summarize_spec(spec: Spec) -> str:
    """
    将 Spec 对象转换为简洁的中文自然语言描述，覆盖排版审查重点维度。

    输出示例：
      "字体：中文宋体 / 英文 Times New Roman。
       正文 12pt，1.5 倍行距，首行缩进 2 字符，两端对齐。
       一级标题 16pt 加粗居中，段前 12pt，段后 6pt。…"

    :param spec: Spec 对象（已校验与填充默认值）
    :return: 自然语言摘要字符串
    """
    raw: Dict[str, Any] = spec.raw
    parts: list[str] = []

    # ── 字体 ──
    fonts = raw.get("fonts", {})
    zh = fonts.get("zh", "宋体")
    en = fonts.get("en", "Times New Roman")
    parts.append(f"字体：中文 {zh} / 英文 {en}")

    # ── 正文 ──
    body = raw.get("body", {})
    body_desc = _describe_body(body, raw)
    parts.append(f"正文：{body_desc}")

    # ── 标题层级 ──
    heading = raw.get("heading", {})
    for level in ("h1", "h2", "h3"):
        h = heading.get(level, {})
        if h:
            level_name = {"h1": "一级标题", "h2": "二级标题", "h3": "三级标题"}[level]
            parts.append(f"{level_name}：{_describe_heading(h)}")

    # ── 段落对齐 ──
    para = raw.get("paragraph", {})
    alignment = para.get("alignment", "justify")
    parts.append(f"段落默认对齐：{_align(alignment)}")

    # ── 题注 ──
    caption = raw.get("caption", {})
    if caption:
        cap_size = caption.get("font_size_pt", "")
        cap_align = _align(caption.get("alignment", "center"))
        parts.append(f"题注：{cap_size}pt {cap_align}")

    # ── 空行清理 ──
    cleanup = raw.get("cleanup", {})
    if cleanup.get("remove_all_blank_paragraphs"):
        parts.append("空行策略：删除所有空段落")
    elif "max_consecutive_blank_paragraphs" in cleanup:
        max_blank = cleanup["max_consecutive_blank_paragraphs"]
        parts.append(f"空行策略：最多保留 {max_blank} 个连续空段")

    return "。\n".join(parts) + "。"


def _describe_body(body: dict, raw: dict) -> str:
    """描述正文格式。"""
    segs: list[str] = []

    size = body.get("font_size_pt")
    if size:
        segs.append(f"{size}pt")

    ls = body.get("line_spacing")
    if ls:
        segs.append(f"{ls} 倍行距")

    flc = body.get("first_line_chars")
    if flc:
        segs.append(f"首行缩进 {flc} 字符")

    sb = body.get("space_before_pt", 0)
    sa = body.get("space_after_pt", 0)
    if sb or sa:
        segs.append(f"段前 {sb}pt / 段后 {sa}pt")

    alignment = raw.get("paragraph", {}).get("alignment", "justify")
    segs.append(_align(alignment))

    return "，".join(segs)


def _describe_heading(h: dict) -> str:
    """描述单个标题层级的格式。"""
    segs: list[str] = []

    size = h.get("font_size_pt")
    if size:
        segs.append(f"{size}pt")

    if h.get("bold"):
        segs.append("加粗")

    alignment = h.get("alignment")
    if alignment:
        segs.append(_align(alignment))

    sb = h.get("space_before_pt")
    sa = h.get("space_after_pt")
    if sb is not None or sa is not None:
        segs.append(f"段前 {sb or 0}pt / 段后 {sa or 0}pt")

    return "，".join(segs)
