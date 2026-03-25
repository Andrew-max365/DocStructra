from __future__ import annotations

"""Template hub + domain router.

目标：
- 为用户指令选择最合适的 spec 模板（default/academic/gov/contract）
- 保留 Agent 能力：规则只做快速先验，LLM 仍可通过 parse 结果覆写模板
"""

from dataclasses import dataclass
from typing import Dict, Optional
import re


@dataclass
class TemplateDecision:
    spec_path: str
    domain: str
    confidence: float
    source: str  # rule | llm | keep
    reason: str


_DOMAIN_TO_SPEC = {
    "default": "specs/default.yaml",
    "academic": "specs/academic.yaml",
    "gov": "specs/gov.yaml",
    "contract": "specs/contract.yaml",
}


_RULES = {
    "gov": [
        r"公文", r"红头", r"请示", r"通知", r"党政", r"机关", r"纪要",
        r"决定", r"通报", r"函",
    ],
    "contract": [
        r"合同", r"协议", r"甲方", r"乙方", r"违约", r"签章", r"补充协议",
        r"保密协议", r"采购合同",
    ],
    "academic": [
        r"论文", r"学位", r"毕业设计", r"摘要", r"关键词", r"参考文献",
        r"APA", r"MLA", r"期刊", r"投稿",
    ],
}


def _score_domain(text: str) -> Dict[str, int]:
    t = (text or "").strip().lower()
    scores = {"default": 0, "academic": 0, "gov": 0, "contract": 0}
    for domain, patterns in _RULES.items():
        for p in patterns:
            if re.search(p, t, re.IGNORECASE):
                scores[domain] += 1
    return scores


def resolve_template(
    user_text: str,
    *,
    current_spec_path: Optional[str] = None,
    llm_meta: Optional[dict] = None,
) -> TemplateDecision:
    """
    模板决策策略（混合）：
    1) 若 LLM 明确返回 domain/spec_path，优先采用（保留 Agent 工具能力）
    2) 否则用规则关键词打分
    3) 若分值不足，沿用当前模板
    """
    # 1) LLM meta 覆写
    if llm_meta:
        llm_spec = llm_meta.get("spec_path")
        llm_domain = (llm_meta.get("domain") or "").strip().lower()
        if isinstance(llm_spec, str) and llm_spec in _DOMAIN_TO_SPEC.values():
            domain = next((d for d, p in _DOMAIN_TO_SPEC.items() if p == llm_spec), "default")
            return TemplateDecision(
                spec_path=llm_spec,
                domain=domain,
                confidence=0.92,
                source="llm",
                reason="LLM 指令解析中明确指定模板",
            )
        if llm_domain in _DOMAIN_TO_SPEC:
            return TemplateDecision(
                spec_path=_DOMAIN_TO_SPEC[llm_domain],
                domain=llm_domain,
                confidence=0.88,
                source="llm",
                reason="LLM 指令解析中明确识别领域",
            )

    # 2) 规则评分
    scores = _score_domain(user_text)
    best_domain = max(scores, key=scores.get)
    best_score = scores[best_domain]
    if best_domain != "default" and best_score >= 1:
        confidence = min(0.55 + 0.15 * best_score, 0.85)
        return TemplateDecision(
            spec_path=_DOMAIN_TO_SPEC[best_domain],
            domain=best_domain,
            confidence=confidence,
            source="rule",
            reason=f"关键词命中 {best_score} 项",
        )

    # 3) 低置信保持当前模板
    keep = current_spec_path or _DOMAIN_TO_SPEC["default"]
    keep_domain = next((d for d, p in _DOMAIN_TO_SPEC.items() if p == keep), "default")
    return TemplateDecision(
        spec_path=keep,
        domain=keep_domain,
        confidence=0.4,
        source="keep",
        reason="未识别出强领域信号，保持当前模板",
    )
