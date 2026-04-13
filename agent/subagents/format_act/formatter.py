# core/formatter.py
import re
import logging
logger = logging.getLogger(__name__)
from typing import Dict, List, Set
from collections import Counter

from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_LINE_SPACING, WD_BREAK
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph

from agent.subagents.ingest_parse.api import Block
from .spec import Spec
from agent.subagents.ingest_parse.api import (
    copy_run_style,
    delete_paragraph,
    is_drawing_paragraph,
    is_pure_drawing_paragraph,
    is_effectively_blank_paragraph,
    iter_all_paragraphs,
    iter_paragraph_runs,
    normalize_mixed_runs,
    set_run_fonts,
)
from .numbering import (
    convert_text_lists,
    detect_text_list_prefix,
    create_list_num_id,
    apply_numpr,
    strip_list_text_prefix,
)


# =========================
# Regex rules
# =========================

RE_SUBTITLE_CN = re.compile(r"^\s*（[一二三四五六七八九十]+）")  # （一）
RE_CAPTION = re.compile(
    r"^\s*(图|表|Figure|Fig\.|Table)\s*[\d一二三四五六七八九十]+(?:[\-–—]\d+)?(?:[\.．:：\s\t]|$)",
    re.IGNORECASE
)

RE_CN_ENUM = re.compile(r"^\s*[一二三四五六七八九十百千万]+、")  # 接受所有中文数字字符前缀（含百千万），不强制校验组合合法性
RE_NUM_DOT = re.compile(r"^\s*\d+(\.\d+){0,3}\s+")
RE_ABSTRACT = re.compile(r"^\s*(摘要|abstract)\s*[:：]?\s*", re.IGNORECASE)
RE_KEYWORD = re.compile(r"^\s*(关键词|关键字|keywords?)\s*[:：]?\s*", re.IGNORECASE)
RE_REFERENCE = re.compile(r"^\s*(参考文献|references?|bibliography)\s*$", re.IGNORECASE)
RE_TOC_HEADING = re.compile(r"^\s*(目录|contents?)\s*$", re.IGNORECASE)
RE_REQUIREMENT_HEADING = re.compile(r"^\s*(课程要求|作业要求|实验要求|考核要求|内容要求)\s*[:：]?\s*$", re.IGNORECASE)
ROLE_LABELS_FALLBACK_TO_RULE = {"blank", "unknown"}
COVER_MAX_LENGTH = 20
COVER_KEYWORDS = ("报告", "论文", "课程设计", "课程", "作业")

# 正文层级列表标记（不是章节标题，而是段落内编号列表项）
# 注意：RE_SUBTITLE_CN 已捕获 （一） 形式（中文括号数字），这里只捕获阿拉伯数字括号/括号后缀/圈数字/英文字母
RE_BODY_LIST_PAREN_ARABIC = re.compile(r"^\s*（\d+）")          # （1） （2）
RE_BODY_LIST_RPAREN = re.compile(r"^\s*\d+[)）]")               # 1) 或 1）（不强制要求后接空格，兼容中文文档无空格写法）
RE_BODY_LIST_ENCLOSED = re.compile(                              # ①②…⑳
    r"^\s*[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]"
)
RE_BODY_LIST_ALPHA = re.compile(r"^\s*[a-zA-Z][.)]\s", re.ASCII)  # a. A. a) A)
RE_BODY_LIST_NUM_DOT = re.compile(r"^\s*\d+\.\s")                 # 1. 2. 3. (single-level)

# 段内多行结构（避免误判标题）
# 匹配嵌入换行后的数字编号（1 text、1. text、1.1 text 等），将整段保留为 body 以便后续拆段
RE_MULTILINE_NUM = re.compile(r"\n\s*\d+(?:\.\d+)*\.?\s+")
RE_MULTILINE_SUB = re.compile(r"\n\s*（[一二三四五六七八九十]+）")
RE_SOFT_LINEBREAK = re.compile(r"[\n\r\v]")

# 表格单元格内联列表分隔符：匹配 ；或 ; 后紧跟列表标记（用于把 "1)项一；2)项二" 拆成独立段落）
_RE_INLINE_LIST_SEP = re.compile(
    r'[；;]'                                                              # 全角或半角分号
    r'(?=\s*(?:'
    r'\d+[)）]'                                                           # rparen: 2) 或 2）
    r'|（\d+）'                                                           # paren_arabic: （2）
    r'|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]'                                        # enclosed
    r'|\d+\. '                                                            # num_dot (需要尾随空格)
    r'|[a-zA-Z][.)]\s'                                                    # alpha
    r'))'
)


# =========================
# Helpers
# =========================

def is_list_paragraph(p: Paragraph) -> bool:
    """True if paragraph is a Word numbered/bulleted list (numPr)."""
    try:
        ppr = p._p.pPr
        return bool(ppr is not None and getattr(ppr, 'numPr', None) is not None)
    except Exception:
        return False

def _is_in_table_cell(p: Paragraph) -> bool:
    """True if paragraph lives inside a table cell (w:tc element)."""
    from docx.oxml.ns import qn as _qn
    if p._p is None:
        return False
    parent = p._p.getparent()
    return parent is not None and parent.tag == _qn("w:tc")



def _apply_page_layout(doc, page_cfg: dict) -> int:
    """Apply section-level page layout settings; returns changed section count."""
    if not page_cfg:
        return 0

    changed = 0
    for sec in getattr(doc, "sections", []):
        touched = False
        try:
            margins = page_cfg.get("margins_cm", {})
            if isinstance(margins, dict):
                top = margins.get("top")
                bottom = margins.get("bottom")
                left = margins.get("left")
                right = margins.get("right")
                if top is not None:
                    sec.top_margin = Cm(float(top)); touched = True
                if bottom is not None:
                    sec.bottom_margin = Cm(float(bottom)); touched = True
                if left is not None:
                    sec.left_margin = Cm(float(left)); touched = True
                if right is not None:
                    sec.right_margin = Cm(float(right)); touched = True

            if page_cfg.get("header_distance_cm") is not None:
                sec.header_distance = Cm(float(page_cfg["header_distance_cm"]))
                touched = True
            if page_cfg.get("footer_distance_cm") is not None:
                sec.footer_distance = Cm(float(page_cfg["footer_distance_cm"]))
                touched = True
        except Exception:
            pass
        if touched:
            changed += 1
    return changed
