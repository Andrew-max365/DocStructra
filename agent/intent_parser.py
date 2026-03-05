# agent/intent_parser.py
"""
意图解析器：将用户自然语言中的排版需求转换为结构化配置字典。
例："把标题改成黑色" → {"heading": {"h1": {"color": "000000"}, "h2": {"color": "000000"}, "h3": {"color": "000000"}}}
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

_INTENT_PARSE_TIMEOUT = 15.0   # 意图解析 API 超时（秒）
_INTENT_PARSE_MAX_TOKENS = 300  # 意图解析最大输出 token 数

_FORMATTING_SYSTEM_PROMPT = (
    "你是一个排版参数解析助手。"
    "用户会用自然语言描述文档格式修改需求（如颜色、字号等），你需要识别这些需求并严格输出 JSON 格式的配置字典。\n\n"
    "支持的配置结构（仅输出用户明确提及的字段）：\n"
    '{"heading": {"h1": {"color": "000000"}, "h2": {"color": "FF0000"}, "h3": {}}, "body": {"color": "333333"}}\n\n'
    "规则：\n"
    "  1. color 字段使用六位十六进制字符串，不含 # 号，如 000000（黑色）、FF0000（红色）。\n"
    "  2. 若用户说"标题"且未指定级别，则同时填充 h1、h2、h3。\n"
    "  3. 若未提及任何排版需求，则输出空 JSON：{}\n"
    "  4. 不要输出除 JSON 以外的任何说明文字。"
)


async def parse_formatting_intent(user_text: str) -> Optional[Dict[str, Any]]:
    """
    解析用户自然语言中的排版意图，返回 overrides 字典。

    若未检测到排版需求，返回 None。
    若调用失败（无 API Key、网络错误等），同样返回 None（静默失败，不影响正常对话）。

    :param user_text: 用户消息内容
    :return: 可直接传入 load_spec(overrides=...) 的字典，或 None
    """
    if not LLM_API_KEY:
        return None

    try:
        import openai as _openai

        client = _openai.AsyncOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            timeout=_INTENT_PARSE_TIMEOUT,
        )
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _FORMATTING_SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            max_tokens=_INTENT_PARSE_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""

        # 用正则提取 JSON 对象，防止 LLM 添加废话文字
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None

        data = json.loads(match.group(0))
        if not isinstance(data, dict) or not data:
            return None
        return data

    except Exception:
        return None
