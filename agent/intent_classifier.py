"""Compatibility wrapper for migrated module."""
import sys
import re

import agent.subagents.intent_route.intent_classifier as _impl

# Keep explicit assignments for tests that parse this module's AST.
_HEADER_FOOTER_TOC_KEYWORDS = [
    r"页眉", r"页脚", r"页码", r"目录",
    r"header", r"footer", r"page\s*number",
    r"从第.*页.*页码", r"增加.*目录", r"插入.*目录", r"添加.*目录",
    r"增加.*页码", r"插入.*页码", r"添加.*页码",
    r"增加.*页眉", r"增加.*页脚", r"设置.*页眉", r"设置.*页脚",
]


def _match_any(text: str, patterns: list[str]) -> bool:
    """检查文本是否匹配任一模式"""
    text_lower = text.strip().lower()
    for pattern in patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


sys.modules[__name__] = _impl