def _autofit_tables(doc) -> int:
    """Set all top-level tables to auto-fit window width. Returns count."""
    count = 0
    for table in doc.tables:
        try:
            table.autofit = True
            count += 1
        except Exception:
            pass
    return count


def _center_tables(doc) -> int:
    """Center all tables. Returns count of tables successfully processed."""
    count = 0
    for table in doc.tables:
        try:
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            count += 1
        except Exception:
            pass
    return count




def _detect_section_role(paragraphs: List[Paragraph], base_role_getter) -> Dict:
    """
    Detect section-level roles in document flow:
    - cover: content before first structural body section (skip formatting)
    - toc: table of contents heading and entries
    - requirement: 课程要求等章节
    """
    section_by_elem: Dict = {}
    current_scope = "body"

    def _is_toc_entry(p: Paragraph, text: str) -> bool:
        if p.style is not None and "toc" in (p.style.name or "").lower():
            return True
        if p._p.xpath(".//*[local-name()='instrText' and contains(., 'TOC')]"):
            return True
        # 匹配目录行：标题后跟导引点（ASCII 点号 ..... 或中文省略号 ……）并以页码结尾
        # 例如：1 绪论........1
        return bool(re.search(r"(?:\.{2,}|…{2,}).*\d+\s*$", text))

    def _is_likely_cover_paragraph(idx: int, base_role: str, text: str) -> bool:
        """Heuristic for cover-title line: first paragraph, body-like role, short title text."""
        if idx != 0:
            return False
        if base_role != "body":
            return False
        if not text:
            return False
        if len(text) > COVER_MAX_LENGTH:
            return False
        return any(k in text for k in COVER_KEYWORDS)

    for idx, p in enumerate(paragraphs):
        t = (p.text or "").strip()
        base_role = base_role_getter(p)

        # 1. 优先处理 cover
        if base_role == "cover" or _is_likely_cover_paragraph(idx, base_role, t):
            section_by_elem[p._p] = "cover"
            continue

        # 2. 改进的空行处理：空行不再打断状态，而是继承特殊状态
        if not t:
            if current_scope in {"toc", "requirement", "cover"}:
                section_by_elem[p._p] = current_scope
            else:
                section_by_elem[p._p] = "blank"
            continue

        # 3. 探测特殊区块的“入口”
        if RE_TOC_HEADING.match(t):
            current_scope = "toc"
            section_by_elem[p._p] = "toc"
            continue

        if RE_REQUIREMENT_HEADING.match(t):
            current_scope = "requirement"
            section_by_elem[p._p] = "requirement"
            continue

        # 4. 探测特殊区块的“明确出口”
        # 如果遇到了明确的一二级标题（比如“第一章 绪论”），说明目录或要求部分彻底结束了
        if base_role in {"h1", "h2"}:
            current_scope = "body"
            # 不要 continue，让底下的逻辑去记录这个 h1/h2 的原始 role

        # 5. 状态机维持逻辑（放宽容错）
        if current_scope == "toc":
            # 只要没遇到 h1/h2 退出信号，我们默认它都是目录的一部分
            section_by_elem[p._p] = "toc"
            continue

        if current_scope == "requirement":
            section_by_elem[p._p] = "requirement"
            continue

    return section_by_elem

def looks_like_multiline_numbered_block(text: str) -> bool:
    t = text or ""
    return bool(RE_MULTILINE_NUM.search(t)) or bool(RE_MULTILINE_SUB.search(t))


def _clear_paragraph_runs(p):
    """彻底清空 run（含 br），用于重建文本。"""
    for child in list(p._p):
        if child.tag.endswith("}r"):
            p._p.remove(child)


def _strip_trailing_newlines_in_paragraph(p):
    """去掉段尾多余换行。如果段落含图片则跳过，避免 _clear_paragraph_runs 吞掉图片。"""
    if is_drawing_paragraph(p):
        return
    txt = p.text or ""
    new_txt = txt.rstrip("\r\n")
    if new_txt == txt:
        return
    _clear_paragraph_runs(p)
    p.add_run(new_txt)


def _insert_paragraph_after(p, text: str):
    """在段落 p 后插入新段落（稳定版），并写入 text。"""
    new_p = OxmlElement('w:p')
    p._p.addnext(new_p)
    new_para = Paragraph(new_p, p._parent)
    new_para.add_run(text)
    return new_para


# =========================
# Role detection
# =========================

