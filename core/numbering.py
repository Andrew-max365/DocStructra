# core/numbering.py
"""
Word document list/numbering utilities.

Converts text-based list markers (e.g. "（1）", "1)", "①") into real
Word numbered list paragraphs (numPr + abstractNum/num definitions).
"""

import re
from typing import Dict, List, Optional, Tuple

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


# ─── List pattern detection ───────────────────────────────────────────────────

_RE_PAREN_ARABIC = re.compile(r"^(\s*（)(\d+)(）)")           # （1）
_RE_RPAREN       = re.compile(r"^(\s*)(\d+)([)）])(\s?)")     # 1) or 1）, optional space
_RE_NUM_DOT      = re.compile(r"^(\s*)(\d+)(\. )")             # 1. text
_RE_ENCLOSED     = re.compile(
    r"^\s*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])"
)
_RE_ALPHA_LOWER  = re.compile(r"^\s*([a-z])[.)]\s", re.ASCII)
_RE_ALPHA_UPPER  = re.compile(r"^\s*([A-Z])[.)]\s", re.ASCII)

_ENCLOSED_ORD: Dict[str, int] = {
    '①': 1,  '②': 2,  '③': 3,  '④': 4,  '⑤': 5,
    '⑥': 6,  '⑦': 7,  '⑧': 8,  '⑨': 9,  '⑩': 10,
    '⑪': 11, '⑫': 12, '⑬': 13, '⑭': 14, '⑮': 15,
    '⑯': 16, '⑰': 17, '⑱': 18, '⑲': 19, '⑳': 20,
}

# Supported format keys
LIST_FMTS = ("paren_arabic", "rparen", "num_dot", "enclosed", "alpha_lower", "alpha_upper")

# Structural roles that always break a list group (never part of a numbered list)
_STRUCTURAL_ROLES = frozenset({
    "h1", "h2", "h3", "caption", "abstract", "keyword", "reference", "footer",
    "cover", "toc", "requirement"
})

# Decimal-style formats that may be freely mixed within one list group when ordinals
# are sequential (e.g. Chinese docs often open with "（1）" and continue with "2）").
_DECIMAL_FMTS = frozenset({"paren_arabic", "rparen"})

# Map format key → (w:numFmt value, w:lvlText value)
_FMT_TO_WORD: Dict[str, Tuple[str, str]] = {
    "paren_arabic": ("decimal",                "（%1）"),  # full-width parentheses
    "rparen":       ("decimal",                "%1)"),
    "num_dot":      ("decimal",                "%1."),    # 1. 2. 3.
    "enclosed":     ("decimalEnclosedCircle",   "%1"),
    "alpha_lower":  ("lowerLetter",             "%1."),
    "alpha_upper":  ("upperLetter",             "%1."),
}


def detect_text_list_prefix(text: str) -> Optional[Tuple[str, int, int]]:
    """
    Detect a text-based list marker at the start of *text*.

    Returns ``(fmt, ordinal, prefix_char_count)`` or ``None``.

    - ``fmt``               – one of :data:`LIST_FMTS`
    - ``ordinal``           – 1-based ordinal of this marker (e.g. 2 for "（2）")
    - ``prefix_char_count`` – number of Unicode codepoints to strip from the
                              paragraph text to remove the marker (includes
                              any leading whitespace in the match)
    """
    t = text or ""
    m = _RE_PAREN_ARABIC.match(t)
    if m:
        return ("paren_arabic", int(m.group(2)), m.end())
    m = _RE_RPAREN.match(t)
    if m:
        return ("rparen", int(m.group(2)), m.end())
    m = _RE_NUM_DOT.match(t)
    if m:
        return ("num_dot", int(m.group(2)), m.end())
    m = _RE_ENCLOSED.match(t)
    if m:
        ch = m.group(1)
        return ("enclosed", _ENCLOSED_ORD.get(ch, 1), m.end())
    m = _RE_ALPHA_LOWER.match(t)
    if m:
        return ("alpha_lower", ord(m.group(1)) - ord('a') + 1, m.end())
    m = _RE_ALPHA_UPPER.match(t)
    if m:
        return ("alpha_upper", ord(m.group(1)) - ord('A') + 1, m.end())
    return None


# ─── Numbering XML helpers ────────────────────────────────────────────────────

