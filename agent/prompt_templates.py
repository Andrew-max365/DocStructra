# agent/prompt_templates.py
# Prompt 模板管理：系统 Prompt 和用户 Prompt 模板
from typing import List, Optional


# ---------------------------------------------------------------------------
# 校对 Prompt（LLM 仅做错别字/标点/规范性校对，不做结构标注）
# ---------------------------------------------------------------------------

PROOFREAD_SYSTEM_PROMPT = (
    "你是一个专业的中文文档校对专家，负责检查文档中的错别字、标点符号使用错误以及规范性问题。\n"
    "你的任务是：对给定的文档段落进行校对，仅找出以下三类问题：\n"
    "  1. 错别字（typo）：错误使用的汉字、词语\n"
    "  2. 标点符号（punctuation）：标点使用不当、中英文标点混用、句末标点缺漏等\n"
    "  3. 规范性问题（standardization）：数字写法不规范、用语不统一、格式不统一等\n"
    "你必须严格按照 JSON Schema 输出，不得包含任何额外说明文字。\n"
    "输出的 JSON 必须包含字段：doc_language, issues（数组）。\n"
    "每条问题必须包含以下字段：\n"
    "  issue_type: typo（错别字）/ punctuation（标点）/ standardization（规范性）\n"
    "  severity: low / medium / high\n"
    "  paragraph_index（可选）: 问题所在段落序号\n"
    "  evidence: 原文中有问题的片段（原样引用）\n"
    "  suggestion: 建议的修改内容\n"
    "  rationale: 问题说明\n"
    "只报告真实存在的问题，不要无中生有。若无问题，issues 数组返回空即可。\n"
    "校对结果仅供提交者参考自行修改，不会被自动应用。"
)


def build_proofread_prompt(
    paragraphs: List[str],
    paragraph_indices: Optional[List[int]] = None,
) -> str:
    """
    构造校对用户 Prompt。

    :param paragraphs: 全部段落文本列表（按原始文档顺序）
    :param paragraph_indices: 仅校对这些序号的段落（hybrid 模式下非空）；
                              None 表示全量校对（llm 模式）
    :return: 格式化后的用户 Prompt 字符串
    """
    if paragraph_indices is not None:
        indices_to_check = sorted(paragraph_indices)
        n = len(indices_to_check)
        lines = "\n".join(
            f"  序号{i}: \"{paragraphs[i][:200]}{'...' if len(paragraphs[i]) > 200 else ''}\""
            for i in indices_to_check if i < len(paragraphs)
        )
        return (
            f"请对以下 {n} 个段落进行错别字、标点符号及规范性校对：\n\n"
            f"{lines}\n\n"
            "请输出符合 Schema 的 JSON。"
        )
    else:
        n = len(paragraphs)
        lines = "\n".join(
            f"  序号{i}: \"{text[:200]}{'...' if len(text) > 200 else ''}\""
            for i, text in enumerate(paragraphs)
        )
        return (
            f"请对以下中文文档（共 {n} 个段落）进行错别字、标点符号及规范性校对：\n\n"
            f"{lines}\n\n"
            "请输出符合 Schema 的 JSON。"
        )