def detect_role(paragraph) -> str:
    """
    Detect the structural role of a paragraph.

    Returns one of:
      blank / h1 / h2 / h3 / caption / abstract / keyword / reference /
      list_item / footer / body

    Rules applied in priority order:
      1. blank (empty / whitespace-only)
      2. body  (multi-line numbered block – avoids misclassifying as heading)
      3. Word heading/footer styles
      4. Abstract / keyword / reference text patterns
      5. Caption text pattern (before list_item so captioned lists stay as caption)
      6. Word list paragraph (numPr)
      7. （一）-style sub-heading → h3
      8. 第X章/节/条 patterns → h1/h2/h3
      9. Chinese numeral enum (一、二、…) → h2
      10. Numeric outline (1. / 1.1) → h2/h3
      11. Fallback → body
    """
    if is_effectively_blank_paragraph(paragraph):
        return "blank"

    text = paragraph.text or ""

    # 段内多行编号块强制当正文，避免误判标题
    if looks_like_multiline_numbered_block(text):
        return "body"

    # 优先尊重 Word 标题样式
    style_name = ""
    try:
        style_name = (paragraph.style.name or "").lower()
    except Exception:
        style_name = ""
    if "heading 1" in style_name or "标题 1" in style_name:
        return "h1"
    if "heading 2" in style_name or "标题 2" in style_name:
        return "h2"
    if "heading 3" in style_name or "标题 3" in style_name:
        return "h3"
    if "footer" in style_name or "页脚" in style_name:
        return "footer"
    if "title" in style_name or "标题" == style_name.strip():
        return "cover"
    if "toc" in style_name or "目录" in style_name:
        return "toc"
    if "caption" in style_name or "题注" in style_name:
        return "caption"

    t = text.strip()

    if RE_ABSTRACT.match(t):
        return "abstract"
    if RE_KEYWORD.match(t):
        return "keyword"
    if RE_REFERENCE.match(t):
        return "reference"
    if RE_TOC_HEADING.match(t):
        return "toc"
    if RE_REQUIREMENT_HEADING.match(t):
        return "requirement"
    if RE_CAPTION.match(t):
        return "caption"
    if is_list_paragraph(paragraph):
        return "list_item"

    if RE_SUBTITLE_CN.match(t):
        return "h3"

    if t.startswith("第") and "章" in t[:12]:
        return "h1"
    if t.startswith("第") and "节" in t[:12]:
        return "h2"
    if t.startswith("第") and "条" in t[:12]:
        return "h3"
    if RE_CN_ENUM.match(t):
        return "h2"
    if RE_NUM_DOT.match(t):
        depth = t.split()[0].count(".")
        return "h2" if depth <= 0 else "h3"

    # 正文层级列表项（阿拉伯数字括号、括号后缀、圈数字、英文字母列表、单层数字点号）
    if (RE_BODY_LIST_PAREN_ARABIC.match(t)
            or RE_BODY_LIST_RPAREN.match(t)
            or RE_BODY_LIST_ENCLOSED.match(t)
            or RE_BODY_LIST_ALPHA.match(t)
            or RE_BODY_LIST_NUM_DOT.match(t)):
        return "list_item"

    return "body"


# =========================
# Formatting helpers
# =========================

def _apply_paragraph_common(p, line_spacing: float, space_before_pt: float, space_after_pt: float):
    pf = p.paragraph_format
    pf.space_before = Pt(space_before_pt)
    pf.space_after = Pt(space_after_pt)

    if line_spacing is not None:
        if line_spacing >= 5.0:
            # 必须使用 Pt() 包裹，并设置规则为 EXACTLY（固定值）
            pf.line_spacing = Pt(line_spacing)
            pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        else:
            # 直接赋值，并设置规则为 MULTIPLE（多倍行距）
            pf.line_spacing = line_spacing
            pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE


def _resolve_alignment(name: str):
    """将配置里的对齐字符串映射为 python-docx 枚举。"""
    mapping = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
        "both": WD_ALIGN_PARAGRAPH.JUSTIFY,
        "distributed": WD_ALIGN_PARAGRAPH.DISTRIBUTE,
    }
    return mapping.get((name or "").strip().lower())


def _apply_runs_font(p, zh_font: str, en_font: str, size_pt: float, force_bold=None, color_hex=None):
    # 先把中英混合 run 拆开，再逐 run 写入完整字体映射（含超链接内 run）
    normalize_mixed_runs(p)
    clean_hex = None
    if color_hex:
        clean_hex = str(color_hex).lstrip("#").upper()
    for run in iter_paragraph_runs(p):
        run.font.size = Pt(size_pt)
        if force_bold is not None:
            run.font.bold = bool(force_bold)
        if clean_hex:
            try:
                run.font.color.rgb = RGBColor.from_string(clean_hex)
            except Exception:
                pass
        set_run_fonts(run, zh_font=zh_font, en_font=en_font)


def _first_line_indent_pt(chars: int, font_size_pt: float) -> Pt:
    # 近似：1 个中文字符宽度 ≈ 1 个字号(pt)
    return Pt(chars * font_size_pt)


def _cleanup_consecutive_blanks(doc, max_keep: int, paras: List[Paragraph] = None) -> int:
    """压缩连续空段：最多保留 max_keep 个（0=全删）。返回删除的空段数量。"""
    blank_run = 0
    to_delete = []
    last_parent = None
    if paras is None:
        paras = list(iter_all_paragraphs(doc))
    for p in paras:
        # Safety check for already-deleted/stale paragraphs
        if getattr(p, '_p', None) is None:
            continue

        cur_parent = p._p.getparent()
        if cur_parent is not last_parent:
            # 不跨容器（正文/单元格）累计空段，避免误删
            blank_run = 0
            last_parent = cur_parent
        if is_effectively_blank_paragraph(p):
            blank_run += 1
            if blank_run > max_keep:
                to_delete.append(p)
        else:
            blank_run = 0

    deleted = 0
    # 倒序删除，避免重排带来的错删
    for p in reversed(to_delete):
        delete_paragraph(p)
        deleted += 1
    return deleted


def _delete_blanks_after_roles(doc, roles: Set[str], role_getter=None, paras: List[Paragraph] = None) -> int:
    """删除“标题/题注后紧跟的所有空段”。返回删除数量。"""
    if role_getter is None:
        role_getter = detect_role

    to_delete = []
    if paras is None:
        paras = list(iter_all_paragraphs(doc))
    i = 0
    while i < len(paras):
        cur = paras[i]
        # Safety check for already-deleted/stale paragraphs
        if getattr(cur, '_p', None) is None:
            i += 1
            continue

        cur_role = role_getter(cur)
        if cur_role in roles:
            cur_parent = cur._p.getparent()
            j = i + 1
            while j < len(paras):
                nxt = paras[j]
                # Safety check for nxt being stale
                if getattr(nxt, '_p', None) is None:
                    j += 1
                    continue

                if nxt._p.getparent() is not cur_parent:
                    break
                if not is_effectively_blank_paragraph(nxt):
                    break
                to_delete.append(nxt)
                j += 1
        i += 1

    for p in reversed(to_delete):
        delete_paragraph(p)
    return len(to_delete)