def _numbering_element(doc):
    """Return the ``w:numbering`` XML element, creating the numbering part if absent.

    Some ``.docx`` files lack a ``word/numbering.xml`` part (no pre-existing
    lists).  In the installed version of python-docx ``NumberingPart.new()`` is
    not implemented, so accessing ``doc.part.numbering_part`` raises
    ``NotImplementedError``.  This function catches that case and creates a
    minimal numbering part so that list definitions can be appended safely.
    """
    try:
        return doc.part.numbering_part._element
    except NotImplementedError:
        # NumberingPart.new() is unimplemented in this python-docx release.
        # Build a minimal numbering part from raw XML and attach it manually.
        from docx.opc.packuri import PackURI
        from docx.oxml.ns import nsdecls
        from docx.oxml.parser import parse_xml
        from docx.parts.numbering import NumberingPart as _NP

        _CONTENT_TYPE = (
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.numbering+xml"
        )
        _REL_TYPE = (
            "http://schemas.openxmlformats.org/officeDocument"
            "/2006/relationships/numbering"
        )
        blob = ("<w:numbering %s/>" % nsdecls("w", "r")).encode("utf-8")
        npart = _NP.load(PackURI("/word/numbering.xml"), _CONTENT_TYPE, blob, doc.part.package)
        doc.part.relate_to(npart, _REL_TYPE)
        return npart._element


def _next_free_id(nelem) -> int:
    """
    Return the next unused integer ID that can be used for both
    ``w:abstractNumId`` and ``w:numId``.  We take max of all existing IDs + 1.
    """
    used = set()
    for child in nelem:
        for attr in (qn("w:abstractNumId"), qn("w:numId")):
            val = child.get(attr)
            if val is not None:
                try:
                    used.add(int(val))
                except ValueError:
                    pass
    return max(used, default=0) + 1


def _build_abstractNum(
    abs_id: int,
    fmt: str,
    left_twips: int,
    hanging_twips: int,
    zh_font: Optional[str] = None,
    en_font: Optional[str] = None,
    size_pt: Optional[float] = None,
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
    start_ordinal: int = 1,
):
    """Build a ``w:abstractNum`` XML element for a single-level list."""
    num_fmt_val, lvl_text_val = _FMT_TO_WORD[fmt]

    abstractNum = OxmlElement("w:abstractNum")
    abstractNum.set(qn("w:abstractNumId"), str(abs_id))

    mlt = OxmlElement("w:multiLevelType")
    mlt.set(qn("w:val"), "singleLevel")
    abstractNum.append(mlt)

    lvl = OxmlElement("w:lvl")
    lvl.set(qn("w:ilvl"), "0")

    start = OxmlElement("w:start")
    # Word requires w:start >= 1; clamp silently to match schema expectations.
    start.set(qn("w:val"), str(max(1, start_ordinal)))
    lvl.append(start)

    numFmt = OxmlElement("w:numFmt")
    numFmt.set(qn("w:val"), num_fmt_val)
    lvl.append(numFmt)

    lvlText = OxmlElement("w:lvlText")
    lvlText.set(qn("w:val"), lvl_text_val)
    lvl.append(lvlText)

    lvlJc = OxmlElement("w:lvlJc")
    lvlJc.set(qn("w:val"), "left")
    lvl.append(lvlJc)

    pPr = OxmlElement("w:pPr")
    ind = OxmlElement("w:ind")
    ind.set(qn("w:left"), str(left_twips))
    ind.set(qn("w:hanging"), str(hanging_twips))
    pPr.append(ind)
    lvl.append(pPr)

    # Ensure list marker glyphs follow configured fonts/size rather than Word defaults.
    if zh_font or en_font or size_pt is not None or bold is not None or italic is not None:
        rPr = OxmlElement("w:rPr")

        if zh_font or en_font:
            rFonts = OxmlElement("w:rFonts")
            if en_font:
                rFonts.set(qn("w:ascii"), en_font)
                rFonts.set(qn("w:hAnsi"), en_font)
                rFonts.set(qn("w:cs"), en_font)
            if zh_font:
                rFonts.set(qn("w:eastAsia"), zh_font)
            rPr.append(rFonts)

        if size_pt is not None:
            sz_val = str(max(2, int(round(float(size_pt) * 2))))
            sz = OxmlElement("w:sz")
            sz.set(qn("w:val"), sz_val)
            rPr.append(sz)
            szCs = OxmlElement("w:szCs")
            szCs.set(qn("w:val"), sz_val)
            rPr.append(szCs)

        if bold is not None:
            b = OxmlElement("w:b")
            b.set(qn("w:val"), "1" if bool(bold) else "0")
            rPr.append(b)

        if italic is not None:
            i = OxmlElement("w:i")
            i.set(qn("w:val"), "1" if bool(italic) else "0")
            rPr.append(i)

        lvl.append(rPr)

    abstractNum.append(lvl)
    return abstractNum


