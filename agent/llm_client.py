# agent/llm_client.py
# 封装对大模型 API 的调用（使用 openai SDK）
from __future__ import annotations

import json
import time
from typing import Any, List, Optional

import openai
import pydantic

from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TIMEOUT_S,
    LLM_CONNECT_TIMEOUT_S,
    LLM_MAX_TIMEOUT_S,
    LLM_RETRY_ATTEMPTS,
    LLM_RETRY_BACKOFF_S,
)
from agent.prompt_templates import (
    PROOFREAD_SYSTEM_PROMPT, build_proofread_prompt,
)
from agent.schema import DocumentProofread, ProofreadIssue


def compute_dynamic_timeout(n_paragraphs: int) -> int:
    """
    根据段落数量动态计算读取超时时间（秒）。

    公式：LLM_TIMEOUT_S + n_paragraphs * 0.5，结果限制在 [LLM_TIMEOUT_S, LLM_MAX_TIMEOUT_S]。

    :param n_paragraphs: 送入 LLM 的段落数量
    :return: 建议的读取超时秒数
    """
    dynamic = LLM_TIMEOUT_S + int(n_paragraphs * 0.5)
    return min(dynamic, LLM_MAX_TIMEOUT_S)


class LLMCallError(Exception):
    """LLM 调用失败时抛出的自定义异常"""
    def __init__(self, message: str, error_type: str = "unknown"):
        super().__init__(message)
        self.error_type = error_type  # "timeout" | "read_timeout" | "connect_timeout" | "connect_error" | "auth" | "format_error" | "unknown"


class LLMClient:
    """
    大模型 API 客户端，封装调用逻辑、超时控制与异常处理。
    兼容 OpenAI 接口规范，支持通过 LLM_BASE_URL 切换到国产模型端点。
    """

    def __init__(self):
        # API Key 不能为空（llm/hybrid 模式下必须设置 LLM_API_KEY）
        if not LLM_API_KEY:
            raise LLMCallError(
                "LLM_API_KEY 未设置。请通过环境变量 LLM_API_KEY 提供大模型 API 密钥。"
            )
        # 初始化 OpenAI 客户端，支持自定义 base_url 和超时
        # 使用 openai.Timeout 分别设置连接超时与读取超时，改善连接阶段的诊断能力
        self.client = openai.OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            timeout=openai.Timeout(LLM_TIMEOUT_S, connect=LLM_CONNECT_TIMEOUT_S),
        )

    def _execute_chat_completion(self, messages: list, timeout: int | None = None) -> str:
        """
        执行聊天补全调用，支持自动重试（指数退避）与详细超时类型分类。

        :param messages: 消息列表（system + user）
        :param timeout: 读取超时秒数；None 时使用客户端默认值
        :return: 模型输出内容字符串
        :raises LLMCallError: 调用失败时抛出（含 error_type）
        """
        call_timeout = (
            openai.Timeout(timeout, connect=LLM_CONNECT_TIMEOUT_S)
            if timeout is not None
            else None
        )
        last_error: LLMCallError | None = None
        for attempt in range(1, LLM_RETRY_ATTEMPTS + 1):
            try:
                kwargs: dict = dict(
                    model=LLM_MODEL,
                    messages=messages,
                    response_format={"type": "json_object"},
                )
                if call_timeout is not None:
                    kwargs["timeout"] = call_timeout
                response = self.client.chat.completions.create(**kwargs)
                return response.choices[0].message.content
            except openai.APITimeoutError as e:
                # 尝试从底层 httpx 异常区分连接超时与读取超时
                cause = getattr(e, "__cause__", None)
                cause_name = type(cause).__name__ if cause is not None else ""
                if "Connect" in cause_name:
                    kind, err_type = "连接超时", "connect_timeout"
                elif "Read" in cause_name:
                    kind, err_type = "读取超时", "read_timeout"
                else:
                    kind, err_type = "请求超时", "timeout"
                last_error = LLMCallError(
                    f"LLM {kind} (尝试 {attempt}/{LLM_RETRY_ATTEMPTS}): {e}",
                    error_type=err_type,
                )
            except openai.APIConnectionError as e:
                last_error = LLMCallError(
                    f"LLM 网络连接失败 (尝试 {attempt}/{LLM_RETRY_ATTEMPTS}): {e}",
                    error_type="connect_error",
                )
            except openai.AuthenticationError as e:
                raise LLMCallError(f"LLM 鉴权失败: {e}", error_type="auth") from e
            except Exception as e:
                raise LLMCallError(f"LLM 调用失败: {e}", error_type="unknown") from e

            if attempt < LLM_RETRY_ATTEMPTS:
                backoff = LLM_RETRY_BACKOFF_S * (2 ** (attempt - 1))
                time.sleep(backoff)

        raise last_error  # type: ignore[misc]

    def call_proofread(
        self,
        paragraphs: List[str],
        paragraph_indices: Optional[List[int]] = None,
    ) -> "DocumentProofread":
        """
        调用大模型进行校对，返回 DocumentProofread（含错别字/标点/规范性问题列表）。

        :param paragraphs: 文档全部段落文本列表
        :param paragraph_indices: 仅校对这些序号的段落（hybrid 模式）；None 表示全量（llm 模式）
        :return: DocumentProofread 实例
        :raises LLMCallError: 调用失败或解析失败时抛出
        """
        try:
            user_prompt = build_proofread_prompt(paragraphs, paragraph_indices)
            messages = [
                {"role": "system", "content": PROOFREAD_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            n = len(paragraph_indices) if paragraph_indices is not None else len(paragraphs)
            raw = self._execute_chat_completion(
                messages, timeout=compute_dynamic_timeout(n)
            )
            data = json.loads(self._normalize_json_text(raw))
            data = self._canonicalize_proofread_payload(data)
            return DocumentProofread(**data)
        except LLMCallError:
            raise
        except json.JSONDecodeError as e:
            raise LLMCallError(f"LLM 校对响应 JSON 解析失败: {e}", error_type="format_error") from e
        except pydantic.ValidationError as e:
            raise LLMCallError(f"LLM 校对响应结构校验失败: {e}", error_type="format_error") from e
        except Exception as e:
            raise LLMCallError(f"LLM 校对调用失败: {e}", error_type="unknown") from e

    @classmethod
    def _canonicalize_proofread_issue(cls, item: Any) -> Any:
        """规范化单条校对问题字段。"""
        if not isinstance(item, dict):
            return item
        s = dict(item)
        valid_types = {"typo", "punctuation", "standardization"}
        if s.get("issue_type") not in valid_types:
            s["issue_type"] = "standardization"
        valid_severities = {"low", "medium", "high"}
        if s.get("severity") not in valid_severities:
            s["severity"] = "low"
        s.setdefault("evidence", "")
        s.setdefault("suggestion", "")
        s.setdefault("rationale", "")
        return s

    @classmethod
    def _canonicalize_proofread_payload(cls, data: Any) -> Any:
        """规范化 DocumentProofread payload。"""
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        issues = payload.get("issues")
        if isinstance(issues, list):
            payload["issues"] = [cls._canonicalize_proofread_issue(i) for i in issues]
        else:
            payload["issues"] = []
        return payload

    @staticmethod
    def _normalize_json_text(raw: str) -> str:
        """兼容不同模型端点可能返回的 Markdown 代码块包装。"""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text
