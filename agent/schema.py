# agent/schema.py
# 使用 pydantic 定义结构化输出 Schema，供 LLM 输出解析使用
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 校对问题（LLM 仅做错别字/标点/规范性校对，不做结构标注）
# ---------------------------------------------------------------------------

class ProofreadIssue(BaseModel):
    """单条校对问题（由 LLM 校对模块产出）"""

    # 问题类型：错别字 / 标点符号 / 规范性
    issue_type: Literal["typo", "punctuation", "standardization"]
    # 严重程度
    severity: Literal["low", "medium", "high"]
    # 关联段落序号（可选）
    paragraph_index: Optional[int] = None
    # 原文中有问题的片段
    evidence: str
    # 建议的修改内容
    suggestion: str
    # 问题说明
    rationale: str


class DocumentProofread(BaseModel):
    """文档校对结果（仅含错别字、标点、规范性问题，不做结构分析）"""

    doc_language: str = "zh"
    # 校对问题列表（供提交者自行修改，不自动应用）
    issues: List[ProofreadIssue] = []


# ---------------------------------------------------------------------------
# 文档结构分析（SmartJudge 使用）
# ---------------------------------------------------------------------------

class ParagraphRole(BaseModel):
    """单段落的结构分析结果"""

    # 段落序号（0-based，与 Block.paragraph_index 对应）
    paragraph_index: int
    # LLM 判定的段落角色
    role: Literal["h1", "h2", "h3", "body", "caption", "abstract", "keyword",
                  "reference", "footer", "list_item", "blank"]
    # 置信度（0.0–1.0）
    confidence: float = Field(ge=0.0, le=1.0)
    # 判断依据
    reason: str = ""


class DocumentStructureAnalysis(BaseModel):
    """文档结构分析结果（用于 SmartJudge 仲裁）"""

    paragraphs: List[ParagraphRole] = []
