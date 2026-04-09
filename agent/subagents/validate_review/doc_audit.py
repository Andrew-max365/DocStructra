# agent/subagents/validate_review/doc_audit.py
"""
文档排版一致性审阅模块（Feature 3）。

功能：
  audit_document(doc, blocks, labels) → List[AuditIssue]

检查项：
  1. 标题格式一致性（加粗、字号、对齐方式）
  2. 中英文括号混用
  3. 同级标题字体/字号不一致
  4. 正文字号不一致
  5. 编号格式不一致（如有的地方 "1." 有的地方 "（1）"）
  6. 行末标点冗余（段落末尾多余空格/标点）
  7. 全角半角符号混用（逗号、句号等）
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from docx import Document

from agent.subagents.ingest_parse.docx_utils import iter_paragraph_runs as _iter_para_runs

_FONT_SIZE_TOLERANCE_PT = 0.5   # 字号比较容差（pt）
_MAJORITY_THRESHOLD = 0.5        # 视为"多数"的最低占比


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AuditIssue:
    """单条审阅问题。"""
    issue_type: str          # "inconsistency" | "punctuation" | "style" | "structure"
    severity: str            # "high" | "medium" | "low"
    location: str            # 人类可读的位置描述，如"第3段"
    paragraph_index: int     # 0-based 段落索引
    description: str         # 问题描述（中文）
    suggestion: str          # 修改建议（中文）
    evidence: str = ""       # 触发此问题的原始文本摘要

    def to_markdown(self, number: int) -> str:
        severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🔵"}.get(self.severity, "⚪")
        return (
            f"**#{number}** {severity_emoji} [{self.issue_type}] {self.location}\n"
            f"> **问题**：{self.description}\n"
            f"> **建议**：{self.suggestion}\n"
            + (f"> **原文**：`{self.evidence[:80]}`\n" if self.evidence else "")
        )


# ─────────────────────────────────────────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────────────────────────────────────────

def _get_para_text(paragraph) -> str:
    return (paragraph.text or "").strip()


def _get_para_font_size(paragraph) -> Optional[float]:
    """获取段落第一个非空 run 的字号（pt），无则返回 None。"""
    for run in _iter_para_runs(paragraph):
        if run.font.size:
            return run.font.size.pt
    return None


def _get_para_bold(paragraph) -> Optional[bool]:
    """获取段落第一个非空 run 的加粗状态。"""
    for run in _iter_para_runs(paragraph):
        if run.text.strip():
            return run.font.bold
    return None


def _get_para_alignment(paragraph) -> Optional[str]:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    al = paragraph.alignment
    if al is None:
        return None
    mapping = {
        WD_ALIGN_PARAGRAPH.CENTER: "center",
        WD_ALIGN_PARAGRAPH.LEFT: "left",
        WD_ALIGN_PARAGRAPH.RIGHT: "right",
        WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
    }
    return mapping.get(al, None)


# ─────────────────────────────────────────────────────────────────────────────
# 核心审阅函数
# ─────────────────────────────────────────────────────────────────────────────

def audit_document(
    doc: Document,
    blocks: Optional[List[Any]] = None,
    labels: Optional[Dict[Any, str]] = None,
) -> List[AuditIssue]:
    """
    对文档进行排版一致性审阅，返回 AuditIssue 列表。

    :param doc:    python-docx Document 对象
    :param blocks: parse_docx_to_blocks 返回的 Block 列表（可选）
    :param labels: rule_based_labels / route 返回的 labels 字典（可选）
    :return:       AuditIssue 列表（按严重程度排序）
    """
    from agent.subagents.ingest_parse.docx_utils import iter_all_paragraphs

    issues: List[AuditIssue] = []
    paragraphs = iter_all_paragraphs(doc)

    # 构建 (段落, index, 角色) 列表
    # 如果没有传入 labels，使用 detect_role 推断
    para_roles: List[Tuple[Any, int, str]] = []

    # 预建索引：paragraph_index → block_id，避免每个段落都遍历全部 blocks（O(n²) → O(1)）
    block_index_map: Dict[int, Any] = {}
    if labels and blocks:
        for b in blocks:
            block_index_map[b.paragraph_index] = b.block_id

    for idx, p in enumerate(paragraphs):
        if labels and blocks:
            bid = block_index_map.get(idx)
            role = labels.get(bid) if bid is not None else None
        else:
            role = None

        if role is None:
            try:
                from agent.subagents.format_act.formatter import detect_role
                role = detect_role(p)
            except Exception:
                role = "body"
        para_roles.append((p, idx, role))

    # ── 1. 标题加粗一致性 ────────────────────────────────────────────────────
    issues += _check_heading_bold_consistency(para_roles)

    # ── 2. 标题对齐一致性 ────────────────────────────────────────────────────
    issues += _check_heading_alignment_consistency(para_roles)

    # ── 3. 标题字号一致性 ────────────────────────────────────────────────────
    issues += _check_heading_fontsize_consistency(para_roles)

    # ── 4. 中英文括号混用 ────────────────────────────────────────────────────
    issues += _check_bracket_mixing(para_roles)

    # ── 5. 中英文标点混用 ────────────────────────────────────────────────────
    issues += _check_punctuation_mixing(para_roles)

    # ── 6. 正文字号不一致 ────────────────────────────────────────────────────
    issues += _check_body_fontsize_consistency(para_roles)

    # ── 7. 空格/多余字符 ────────────────────────────────────────────────────
    issues += _check_redundant_whitespace(para_roles)

    # 按严重程度排序：high > medium > low
    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda x: order.get(x.severity, 3))

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# 各项检查函数
# ─────────────────────────────────────────────────────────────────────────────

def _check_heading_bold_consistency(
    para_roles: List[Tuple[Any, int, str]]
) -> List[AuditIssue]:
    """检查同级标题加粗状态是否一致。"""
    issues = []
    for level in ("h1", "h2", "h3"):
        heading_paras = [(p, idx) for p, idx, role in para_roles if role == level]
        if len(heading_paras) < 2:
            continue
        bold_states = [(_get_para_bold(p), idx) for p, idx in heading_paras]
        # 统计众数
        valid = [(b, i) for b, i in bold_states if b is not None]
        if not valid:
            continue
        counter = Counter(b for b, _ in valid)
        majority = counter.most_common(1)[0][0]
        for bold_val, idx in valid:
            if bold_val != majority:
                p_text = heading_paras[[i for _, i in heading_paras].index(idx)][0].text[:40]
                issues.append(AuditIssue(
                    issue_type="inconsistency",
                    severity="medium",
                    location=f"第 {idx + 1} 段（{level} 标题）",
                    paragraph_index=idx,
                    description=(
                        f"此 {level} 级标题{'未加粗' if not bold_val else '加粗了'}，"
                        f"但同级其他标题{'加粗' if majority else '未加粗'}，格式不一致。"
                    ),
                    suggestion=f"建议将此标题{'加粗' if majority else '取消加粗'}，以保持同级标题格式统一。",
                    evidence=p_text,
                ))
    return issues


def _check_heading_alignment_consistency(
    para_roles: List[Tuple[Any, int, str]]
) -> List[AuditIssue]:
    """检查同级标题对齐方式是否一致。"""
    issues = []
    for level in ("h1", "h2", "h3"):
        heading_paras = [(p, idx) for p, idx, role in para_roles if role == level]
        if len(heading_paras) < 2:
            continue
        aligns = [(_get_para_alignment(p), idx) for p, idx in heading_paras]
        valid = [(a, i) for a, i in aligns if a is not None]
        if not valid:
            continue
        counter = Counter(a for a, _ in valid)
        majority = counter.most_common(1)[0][0]
        for align_val, idx in valid:
            if align_val != majority:
                p_text = heading_paras[[i for _, i in heading_paras].index(idx)][0].text[:40]
                issues.append(AuditIssue(
                    issue_type="inconsistency",
                    severity="medium",
                    location=f"第 {idx + 1} 段（{level} 标题）",
                    paragraph_index=idx,
                    description=(
                        f"此 {level} 级标题对齐方式为「{align_val}」，"
                        f"但同级其他标题为「{majority}」，对齐方式不一致。"
                    ),
                    suggestion=f"建议将此标题对齐方式改为「{majority}」。",
                    evidence=p_text,
                ))
    return issues


def _check_heading_fontsize_consistency(
    para_roles: List[Tuple[Any, int, str]]
) -> List[AuditIssue]:
    """检查同级标题字号是否一致。"""
    issues = []
    for level in ("h1", "h2", "h3"):
        heading_paras = [(p, idx) for p, idx, role in para_roles if role == level]
        if len(heading_paras) < 2:
            continue
        sizes = [(_get_para_font_size(p), idx) for p, idx in heading_paras]
        valid = [(s, i) for s, i in sizes if s is not None]
        if not valid:
            continue
        counter = Counter(s for s, _ in valid)
        majority_size = counter.most_common(1)[0][0]
        for size_val, idx in valid:
            if abs(size_val - majority_size) > _FONT_SIZE_TOLERANCE_PT:
                p_text = heading_paras[[i for _, i in heading_paras].index(idx)][0].text[:40]
                issues.append(AuditIssue(
                    issue_type="inconsistency",
                    severity="medium",
                    location=f"第 {idx + 1} 段（{level} 标题）",
                    paragraph_index=idx,
                    description=(
                        f"此 {level} 级标题字号为 {size_val:.1f}pt，"
                        f"但同级其他标题字号为 {majority_size:.1f}pt，字号不一致。"
                    ),
                    suggestion=f"建议将此标题字号统一为 {majority_size:.1f}pt。",
                    evidence=p_text,
                ))
    return issues


# 括号模式
_CN_BRACKET_OPEN = re.compile(r"（")
_CN_BRACKET_CLOSE = re.compile(r"）")
_EN_BRACKET_OPEN = re.compile(r"\(")
_EN_BRACKET_CLOSE = re.compile(r"\)")


def _check_bracket_mixing(
    para_roles: List[Tuple[Any, int, str]]
) -> List[AuditIssue]:
    """检查全文中括号（中文全角 vs 英文半角）的混用情况。"""
    issues = []
    cn_bracket_count = 0
    en_bracket_count = 0
    cn_bracket_paras: List[Tuple[int, str]] = []
    en_bracket_paras: List[Tuple[int, str]] = []

    for p, idx, role in para_roles:
        text = _get_para_text(p)
        if not text:
            continue
        cn_cnt = len(_CN_BRACKET_OPEN.findall(text)) + len(_CN_BRACKET_CLOSE.findall(text))
        en_cnt = len(_EN_BRACKET_OPEN.findall(text)) + len(_EN_BRACKET_CLOSE.findall(text))
        cn_bracket_count += cn_cnt
        en_bracket_count += en_cnt
        if cn_cnt > 0:
            cn_bracket_paras.append((idx, text[:60]))
        if en_cnt > 0:
            en_bracket_paras.append((idx, text[:60]))

    # 如果两种括号都存在，且不是单一类型远多于另一类型
    if cn_bracket_count > 0 and en_bracket_count > 0:
        # 判断哪种是"异常"的（少数）
        minority_type, minority_count, minority_paras = (
            ("英文半角括号 ()", en_bracket_count, en_bracket_paras)
            if cn_bracket_count > en_bracket_count
            else ("中文全角括号 （）", cn_bracket_count, cn_bracket_paras)
        )
        majority_type = (
            "中文全角括号 （）"
            if cn_bracket_count > en_bracket_count
            else "英文半角括号 ()"
        )
        # 只报告少数类型的段落（最多报 5 条）
        for para_idx, evidence in minority_paras[:5]:
            issues.append(AuditIssue(
                issue_type="punctuation",
                severity="low",
                location=f"第 {para_idx + 1} 段",
                paragraph_index=para_idx,
                description=(
                    f"此段使用了 {minority_type}，"
                    f"但文档其他地方主要使用 {majority_type}，存在括号类型混用。"
                ),
                suggestion=f"建议将此段括号统一改为 {majority_type}，保持全文一致。",
                evidence=evidence,
            ))
    return issues


# 中文标点
_CN_COMMA = re.compile(r"，")
_CN_PERIOD = re.compile(r"。")
_CN_SEMICOLON = re.compile(r"；")
# 英文标点
_EN_COMMA = re.compile(r"(?<![a-zA-Z0-9]),(?!\d)")  # 排除数字中的逗号
_EN_PERIOD = re.compile(r"(?<![a-zA-Z0-9\d])\.(?!\d)")
_EN_SEMICOLON = re.compile(r";")


def _check_punctuation_mixing(
    para_roles: List[Tuple[Any, int, str]]
) -> List[AuditIssue]:
    """检查中英文标点混用（逗号、句号、分号）。"""
    issues = []

    cn_comma_count = en_comma_count = 0
    cn_semi_count = en_semi_count = 0
    suspicious: List[Tuple[int, str, str]] = []  # (idx, text, issue_desc)

    for p, idx, role in para_roles:
        if role in ("h1", "h2", "h3"):
            continue  # 标题一般不检查标点
        text = _get_para_text(p)
        if not text or len(text) < 5:
            continue

        cn_c = len(_CN_COMMA.findall(text))
        en_c = len(_EN_COMMA.findall(text))
        cn_s = len(_CN_SEMICOLON.findall(text))
        en_s = len(_EN_SEMICOLON.findall(text))

        cn_comma_count += cn_c
        en_comma_count += en_c
        cn_semi_count += cn_s
        en_semi_count += en_s

        # 单段中若同时出现中英文逗号（且不是代码/URL 场景）
        if cn_c > 0 and en_c > 0:
            suspicious.append((idx, text[:60], "同时包含中文逗号「，」和英文逗号「,」"))
        if cn_s > 0 and en_s > 0:
            suspicious.append((idx, text[:60], "同时包含中文分号「；」和英文分号「;」"))

    # 报告段内混用
    for para_idx, evidence, desc in suspicious[:5]:
        issues.append(AuditIssue(
            issue_type="punctuation",
            severity="low",
            location=f"第 {para_idx + 1} 段",
            paragraph_index=para_idx,
            description=f"此段{desc}，存在中英文标点混用。",
            suggestion="请检查此段标点，统一使用中文或英文标点风格。",
            evidence=evidence,
        ))

    # 全文级别：若全文主要用中文逗号但偶尔出现英文逗号
    if cn_comma_count > 5 and 0 < en_comma_count < cn_comma_count // 3:
        issues.append(AuditIssue(
            issue_type="punctuation",
            severity="low",
            location="全文",
            paragraph_index=-1,
            description=(
                f"全文中文逗号共 {cn_comma_count} 处，英文逗号共 {en_comma_count} 处，"
                "疑似存在少量英文逗号混入。"
            ),
            suggestion="建议全文搜索英文逗号「,」并替换为中文逗号「，」（代码/公式段落除外）。",
        ))

    return issues


def _check_body_fontsize_consistency(
    para_roles: List[Tuple[Any, int, str]]
) -> List[AuditIssue]:
    """检查正文段落字号是否一致。"""
    issues = []
    body_paras = [(p, idx) for p, idx, role in para_roles if role in ("body", "unknown")]
    if len(body_paras) < 3:
        return issues

    sizes = [(_get_para_font_size(p), idx) for p, idx in body_paras]
    valid = [(s, i) for s, i in sizes if s is not None]
    if not valid:
        return issues

    counter = Counter(round(s) for s, _ in valid)
    majority_size = counter.most_common(1)[0][0]
    majority_count = counter[majority_size]

    # 只有当众数明显占优时才报告
    if majority_count < len(valid) * _MAJORITY_THRESHOLD:
        return issues

    anomalies = [(s, i) for s, i in valid if abs(round(s) - majority_size) > 1]
    for size_val, idx in anomalies[:5]:
        p_text = body_paras[[i for _, i in body_paras].index(idx)][0].text[:40]
        issues.append(AuditIssue(
            issue_type="style",
            severity="medium",
            location=f"第 {idx + 1} 段（正文）",
            paragraph_index=idx,
            description=(
                f"此正文段落字号为 {size_val:.1f}pt，"
                f"但全文正文主要使用 {majority_size}pt，字号不一致。"
            ),
            suggestion=f"建议将此段字号统一改为 {majority_size}pt。",
            evidence=p_text,
        ))
    return issues


_REDUNDANT_WS = re.compile(r"  +")  # 连续两个或更多空格


def _check_redundant_whitespace(
    para_roles: List[Tuple[Any, int, str]]
) -> List[AuditIssue]:
    """检查段落中多余的空白字符。"""
    issues = []
    for p, idx, role in para_roles:
        text = _get_para_text(p)
        if not text:
            continue
        if _REDUNDANT_WS.search(text):
            issues.append(AuditIssue(
                issue_type="style",
                severity="low",
                location=f"第 {idx + 1} 段",
                paragraph_index=idx,
                description="此段落中包含连续多个空格，可能是手动对齐所致。",
                suggestion="建议使用制表符或段落缩进代替多个空格，以确保排版稳定。",
                evidence=text[:60],
            ))
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# 格式化输出辅助
# ─────────────────────────────────────────────────────────────────────────────

def format_audit_report(issues: List[AuditIssue]) -> str:
    """将审阅结果格式化为 Markdown 字符串。"""
    if not issues:
        return "✅ **文档审阅完成，未发现明显格式不一致问题。**"

    severity_counts = Counter(i.severity for i in issues)
    lines = [
        f"### 📋 文档排版审阅报告（共发现 {len(issues)} 条潜在问题）",
        "",
        f"- 🔴 **高优先级**：{severity_counts.get('high', 0)} 条",
        f"- 🟡 **中优先级**：{severity_counts.get('medium', 0)} 条",
        f"- 🔵 **低优先级**：{severity_counts.get('low', 0)} 条",
        "",
        "---",
        "",
    ]
    for i, issue in enumerate(issues, 1):
        lines.append(issue.to_markdown(i))
        lines.append("")

    return "\n".join(lines)