def _normalize_table_list_separators(doc, role_getter=None, paras: List[Paragraph] = None) -> int:
    """
    表格单元格内列表项预处理：把"；N)"/"；（N）"等内联分隔符替换为 '\\n'，
    以便后续 _split_body_paragraphs_on_linebreaks 正确将其拆成独立段落。

    只处理表格单元格内（_is_in_table_cell）且角色为 body/list_item/unknown 的段落。
    返回被修改的段落数量。
    """
    if role_getter is None:
        role_getter = detect_role

    modified = 0
    if paras is None:
        paras = list(iter_all_paragraphs(doc))
    for p in paras:
        if not _is_in_table_cell(p):
            continue
        if is_effectively_blank_paragraph(p):
            continue
        role = role_getter(p)
        if role not in {"body", "list_item", "unknown"}:
            continue
        text = p.text or ""
        # 只处理以列表标记开头、且包含内联分隔符的段落
        if detect_text_list_prefix(text) is None:
            continue
        if not _RE_INLINE_LIST_SEP.search(text):
            continue
        # 逐 run 将 ；（紧跟列表标记处）替换为 \n
        changed = False
        for run in iter_paragraph_runs(p):
            if run.text and _RE_INLINE_LIST_SEP.search(run.text):
                run.text = _RE_INLINE_LIST_SEP.sub('\n', run.text)
                changed = True
        if changed:
            modified += 1
    return modified


def _split_body_paragraphs_on_linebreaks(doc, role_getter=None, on_new_paragraph=None, paras: List[Paragraph] = None) -> int:
    """
    把正文/列表段落里的 '\n'（通常是 Shift+Enter 软回车）拆成多个段落。
    """
    if role_getter is None:
        role_getter = detect_role

    created = 0
    if paras is None:
        paras = list(iter_all_paragraphs(doc))
    for p in paras:
        if is_effectively_blank_paragraph(p):
            continue

        # SAFETY: If paragraph has drawings/images, do NOT split it by clearing runs,
        # as it would "swallow" the image.
        if is_drawing_paragraph(p):
            continue

        # 只拆正文/列表/unknown；标题/题注不拆，避免破坏结构
        role = role_getter(p)
        if role not in {"body", "list_item", "unknown"}:
            continue

        text = p.text or ""
        if RE_SOFT_LINEBREAK.search(text) is None:
            continue

        # 保留每一行对应的“源 run 样式”（颜色/粗斜体），避免拆段后样式丢失
        raw_runs = list(iter_paragraph_runs(p))
        line_parts = [[]]
        for src_run in raw_runs:
            parts = re.split(r"[\n\r\v]", (src_run.text or ""))
            for idx, part in enumerate(parts):
                if part:
                    line_parts[-1].append((part, src_run))
                if idx < len(parts) - 1:
                    line_parts.append([])

        resolved_lines = []
        for parts in line_parts:
            line_text = "".join(seg for seg, _ in parts).strip()
            if not line_text:
                continue
            style_run = parts[0][1] if parts else (raw_runs[0] if raw_runs else None)
            resolved_lines.append((line_text, style_run))

        if len(resolved_lines) <= 1:
            continue

        # 当前段落替换成第一行并继承该行样式
        _clear_paragraph_runs(p)
        first_text, first_style_run = resolved_lines[0]
        first_run = p.add_run(first_text)
        if first_style_run is not None:
            copy_run_style(first_style_run, first_run)

        # 后续行插入为新段落，复制段落样式与该行首 run 样式
        prev = p
        for ln, style_run in resolved_lines[1:]:
            new_p = _insert_paragraph_after(prev, ln)
            created += 1
            try:
                new_p.style = p.style
            except Exception:
                pass
            if style_run is not None and new_p.runs:
                copy_run_style(style_run, new_p.runs[0])

            if on_new_paragraph is not None:
                try:
                    on_new_paragraph(p, new_p)
                except Exception:
                    # 回调失败不应影响主流程
                    pass

            prev = new_p
    return created