def _build_num(num_id: int, abs_id: int):
    """Build a ``w:num`` element that references an ``w:abstractNum``."""
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    abstractNumId = OxmlElement("w:abstractNumId")
    abstractNumId.set(qn("w:val"), str(abs_id))
    num.append(abstractNumId)
    return num


def _insert_before_first_num(nelem, new_abstractNum):
    """Insert abstractNum before the first w:num child (required by schema order)."""
    first_num = None
    for child in nelem:
        if child.tag == qn("w:num"):
            first_num = child
            break
    if first_num is not None:
        nelem.insert(list(nelem).index(first_num), new_abstractNum)
    else:
        nelem.append(new_abstractNum)


def create_list_num_id(
    doc,
    fmt: str,
    left_twips: int = 720,
    hanging_twips: int = 360,
    zh_font: Optional[str] = None,
    en_font: Optional[str] = None,
    size_pt: Optional[float] = None,
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
    start_ordinal: int = 1,
) -> int:
    """
    Add a new single-level numbered list definition to the document's numbering
    part for the given ``fmt`` (one of :data:`LIST_FMTS`).

    ``start_ordinal`` sets the ``w:start`` value of the numbering level.  For
    list groups that begin mid-sequence (e.g. the second item in a different
    table cell), pass the ordinal of the first item so that Word renders the
    correct number (e.g. ``2)`` instead of ``1)``).

    Returns the ``w:numId`` value that should be used in ``w:numPr``.
    """
    nelem = _numbering_element(doc)
    new_id = _next_free_id(nelem)

    abs_num = _build_abstractNum(
        new_id,
        fmt,
        left_twips,
        hanging_twips,
        zh_font=zh_font,
        en_font=en_font,
        size_pt=size_pt,
        bold=bold,
        italic=italic,
        start_ordinal=start_ordinal,
    )
    _insert_before_first_num(nelem, abs_num)

    num = _build_num(new_id, new_id)
    nelem.append(num)

    return new_id


def apply_numpr(paragraph: Paragraph, num_id: int, ilvl: int = 0):
    """Write ``w:numPr`` onto *paragraph*, giving it real Word list numbering."""
    pPr = paragraph._p.get_or_add_pPr()

    # Remove any existing numPr to avoid duplicates
    existing = pPr.find(qn("w:numPr"))
    if existing is not None:
        pPr.remove(existing)

    numPr = OxmlElement("w:numPr")
    ilvl_elem = OxmlElement("w:ilvl")
    ilvl_elem.set(qn("w:val"), str(ilvl))
    numId_elem = OxmlElement("w:numId")
    numId_elem.set(qn("w:val"), str(num_id))
    numPr.append(ilvl_elem)
    numPr.append(numId_elem)
    pPr.append(numPr)


def strip_list_text_prefix(paragraph: Paragraph, prefix_char_count: int):
    """
    Strip the first *prefix_char_count* Unicode codepoints from the paragraph
    text (distributed across runs), removing the text-based list marker that
    was already converted to a real ``w:numPr`` list.

    After stripping, any remaining leading whitespace on the first run is also
    removed so the list text starts cleanly.

    If the paragraph contains no ``w:r`` runs (e.g. text lives in raw XML
    nodes not wrapped by a run), the function is a no-op and the prefix is
    left in place.  This is an uncommon edge case for documents authored
    outside of Word.
    """
    runs = list(paragraph.runs)
    remaining = prefix_char_count
    for run in runs:
        text = run.text or ""
        if remaining <= 0:
            break
        if len(text) <= remaining:
            run.text = ""
            remaining -= len(text)
        else:
            run.text = text[remaining:]
            remaining = 0

    # Strip any leading whitespace left on the first non-empty run
    for run in paragraph.runs:
        if run.text:
            run.text = run.text.lstrip()
            break


# ─── High-level group conversion ─────────────────────────────────────────────

