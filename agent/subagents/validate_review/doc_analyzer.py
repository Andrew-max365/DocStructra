# agent/doc_analyzer.py
# 文档结构分析 Agent：封装 LLMClient 的懒加载构造，供 ModeRouter 使用
from __future__ import annotations

from typing import Optional

from agent.llm_client import LLMClient


class DocAnalyzer:
    """
    LLMClient 工厂：按需初始化 LLMClient，避免在 rule 模式下触发 API Key 检查。
    ModeRouter 通过 analyzer.client 访问底层 LLMClient 实例。
    """

    def __init__(self, client: Optional[LLMClient] = None):
        # 允许注入自定义 LLMClient，便于测试和替换
        self.client = client or LLMClient()
