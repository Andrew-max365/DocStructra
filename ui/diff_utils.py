# ui/diff_utils.py
"""
Utilities for generating diff views and applying LLM proofread suggestions.

Provides:
  - DiffItem: a numbered, human-readable description of a single text change
  - build_diff_items(): convert raw proofread issue dicts → [DiffItem]
  - parse_rejected_numbers(): parse user text like "不要修改#3 #5" → {3, 5}
  - apply_proofread_issues(): apply text replacements to a python-docx Document
  - apply_and_save_proofread(): convenience wrapper – load bytes, apply, save bytes
  - generate_structural_diff(): markdown summary of structural formatting changes
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# DiffItem – one numbered proofread change
# ---------------------------------------------------------------------------

_ISSUE_ICONS: Dict[str, str] = {
    "typo": "🔤",
    "punctuation": "🔡",
    "standardization": "📏",
}
_SEVERITY_ICONS: Dict[str, str] = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🔴",
}
_ISSUE_LABELS: Dict[str, str] = {
    "typo": "错别字",
    "punctuation": "标点符号",
    "standardization": "规范性",
}


@dataclass
class DiffItem:
    """A numbered diff item representing a single proofread text change."""

    number: int
    issue_type: str          # "typo" | "punctuation" | "standardization"
    severity: str            # "low" | "medium" | "high"
    para_idx: Optional[int]  # 0-based paragraph index, if known
    evidence: str            # original text fragment
    suggestion: str          # suggested replacement
    rationale: str           # explanation

    def to_markdown(self) -> str:
        """Render as a compact markdown diff card."""
        icon = _ISSUE_ICONS.get(self.issue_type, "📝")
        sev = _SEVERITY_ICONS.get(self.severity, "")
        label = _ISSUE_LABELS.get(self.issue_type, self.issue_type)
        location = f" (段落 {self.para_idx})" if self.para_idx is not None else ""
        return (
            f"**#{self.number}** {icon}{sev} `{label}`{location}\n"
            f"  - ❌ 原文片段：`{self.evidence}`\n"
            f"  - ✅ 建议修改：`{self.suggestion}`\n"
            f"  - 💡 说明：{self.rationale}"
        )


def build_diff_items(issues: List[dict]) -> List[DiffItem]:
    """
    Convert a list of raw proofread issue dicts (from report['llm_proofread']['issues'])
    into numbered DiffItem objects.
    """
    items: List[DiffItem] = []
    for i, issue in enumerate(issues, start=1):
        evidence = (issue.get("evidence") or "").strip()
        suggestion = (issue.get("suggestion") or "").strip()
        if not evidence or not suggestion:
            continue
        items.append(
            DiffItem(
                number=i,
                issue_type=issue.get("issue_type", "standardization"),
                severity=issue.get("severity", "low"),
                para_idx=issue.get("paragraph_index"),
                evidence=evidence,
                suggestion=suggestion,
                rationale=(issue.get("rationale") or ""),
            )
        )
    return items


# ---------------------------------------------------------------------------
# Parsing user rejection commands
# ---------------------------------------------------------------------------

# Patterns that signal "accept all" (i.e., no rejections)
_ACCEPT_ALL_PATTERNS = re.compile(
    r"(全部接受|全部同意|全部确认|确认所有|accept\s*all|接受全部|同意全部|应用全部|全部应用)",
    re.IGNORECASE,
)
# Patterns that signal "reject all"
_REJECT_ALL_PATTERNS = re.compile(
    r"(全部不要|全部拒绝|不要任何|全部保留原文|reject\s*all|不接受任何|全部回退)",
    re.IGNORECASE,
)


def parse_rejected_numbers(user_text: str, total: int) -> Tuple[Set[int], str]:
    """
    Parse user text to find which change numbers are being rejected.

    Returns (rejected_set, intent) where intent is one of:
      "accept_all"  – apply every change
      "reject_all"  – apply no change
      "partial"     – apply some, reject the identified numbers

    Examples handled:
      "不要修改#3"            → ({3}, "partial")
      "不要改#3 和 #5"        → ({3, 5}, "partial")
      "reject 3, 5"           → ({3, 5}, "partial")
      "不要第3条和第5条"      → ({3, 5}, "partial")
      "全部接受"              → ({}, "accept_all")
      "全部不要"              → (set(range(1, total+1)), "reject_all")
    """
    if _ACCEPT_ALL_PATTERNS.search(user_text):
        return set(), "accept_all"
    if _REJECT_ALL_PATTERNS.search(user_text):
        return set(range(1, total + 1)), "reject_all"

    rejected: Set[int] = set()

    # Match explicit #N patterns first (highest confidence)
    for m in re.finditer(r"#\s*(\d+)", user_text):
        n = int(m.group(1))
        if 1 <= n <= total:
            rejected.add(n)

    # Match "第N条" / "第N个" patterns
    for m in re.finditer(r"第\s*(\d+)\s*[条个项]", user_text):
        n = int(m.group(1))
        if 1 <= n <= total:
            rejected.add(n)

    # If nothing matched yet, try standalone numbers (less confident – only when
    # "rejection" context is present in the message)
    if not rejected:
        rejection_keywords = re.compile(
            r"(不要|拒绝|取消|不接受|skip|reject|ignore|不改|保留原文|撤销)",
            re.IGNORECASE,
        )
        if rejection_keywords.search(user_text):
            for m in re.finditer(r"\b(\d+)\b", user_text):
                n = int(m.group(1))
                if 1 <= n <= total:
                    rejected.add(n)

    intent = "partial" if rejected else "accept_all"
    return rejected, intent


# ---------------------------------------------------------------------------
# Applying proofread issues to a python-docx Document
# ---------------------------------------------------------------------------

def _replace_text_in_paragraph(para, old_text: str, new_text: str) -> bool:
    """
    Replace the first occurrence of *old_text* with *new_text* inside *para*.

    Tries single-run replacement first; falls back to a multi-run approach where
    all run texts are merged, the substitution is performed, and the combined
    text is placed back in the first run (remaining runs are cleared).

    Returns True if a replacement was made.
    """
    # Fast path: the text is entirely within one run
    for run in para.runs:
        if old_text in run.text:
            run.text = run.text.replace(old_text, new_text, 1)
            return True

    # Slow path: text spans multiple runs
    runs = list(para.runs)
    if not runs:
        return False
    full = "".join(r.text for r in runs)
    pos = full.find(old_text)
    if pos < 0:
        return False
    new_full = full[:pos] + new_text + full[pos + len(old_text):]
    runs[0].text = new_full
    for run in runs[1:]:
        run.text = ""
    return True


def apply_proofread_issues(
    doc,
    issues: List[dict],
    excluded_numbers: Optional[Set[int]] = None,
) -> int:
    """
    Apply proofread issues (text replacements) to a python-docx Document in-place.

    :param doc:               python-docx Document object (modified in-place)
    :param issues:            list of raw issue dicts (from report['llm_proofread']['issues'])
    :param excluded_numbers:  1-based set of issue numbers to skip
    :return:                  number of replacements successfully applied
    """
    if not issues:
        return 0

    from core.docx_utils import iter_all_paragraphs

    excluded = excluded_numbers or set()
    paragraphs = iter_all_paragraphs(doc)
    applied = 0

    for i, issue in enumerate(issues, start=1):
        if i in excluded:
            continue
        evidence = (issue.get("evidence") or "").strip()
        suggestion = (issue.get("suggestion") or "").strip()
        if not evidence or not suggestion or evidence == suggestion:
            continue

        para_idx = issue.get("paragraph_index")
        if para_idx is not None and 0 <= para_idx < len(paragraphs):
            if _replace_text_in_paragraph(paragraphs[para_idx], evidence, suggestion):
                applied += 1
                continue
        # Fallback: search all paragraphs
        for para in paragraphs:
            if evidence in para.text:
                if _replace_text_in_paragraph(para, evidence, suggestion):
                    applied += 1
                    break

    return applied


def apply_and_save_proofread(
    output_bytes: bytes,
    issues: List[dict],
    excluded_numbers: Optional[Set[int]] = None,
) -> Tuple[bytes, int]:
    """
    Load a docx from *output_bytes*, apply proofread issues (excluding
    *excluded_numbers*), and return the modified docx bytes + count applied.
    """
    from docx import Document
    from core.writer import save_docx

    tmp_in = tmp_out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(output_bytes)
            tmp_in = f.name
        tmp_out = tmp_in + "_proofread.docx"

        doc = Document(tmp_in)
        applied = apply_proofread_issues(doc, issues, excluded_numbers)
        save_docx(doc, tmp_out)

        with open(tmp_out, "rb") as f:
            result_bytes = f.read()

        return result_bytes, applied

    finally:
        for p in (tmp_in, tmp_out):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# HTML side-by-side diff table
# ---------------------------------------------------------------------------

import difflib
import html as _html_module


def _char_diff_html(old_text: str, new_text: str) -> Tuple[str, str]:
    """
    Return (old_html, new_html) strings with character-level diff highlighted.
    Deletions are wrapped in ``<span class="del">`` and insertions in
    ``<span class="ins">`` for CSS styling.
    """
    old_chars = list(old_text)
    new_chars = list(new_text)
    matcher = difflib.SequenceMatcher(None, old_chars, new_chars, autojunk=False)

    old_parts: List[str] = []
    new_parts: List[str] = []

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        old_seg = _html_module.escape("".join(old_chars[i1:i2]))
        new_seg = _html_module.escape("".join(new_chars[j1:j2]))
        if op == "equal":
            old_parts.append(old_seg)
            new_parts.append(new_seg)
        elif op == "replace":
            old_parts.append(f'<span class="del">{old_seg}</span>')
            new_parts.append(f'<span class="ins">{new_seg}</span>')
        elif op == "delete":
            old_parts.append(f'<span class="del">{old_seg}</span>')
        elif op == "insert":
            new_parts.append(f'<span class="ins">{new_seg}</span>')

    return "".join(old_parts), "".join(new_parts)


_DIFF_TABLE_STYLE = """
<style>
.diff-wrap{overflow-x:auto;margin:8px 0}
.diff-tbl{border-collapse:collapse;width:100%;font-size:13px;font-family:sans-serif}
.diff-tbl th{background:#f5f5f5;padding:6px 10px;text-align:left;border:1px solid #ddd;white-space:nowrap}
.diff-tbl td{padding:6px 10px;border:1px solid #ddd;vertical-align:top}
.diff-tbl .c-num{width:36px;font-weight:700;color:#555;white-space:nowrap}
.diff-tbl .c-tag{width:110px;white-space:nowrap}
.diff-tbl .c-old{background:#fff5f5}
.diff-tbl .c-new{background:#f5fff5}
.diff-tbl .c-note{color:#666;font-size:12px}
span.del{background:#ffc8c8;text-decoration:line-through}
span.ins{background:#c8ffc8}
</style>
"""


def generate_diff_html(diff_items: List[DiffItem]) -> str:
    """
    Generate an HTML string containing a side-by-side diff table for *diff_items*.

    The table has columns: #, 类型, ❌ 原文, ✅ 建议, 说明.
    Character-level differences are highlighted with ``<span class="del/ins">``.
    """
    rows: List[str] = []
    for item in diff_items:
        icon = _ISSUE_ICONS.get(item.issue_type, "📝")
        sev = _SEVERITY_ICONS.get(item.severity, "")
        label = _ISSUE_LABELS.get(item.issue_type, item.issue_type)
        location = f"<br><small>段落 {item.para_idx}</small>" if item.para_idx is not None else ""

        old_html, new_html = _char_diff_html(item.evidence, item.suggestion)
        note_html = _html_module.escape(item.rationale)

        rows.append(
            f"<tr>"
            f'<td class="c-num">#{item.number}</td>'
            f'<td class="c-tag">{icon}{sev} {_html_module.escape(label)}{location}</td>'
            f'<td class="c-old">{old_html}</td>'
            f'<td class="c-new">{new_html}</td>'
            f'<td class="c-note">{note_html}</td>'
            f"</tr>"
        )

    header = (
        "<tr>"
        "<th>#</th>"
        "<th>类型</th>"
        "<th>❌ 原文</th>"
        "<th>✅ 建议</th>"
        "<th>说明</th>"
        "</tr>"
    )
    return (
        f"{_DIFF_TABLE_STYLE}"
        f'<div class="diff-wrap">'
        f'<table class="diff-tbl">'
        f"<thead>{header}</thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"</table></div>"
    )


# ---------------------------------------------------------------------------
# Structural diff summary
# ---------------------------------------------------------------------------

def generate_structural_diff(report: dict) -> str:
    """
    Generate a markdown summary of the structural formatting changes recorded
    in the *actions* section of the format report.
    """
    actions = report.get("actions", {})
    if not actions:
        return ""

    lines: List[str] = []

    _MAPPING = [
        ("h1_applied",         "一级标题样式应用"),
        ("h2_applied",         "二级标题样式应用"),
        ("h3_applied",         "三级标题样式应用"),
        ("body_applied",       "正文样式应用"),
        ("caption_applied",    "题注样式应用"),
        ("abstract_applied",   "摘要样式应用"),
        ("keyword_applied",    "关键词样式应用"),
        ("reference_applied",  "参考文献样式应用"),
        ("list_converted",     "列表项转换（numPr）"),
        ("tables_autofitted",  "表格自动适配"),
        ("blank_removed",      "空段落清理"),
    ]

    for key, label in _MAPPING:
        n = actions.get(key, 0)
        if n:
            lines.append(f"- **{n}** 处 {label}")

    return "\n".join(lines)