def convert_text_lists(
    doc,
    paragraphs: List[Paragraph],
    get_role,
    is_list_paragraph_fn,
    is_blank_fn,
    min_run_len: int = 1,
    left_twips: int = 720,
    hanging_twips: int = 360,
    zh_font: Optional[str] = None,
    en_font: Optional[str] = None,
    size_pt: Optional[float] = None,
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
) -> Tuple[int, List[Paragraph]]:
    """
    Scan *paragraphs* for runs of consecutive paragraphs whose text begins
    with a recognised list marker and that do **not** already carry ``w:numPr``.

    Eligible paragraphs are those whose role is **not** a structural role
    (heading, caption, abstract, keyword, reference, or footer).  In
    particular both ``list_item`` **and** ``body``/``unknown`` paragraphs are
    eligible, so that documents where the LLM mis-labelled some items in a
    sequence are still converted as a whole group.

    For each run that is at least *min_run_len* items long and uses the same
    list format, this function:

    1. Creates a Word numbering definition (abstractNum + num).
    2. Applies ``w:numPr`` to every paragraph in the run.
    3. Strips the text prefix from each paragraph.

    *min_run_len* controls the minimum consecutive run length required before
    conversion.  The default is 1, meaning even a single isolated list item
    will be converted.  Set to 2 or higher to require at least that many
    consecutive items before converting.

    Returns ``(converted_count, converted_paragraphs)`` where
    *converted_paragraphs* is the list of paragraphs that received ``numPr``.
    """
    # Group consecutive paragraphs that carry a recognised list prefix.
    # A group resets whenever: parent container changes (different table cell or body),
    # blank para, structural role (heading/caption/…), paragraph already carrying real
    # numPr, no detectable prefix, or an incompatible format change.
    # "body" / "unknown" / "list_item" paragraphs are all eligible so that
    # groups where the LLM mis-labelled some items still convert as a whole.
    #
    # Format tolerance: paren_arabic ("（1）") and rparen ("2）") are treated as the
    # same decimal list when ordinals are consecutive.  This handles the common Chinese
    # document pattern where the first item uses full-width opening and closing
    # parentheses while subsequent items use only the right parenthesis.
    groups: List[List[Tuple[Paragraph, str, int, int]]] = []
    current: List[Tuple[Paragraph, str, int, int]] = []
    current_fmt: Optional[str] = None
    current_container = None  # track paragraph parent element for cell-boundary detection

    for p in paragraphs:
        # ── Container boundary: break group when crossing into a different parent
        # element (e.g. from one table cell to another, or body ↔ cell).
        p_container = p._p.getparent()
        if p_container is not current_container:
            if current:
                groups.append(current)
                current = []
                current_fmt = None
            current_container = p_container

        if is_blank_fn(p):
            if current:
                groups.append(current)
                current = []
                current_fmt = None
            continue

        if is_list_paragraph_fn(p):
            # Already carries real numPr – break the current group so it
            # doesn't merge with the preceding/following text-based items.
            if current:
                groups.append(current)
                current = []
                current_fmt = None
            continue

        role = get_role(p)
        if role in _STRUCTURAL_ROLES:
            # Headings, captions, abstracts, etc. always break a group.
            if current:
                groups.append(current)
                current = []
                current_fmt = None
            continue

        text = p.text or ""
        result = detect_text_list_prefix(text)
        if result is None:
            # No detectable numeric prefix – break the group.
            if current:
                groups.append(current)
                current = []
                current_fmt = None
            continue

        fmt, ordinal, prefix_len = result
        # Determine whether this item continues the current group.
        # Normally the format must match exactly.  As a special case, paren_arabic and
        # rparen are treated as compatible when ordinals are consecutive, allowing
        # "（1）first" followed by "2）second" to form a single list group.
        _sequential = (
            current
            and fmt in _DECIMAL_FMTS
            and current_fmt in _DECIMAL_FMTS
            and ordinal == current[-1][2] + 1
        )
        if fmt == current_fmt or _sequential:
            current.append((p, fmt, ordinal, prefix_len))
        else:
            if current:
                groups.append(current)
            current = [(p, fmt, ordinal, prefix_len)]
            current_fmt = fmt

    if current:
        groups.append(current)

    # Convert qualifying groups
    converted = 0
    converted_paras: List[Paragraph] = []
    for group in groups:
        if len(group) < min_run_len:
            continue
        fmt = group[0][1]
        start_ordinal = group[0][2]
        num_id = create_list_num_id(
            doc,
            fmt,
            left_twips,
            hanging_twips,
            zh_font=zh_font,
            en_font=en_font,
            size_pt=size_pt,
            bold=bold,
            italic=italic,
            start_ordinal=start_ordinal,
        )
        for p, _fmt, _ord, prefix_len in group:
            apply_numpr(p, num_id)
            strip_list_text_prefix(p, prefix_len)
            converted += 1
            converted_paras.append(p)

    return converted, converted_paras