def apply_formatting(doc, blocks: List[Block], labels: Dict[int, str], spec: Spec):
    """
    对文档段落进行排版：首行缩进、字体、字号、行距、段距、空行清理，
    并把正文中的软回车换行拆为独立段落，以及（可选）将文本编号列表转换为
    真正的 Word 列表（numPr）。

    返回：report(dict) —— 可直接 dump 为 JSON，用于“诊断/修复报告”。
    """
    cfg = spec.raw
    zh_font = cfg["fonts"]["zh"]
    en_font = cfg["fonts"]["en"]

    body_cfg = cfg["body"]
    body_size = float(body_cfg["font_size_pt"])
    body_line_spacing = float(body_cfg["line_spacing"])
    body_before = float(body_cfg["space_before_pt"])
    body_after = float(body_cfg["space_after_pt"])
    first_line_chars = int(body_cfg["first_line_chars"])
    # 支持每个角色独立的字体覆盖：body.font_name 优先于全局 fonts
    body_zh_font = body_cfg.get("font_name", zh_font)
    body_en_font = body_cfg.get("font_name_en", en_font)
    body_bold = body_cfg.get("bold", None)  # None 表示不强制
    body_italic = bool(body_cfg.get("italic", False)) if "italic" in body_cfg else None

    heading_cfg = cfg["heading"]
    caption_cfg = cfg.get("caption", None)
    paragraph_cfg = cfg.get("paragraph", {})
    page_cfg = cfg.get("page", {})

    cleanup_cfg = cfg.get("cleanup", {})
    max_blank_keep = int(cleanup_cfg.get("max_consecutive_blank_paragraphs", 1))
    remove_blank_after_roles = set(cleanup_cfg.get("remove_blank_after_roles", ["h1", "h2", "h3", "caption"]))

    list_cfg = cfg.get("list_item", {})
    list_left_indent = float(list_cfg.get("left_indent_pt", 18))
    list_hanging_indent = float(list_cfg.get("hanging_indent_pt", 18))
    list_size = float(list_cfg.get("font_size_pt", body_size))
    list_bold = bool(list_cfg.get("bold", False))
    list_italic = bool(list_cfg.get("italic", False))

    body_alignment = _resolve_alignment(paragraph_cfg.get("alignment", "justify"))

    def _build_body_scope_from_sentinel(paras: List[Paragraph]) -> set:
        has_tag = any("{body}" in (p.text or "") for p in paras)
        in_range = not has_tag
        body_elements = set()
        for p in paras:
            t = (p.text or "").strip()
            if "{body}" in t:
                in_range = True
                continue
            if "{/body}" in t:
                in_range = False
                continue
            if in_range:
                body_elements.add(p._p)
        return body_elements

    orig_paras = list(iter_all_paragraphs(doc))
    in_body_scope_elems = _build_body_scope_from_sentinel(orig_paras)
    logger.info(f"[scope] 排版范围锁定: {len(in_body_scope_elems)} 个段落节点。")

    # ====== 角色映射：优先 labels，缺失才 fallback detect_role ======
    # 关键点：用底层 CT_P XML 元素做 key（而不是 Paragraph 包装对象），
    # 因为每次调用 iter_all_paragraphs 都会创建新的 Paragraph 包装对象，
    # 若用对象本身做 key 会导致 label_by_elem 查找永远失败。
    para_by_index = {i: p for i, p in enumerate(orig_paras)}
    label_by_elem: Dict = {}  # CT_P element -> role str

    for b in blocks:
        role = labels.get(b.block_id)
        if not role or role in ROLE_LABELS_FALLBACK_TO_RULE:
            continue
        p = para_by_index.get(b.paragraph_index)
        if p is not None:
            label_by_elem[p._p] = role

    def get_role(p: Paragraph) -> str:
        return label_by_elem.get(p._p) or detect_role(p)

    # section-aware role overrides: cover/toc/requirement
    section_role_by_elem = _detect_section_role(orig_paras, detect_role)

    # 需要被跳过排版的特殊角色集合
    _SKIP_ROLES = {"cover", "toc", "requirement", "reference"}

    def get_effective_role(p: Paragraph) -> str:
        # 1. 基础识别逻辑
        res = _get_effective_role_raw(p)

        # 2. 极其严格的 Caption（图例/题注）二次校验
        # 只有在明确具有图例特征时才允许居中对齐，否则强制转为 body
        if res == "caption":
            txt = (p.text or "").strip()
            # 必须匹配 RE_CAPTION 正则或是具有明确的 Word 题注样式
            style_name = ""
            try:
                style_name = (p.style.name or "").lower()
            except:
                pass
            is_valid_caption_style = "caption" in style_name or "题注" in style_name
            
            if not (RE_CAPTION.match(txt) or is_valid_caption_style):
                return "body"

        return res

    def _get_effective_role_raw(p: Paragraph) -> str:
        # 0. 区域排版边界控制：不在 {body} 闭 closure 内的，统统视为 cover 跳过排版
        if p._p not in in_body_scope_elems:
            return "cover"

        # 1. section 状态机结果优先（基于文档结构流的上下文推断）
        sec_role = section_role_by_elem.get(p._p)
        if sec_role:
            return sec_role

        # 2. LLM 写入 labels 的标签（含 LLM 页面分类结果 cover/toc 等）
        #    必须在 detect_role 之前检查，否则 LLM 的 cover/toc 标注永远不生效
        llm_role = label_by_elem.get(p._p)
        if llm_role:
            return llm_role

        # 3. detect_role 识别出的特殊角色（pattern/style 匹配）
        detected = detect_role(p)
        if detected in _SKIP_ROLES:
            return detected

        # 4. detect_role 结果（通用 body/h1/h2 等）
        return detected

    # ====== Report（诊断/动作统计/可解释输出）======
    label_source = labels.get("_source", "unknown")
    # 仅统计 blocks 对应的标签，忽略 labels 里的元信息键
    label_list = [labels.get(b.block_id, "unknown") for b in blocks]
    label_counts = dict(Counter(label_list))

    # labels 覆盖率（仅针对 blocks；unknown/blank 视为未标注）
    total_blocks = len(blocks)
    labeled_blocks = 0
    for b in blocks:
        lab = labels.get(b.block_id)
        if lab and lab not in ("blank", "unknown"):
            labeled_blocks += 1
    coverage_rate = (labeled_blocks / total_blocks) if total_blocks else 0.0

    # label vs fallback 规则的一致性（仅针对原始段落）
    mismatch = 0
    compared = 0
    mismatch_examples = []
    for b in blocks:
        p = para_by_index.get(b.paragraph_index)
        if p is None:
            continue
        lab = labels.get(b.block_id)
        if not lab or lab == "blank":
            continue
        # Skip multi-line numbered blocks: detect_role returns 'body' as a
        # conservative safety measure (to avoid heading mis-classification), not
        # as a confident semantic classification.  The LLM's label for these
        # paragraphs is the reliable signal; the paragraph will be split on
        # linebreaks in step 3 and processed correctly regardless.
        if looks_like_multiline_numbered_block(p.text or ""):
            continue
        compared += 1
        det = detect_role(p)
        if det != lab:
            mismatch += 1
            if len(mismatch_examples) < 5:
                mismatch_examples.append({
                    "paragraph_index": b.paragraph_index,
                    "block_id": b.block_id,
                    "label": lab,
                    "detect_role": det,
                    "text": (p.text or "")[:120],
                })

    list_para_count = sum(1 for p in orig_paras if is_list_paragraph(p))

    report = {
        "meta": {
            "paragraphs_before": len(orig_paras),
            "blank_paragraphs_before": sum(1 for p in orig_paras if is_effectively_blank_paragraph(p)),
            "list_paragraphs_before": list_para_count,
        },
        "labels": {
            "source": label_source,
            "counts": label_counts,
            "coverage": {
                "total_blocks": total_blocks,
                "labeled_blocks": labeled_blocks,
                "coverage_rate": coverage_rate,
            },
            "consistency": {
                "compared": compared,
                "mismatched": mismatch,
                "mismatch_rate": (mismatch / compared) if compared else 0.0,
                "mismatch_examples": mismatch_examples,
            },
        },
        "plan_executed": [
            "cleanup_consecutive_blanks",
            "delete_blanks_after_titles",
            "normalize_table_inline_list_separators",
            "split_body_paragraphs_on_linebreaks",
            "apply_formatting",
            "convert_text_lists_to_numpr",
        ],
        "actions": {},
        "formatted": {"counts": {}},
        "warnings": [],
    }
    # 0) 页面级排版（section）
    page_sections_changed = _apply_page_layout(doc, page_cfg)
    report["actions"]["page_sections_changed"] = page_sections_changed

    body_paras_only = [p for p in orig_paras if p._p in in_body_scope_elems]

    # 1) 空段压缩/清理
    deleted_consecutive = _cleanup_consecutive_blanks(doc, max_blank_keep, paras=body_paras_only)
    report["actions"]["cleanup_consecutive_blanks_deleted"] = deleted_consecutive
    report["actions"]["cleanup_consecutive_blank_keep"] = max_blank_keep

    # Refilter body paragraphs after potential deletions in stage 1
    body_paras_only = [p for p in body_paras_only if getattr(p, '_p', None) is not None]

    # 2) 标题/题注后空段删光
    deleted_after_roles = _delete_blanks_after_roles(doc, roles=remove_blank_after_roles, role_getter=get_effective_role, paras=body_paras_only)
    report["actions"]["delete_blanks_after_titles_deleted"] = deleted_after_roles

    # Refilter body paragraphs after potential deletions in stage 2
    body_paras_only = [p for p in body_paras_only if getattr(p, '_p', None) is not None]

    # 2.5) 表格单元格内联列表分隔符规范化
    normalized_table_seps = _normalize_table_list_separators(doc, role_getter=get_effective_role, paras=body_paras_only)
    report["actions"]["table_inline_list_normalized"] = normalized_table_seps

    # 3) 核心修复：拆正文段落里的软回车换行（\n/\r/\v）
    # 先做一次“预估/统计”，便于 report 诊断
    split_affected = 0
    split_max_lines = 0
    split_estimated_new = 0
    for p in body_paras_only:
        if getattr(p, '_p', None) is None:
            continue
        if is_effectively_blank_paragraph(p):
            continue
        if get_effective_role(p) not in {"body", "list_item", "unknown"}:
            continue
        t = hasattr(p, 'text') and p.text or ""
        if RE_SOFT_LINEBREAK.search(t) is None:
            continue
        lines = [ln.strip() for ln in re.split(r"[\n\r\v]", t) if ln.strip()]
        if len(lines) <= 1:
            continue
        split_affected += 1
        split_max_lines = max(split_max_lines, len(lines))
        split_estimated_new += (len(lines) - 1)

    new_paras_from_split: List[Paragraph] = []
    # Cache original labels before any mutations in _inherit_label so that
    # siblings processed after the first child still see the pre-mutation value.
    _orig_split_labels: Dict = {}

    def _inherit_label(parent_p, child_p):
        # 让拆分出来的新段落继承原段落的标签（避免 fallback 造成标签不一致）
        if parent_p._p not in label_by_elem:
            new_paras_from_split.append(child_p)
            return

        # Cache the original label on first encounter (before any mutations).
        if parent_p._p not in _orig_split_labels:
            _orig_split_labels[parent_p._p] = label_by_elem[parent_p._p]
        orig_label = _orig_split_labels[parent_p._p]

        # When a list_item multi-line block is split, some fragments (e.g. a
        # preamble sentence like "以下方向：" or a header like "【大二下学期】")
        # have no recognisable list prefix and will NOT be converted to numPr by
        # step 5.  Stamping them as list_item would give them a hanging indent
        # without a numPr marker, which produces wrong首行缩进 rendering.  Treat
        # such fragments as body so they receive the normal首行缩进 instead.
        def _effective_label(p):
            if (orig_label == "list_item"
                    and detect_text_list_prefix(p.text or "") is None
                    and not is_list_paragraph(p)):
                return "body"
            return orig_label

        # Update parent label if its text (first split line) has no list prefix.
        label_by_elem[parent_p._p] = _effective_label(parent_p)

        if child_p._p not in label_by_elem:
            label_by_elem[child_p._p] = _effective_label(child_p)

        new_paras_from_split.append(child_p)

    created_by_split = _split_body_paragraphs_on_linebreaks(doc, role_getter=get_effective_role, on_new_paragraph=_inherit_label, paras=body_paras_only)
    report["actions"]["split_body_new_paragraphs_created"] = created_by_split
    report["actions"]["split_body_original_paragraphs_affected"] = split_affected
    report["actions"]["split_body_max_lines_in_one_paragraph"] = split_max_lines
    report["actions"]["split_body_estimated_new_paragraphs"] = split_estimated_new

    # 4) 套格式
    formatted_counter = Counter()

    paras_for_formatting = list(iter_all_paragraphs(doc))
    for p in paras_for_formatting:
        if is_effectively_blank_paragraph(p):
            continue

        role = get_effective_role(p)

        # 标题/题注：去掉段尾多余换行
        if role in ("h1", "h2", "h3", "caption"):
            _strip_trailing_newlines_in_paragraph(p)

        if role in ("cover", "toc", "requirement"):
            # 特殊页面区域：封面/目录/课程要求等跳过排版，保留原始格式
            formatted_counter[f"{role}_skipped"] += 1

        elif role == "body":
            _apply_paragraph_common(p, body_line_spacing, body_before, body_after)

            # 普通正文与 Word 列表正文分别处理缩进，避免相互覆盖
            if is_pure_drawing_paragraph(p):
                p.paragraph_format.left_indent = Pt(0)
                p.paragraph_format.hanging_indent = Pt(0)
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
                formatted_counter["image_body"] += 1
            elif is_list_paragraph(p):
                p.paragraph_format.left_indent = Pt(list_left_indent)
                p.paragraph_format.hanging_indent = Pt(list_hanging_indent)
                p.paragraph_format.first_line_indent = Pt(0)
                formatted_counter["list_body"] += 1
            elif _is_in_table_cell(p):
                # 表格单元格内正文：不添加首行缩进，避免窄列中显示异常
                p.paragraph_format.left_indent = Pt(0)
                p.paragraph_format.hanging_indent = Pt(0)
                p.paragraph_format.first_line_indent = Pt(0)
                formatted_counter["table_body"] += 1
            else:
                p.paragraph_format.left_indent = Pt(0)
                p.paragraph_format.hanging_indent = Pt(0)
                p.paragraph_format.first_line_indent = _first_line_indent_pt(first_line_chars, body_size)
                formatted_counter["body"] += 1

            if body_alignment is not None:
                p.paragraph_format.alignment = body_alignment
            _apply_runs_font(p, body_zh_font, body_en_font, size_pt=body_size,
                             force_bold=body_bold, color_hex=body_cfg.get("color"))
            # 支持 body 斜体覆盖
            if body_italic is not None:
                for run in iter_paragraph_runs(p):
                    run.font.italic = body_italic

        elif role in ("h1", "h2", "h3"):
            hc = heading_cfg[role]
            size = float(hc["font_size_pt"])
            bold = bool(hc["bold"])
            before = float(hc["space_before_pt"])
            after = float(hc["space_after_pt"])
            # 兼容 alignment 和旧字段名 align
            heading_align = _resolve_alignment(hc.get("alignment") or hc.get("align", "left"))
            # 支持标题级别独立行距；若未配置则继承正文行距
            heading_line_sp = float(hc["line_spacing"]) if "line_spacing" in hc else body_line_spacing
            # 支持标题级别独立字体；若未配置则继承全局字体
            h_zh_font = hc.get("font_name", zh_font)
            h_en_font = hc.get("font_name_en", en_font)

            _apply_paragraph_common(p, heading_line_sp, before, after)
            p.paragraph_format.left_indent = Pt(0)
            p.paragraph_format.hanging_indent = Pt(0)
            p.paragraph_format.first_line_indent = Pt(0)
            if heading_align is not None:
                p.paragraph_format.alignment = heading_align
            _apply_runs_font(p, h_zh_font, h_en_font, size_pt=size, force_bold=bold,
                             color_hex=hc.get("color"))
            # 支持标题斜体
            if hc.get("italic") is not None:
                for run in iter_paragraph_runs(p):
                    run.font.italic = bool(hc["italic"])
            formatted_counter[role] += 1

        elif role == "caption":
            # 无论 caption_cfg 是否为空，我们都要保证标题默认居中
            size = float(caption_cfg.get("font_size_pt", body_size)) if caption_cfg else body_size
            bold = bool(caption_cfg.get("bold", False)) if caption_cfg else False
            before = float(caption_cfg.get("space_before_pt", body_before)) if caption_cfg else body_before
            after = float(caption_cfg.get("space_after_pt", body_after)) if caption_cfg else body_after

            _apply_paragraph_common(p, body_line_spacing, before, after)
            p.paragraph_format.left_indent = Pt(0)
            p.paragraph_format.hanging_indent = Pt(0)
            p.paragraph_format.first_line_indent = Pt(0)
            
            # 使用配置中的 alignment，如果未配置或者 caption_cfg 为空，则默认 center 
            cap_align_str = caption_cfg.get("alignment", "center" if caption_cfg.get("center", True) else "left") if caption_cfg else "center"
            cap_align = _resolve_alignment(cap_align_str)
            if cap_align is not None:
                p.paragraph_format.alignment = cap_align
            _apply_runs_font(p, zh_font, en_font, size_pt=size, force_bold=bold)

            formatted_counter["caption"] += 1

        elif role in ("abstract", "keyword", "reference", "footer", "list_item", "toc", "requirement"):
            rc = cfg.get(role, {})
            size = float(rc.get("font_size_pt", body_size))
            bold = bool(rc.get("bold", False))
            italic = bool(rc.get("italic", False))
            before = float(rc.get("space_before_pt", body_before))
            after = float(rc.get("space_after_pt", body_after))
            flc = int(rc.get("first_line_chars", 0))
            hanging = float(rc.get("hanging_indent_pt", 0))
            role_align = _resolve_alignment(rc.get("alignment", "justify"))

            _apply_paragraph_common(p, body_line_spacing, before, after)
            if hanging:
                # 悬挂缩进：所有行左缩进 hanging pt，首行回缩 -hanging pt（首行从页边起）
                p.paragraph_format.left_indent = Pt(hanging)
                p.paragraph_format.first_line_indent = Pt(-hanging)
            elif flc and not _is_in_table_cell(p):
                p.paragraph_format.left_indent = Pt(0)
                p.paragraph_format.first_line_indent = _first_line_indent_pt(flc, size)
            else:
                p.paragraph_format.left_indent = Pt(0)
                p.paragraph_format.first_line_indent = Pt(0)
            if role_align is not None:
                p.paragraph_format.alignment = role_align

            normalize_mixed_runs(p)
            for run in iter_paragraph_runs(p):
                run.font.size = Pt(size)
                run.font.bold = bold
                run.font.italic = italic
                set_run_fonts(run, zh_font=zh_font, en_font=en_font)

            formatted_counter[role] += 1

        else:
            # unknown：当正文处理，尽量不让段落漏掉缩进/字体统一
            _apply_paragraph_common(p, body_line_spacing, body_before, body_after)
            p.paragraph_format.left_indent = Pt(0)
            p.paragraph_format.hanging_indent = Pt(0)
            if is_drawing_paragraph(p):
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
                formatted_counter["image_body"] += 1
            elif _is_in_table_cell(p):
                p.paragraph_format.first_line_indent = Pt(0)
            else:
                p.paragraph_format.first_line_indent = _first_line_indent_pt(first_line_chars, body_size)
            
            if body_alignment is not None and not is_drawing_paragraph(p):
                p.paragraph_format.alignment = body_alignment
            _apply_runs_font(p, zh_font, en_font, size_pt=body_size, force_bold=None)
            formatted_counter["unknown_as_body"] += 1



    # 4.5) 已废弃：原 LLM 直接驱动的 list_item 编号转换逻辑已合并到步骤 5。
    # 步骤 5 的 convert_text_lists 现在同时处理 list_item 和 body/unknown 段落，
    # 保证连续编号项整组使用同一 num_id，避免首项被单独结构化的问题。
    convert_text_nums = bool(list_cfg.get("convert_text_numbers", True))
    llm_direct_converted = 0
    report["actions"]["llm_direct_list_converted"] = llm_direct_converted

    # 5) 文本编号 → 真实 Word 列表（numPr）转换
    # convert_text_lists 会将连续的编号段落（list_item、body 或 unknown 角色且含编号前缀）
    # 整组转换为同一 num_id 的 Word 列表，保证渲染对齐。
    if convert_text_nums:
        num_left_twips = int(list_cfg.get("num_left_twips", 720))
        num_hanging_twips = int(list_cfg.get("num_hanging_twips", 360))
        min_run_len = int(list_cfg.get("min_run_len", 1))
        converted_to_numpr, step5_converted_paras = convert_text_lists(
            doc,
            iter_all_paragraphs(doc),
            get_effective_role,
            is_list_paragraph,
            is_effectively_blank_paragraph,
            min_run_len=min_run_len,
            left_twips=num_left_twips,
            hanging_twips=num_hanging_twips,
            zh_font=zh_font,
            en_font=en_font,
            size_pt=list_size,
            bold=list_bold,
            italic=list_italic,
        )
        # Sync font/size and indentation for paragraphs converted in this step.
        # Critical: override any positive first_line_indent (e.g. from body role
        # formatting in step 4) that would otherwise conflict with the numPr
        # hanging indent and break the visual indentation of numbered list items.
        _list_first_line_chars = int(list_cfg.get("first_line_chars", 0))
        for p in step5_converted_paras:
            if list_hanging_indent:
                p.paragraph_format.left_indent = Pt(list_hanging_indent)
                p.paragraph_format.first_line_indent = Pt(-list_hanging_indent)
            elif _list_first_line_chars and not _is_in_table_cell(p):
                p.paragraph_format.left_indent = Pt(0)
                p.paragraph_format.first_line_indent = _first_line_indent_pt(_list_first_line_chars, list_size)
            else:
                p.paragraph_format.left_indent = Pt(0)
                p.paragraph_format.first_line_indent = Pt(0)
            normalize_mixed_runs(p)
            for run in iter_paragraph_runs(p):
                run.font.size = Pt(list_size)
                run.font.bold = list_bold
                run.font.italic = list_italic
                set_run_fonts(run, zh_font=zh_font, en_font=en_font)
    else:
        converted_to_numpr = 0
    report["actions"]["text_list_converted_to_numpr"] = converted_to_numpr

    # 6) 表格自适应页宽
    tables_autofitted = _autofit_tables(doc)
    report["actions"]["tables_autofitted"] = tables_autofitted
    tables_centered = _center_tables(doc)
    report["actions"]["tables_centered"] = tables_centered

    media_paragraphs_centered = 0
    for p in iter_all_paragraphs(doc):
        if is_pure_drawing_paragraph(p):
            p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            media_paragraphs_centered += 1
    report["actions"]["media_paragraphs_centered"] = media_paragraphs_centered


    # ====== 追加诊断提示（让报告更“智能体”）======
    # 1) 软回车拆段提示
    if created_by_split > 0:
        report["warnings"].append(
            f"检测到正文段落含软回车(\\n)，已自动拆分为独立段落：新增 {created_by_split} 段。"
        )

    # 2) 列表段落提示（Word 真编号/项目符号可能覆盖缩进）
    if list_para_count > 0:
        report["warnings"].append(
            f"检测到 {list_para_count} 个 Word 列表/编号段落(numPr)。其缩进可能由列表级别控制，首行缩进不一致时建议单独处理列表缩进。"
        )

    # 3) 标签一致性提示
    if compared > 0 and mismatch > 0:
        rate = report["labels"]["consistency"]["mismatch_rate"]
        report["warnings"].append(
            f"标签与回退规则(detect_role)存在不一致：{mismatch}/{compared} (≈{rate:.0%})。建议统一以 labels 为准，并逐步收敛规则/LLM 标注。"
        )

    # 4) 标题层级提示：h3 前无 h2
    roles_seq = [get_effective_role(p) for p in iter_all_paragraphs(doc) if not is_effectively_blank_paragraph(p)]
    h2_seen = False
    orphan_h3 = 0
    for r in roles_seq:
        if r == "h2":
            h2_seen = True
        elif r == "h1":
            h2_seen = False
        elif r == "h3" and not h2_seen:
            orphan_h3 += 1
    if orphan_h3 > 0:
        report["warnings"].append(
            f"检测到 {orphan_h3} 个 h3 在其前方未出现 h2（可能层级断裂或标签误判）。"
        )

    # 额外统计：拆分新增段落的 role 分布（便于验证“新增段大多为正文”这一预期）
    try:
        new_role_counts = Counter(get_role(p) for p in new_paras_from_split if p is not None)
        report["actions"]["split_body_new_paragraph_roles"] = dict(new_role_counts)
    except Exception:
        report["actions"]["split_body_new_paragraph_roles"] = {}

    # 7) --- 清理标记段落并插入分页符 ---
    # 使用后置扫描以保证鲁棒性
    final_paras_for_cleanup = list(iter_all_paragraphs(doc))
    for p in final_paras_for_cleanup:
        try:
            full_text = p.text or ""
            if "{body}" in full_text:
                delete_paragraph(p)
            elif "{/body}" in full_text:
                # 依据要求：在正文结束处确保分页
                p.text = ""
                p.add_run().add_break(WD_BREAK.PAGE)
        except Exception:
            pass

    # === 表格自动居中 ===
    centered_tables = _center_tables(doc)
    report["actions"]["tables_centered"] = centered_tables

    # 最后计算报告统计数据
    all_final = iter_all_paragraphs(doc)
    report["meta"]["paragraphs_after"] = len(all_final)
    report["meta"]["blank_paragraphs_after"] = sum(1 for p in all_final if is_effectively_blank_paragraph(p))
    report["formatted"]["counts"] = dict(formatted_counter)
                
    return report
