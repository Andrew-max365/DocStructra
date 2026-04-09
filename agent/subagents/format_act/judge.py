# core/judge.py
import re
import warnings
from typing import Dict, List

from agent.subagents.ingest_parse.parser import Block
from agent.subagents.ingest_parse.docx_utils import iter_all_paragraphs

# 兼容：无 doc 情况下的简易规则（保留）
RE_H1 = re.compile(r"^\s*第[一二三四五六七八九十百千0-9]+章")
RE_H2 = re.compile(r"^\s*第[一二三四五六七八九十百千0-9]+节")
RE_CN_ENUM = re.compile(r"^\s*[一二三四五六七八九十百千万]+、")
RE_NUM_DOT = re.compile(r"^\s*\d+(\.\d+){0,3}\s+")  # 1 / 1.1 / 1.1.1

def rule_based_labels(blocks: List[Block], doc=None) -> Dict[int, str]:
    """
    returns: {block_id: role} where role in:
      - blank
      - h1 / h2 / h3
      - caption
      - body

    优化点：
    - 如果传入 doc（python-docx Document），则优先对“真实段落对象”调用 formatter.detect_role，
      这样会考虑 Word 标题样式、题注、以及段内多行结构等，和后续 apply_formatting 的 fallback 规则保持一致。
    - 若未传入 doc，则退回到简单 regex 规则（兼容旧调用方式）。
    """
    labels: Dict[int, str] = {}

    if doc is not None:
        try:
            # 注意：这里导入的是你优化后的 formatter（若你直接替换文件名，可改回 .formatter）
            from .formatter import detect_role
            paras = iter_all_paragraphs(doc)
            for b in blocks:
                p = paras[b.paragraph_index] if 0 <= b.paragraph_index < len(paras) else None
                if p is None:
                    # 段落越界则退回文本规则
                    text = (b.text or "").strip()
                    labels[b.block_id] = "blank" if text == "" else "body"
                    continue
                labels[b.block_id] = detect_role(p)
            return labels
        except Exception as e:
            warnings.warn(
                f"[judge] detect_role via doc failed, falling back to regex rules: {e}",
                stacklevel=2,
            )

    # -------- fallback: regex rules over plain text --------
    for b in blocks:
        text = (b.text or "").strip()
        if text == "":
            labels[b.block_id] = "blank"
            continue

        if RE_H1.match(text):
            labels[b.block_id] = "h1"
        elif RE_H2.match(text):
            labels[b.block_id] = "h2"
        elif RE_NUM_DOT.match(text):
            depth = text.split()[0].count(".")
            labels[b.block_id] = "h2" if depth <= 0 else "h3"
        elif RE_CN_ENUM.match(text):
            labels[b.block_id] = "h2"
        else:
            labels[b.block_id] = "body"

    return labels


# ---------------------------------------------------------------------------
# SmartJudge：规则 + LLM 置信度仲裁
# ---------------------------------------------------------------------------

# "硬核规则"正则——面向中文文档的明确章节标记。
# 这些模式在中文学术/公文文档中具有极高的准确率，优先于 LLM 的判断。
# 注意：当前仅覆盖中文"第X章/节"形式；英文文档标题识别依赖 detect_role 的 Word 样式规则。
_RE_HARD_H1 = re.compile(r"^\s*第[一二三四五六七八九十百千0-9]+章")
_RE_HARD_H2 = re.compile(r"^\s*第[一二三四五六七八九十百千0-9]+节")


class SmartJudge:
    """
    规则 + LLM 仲裁器：在规则标签的基础上，用 LLM 语义结果进行校验和覆盖。

    仲裁逻辑：
    - 若 LLM 置信度 >= threshold（默认 0.8）且规则判定为 body，则信任 LLM 的特殊标签。
    - 若文本命中"硬核规则"（如明确的"第一章"），则保守信任规则，忽略 LLM 结果。
    - 其余情况保持规则标签。
    """

    def __init__(self, confidence_threshold: float = 0.8):
        """
        :param confidence_threshold: LLM 置信度阈值（默认 0.8）。
            选择 0.8 是经验值：既保证 LLM 高确信时能纠正规则误分类，
            又避免低置信推测引入噪声。可在实例化时根据业务需求调整。
        """
        self.confidence_threshold = confidence_threshold

    def arbitrate(self, text: str, rule_role: str, llm_response_dict: dict) -> str:
        """
        对单段进行仲裁。

        :param text: 段落文本
        :param rule_role: 规则层给出的角色
        :param llm_response_dict: LLM 给出的单段结果，含 role、confidence 字段
        :return: 最终采用的角色
        """
        llm_role = llm_response_dict.get("role", rule_role)
        confidence = float(llm_response_dict.get("confidence", 0.0))

        # 硬核规则：第X章/第X节 → 绝对信任规则
        t = (text or "").strip()
        if _RE_HARD_H1.match(t) or _RE_HARD_H2.match(t):
            return rule_role

        # LLM 高置信 + 规则认为是 body → 信任 LLM 的特殊识别
        if confidence >= self.confidence_threshold and rule_role == "body":
            return llm_role

        return rule_role
