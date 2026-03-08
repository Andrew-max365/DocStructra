# tests/test_llm_timeout.py
# 超时配置、动态超时计算、重试逻辑与回退行为的测试
from __future__ import annotations

import warnings
from unittest.mock import MagicMock, call, patch

import openai
import pytest

from agent.llm_client import LLMCallError, LLMClient, compute_dynamic_timeout
import config


# ---------------------------------------------------------------------------
# 1. compute_dynamic_timeout — 动态超时计算
# ---------------------------------------------------------------------------

class TestComputeDynamicTimeout:
    def test_zero_paragraphs_returns_base_timeout(self):
        """0 段落时返回基础超时。"""
        result = compute_dynamic_timeout(0)
        assert result == config.LLM_TIMEOUT_S

    def test_increases_with_paragraph_count(self):
        """段落越多，超时应越大（或相等）。"""
        t1 = compute_dynamic_timeout(10)
        t2 = compute_dynamic_timeout(100)
        assert t2 >= t1

    def test_capped_at_max_timeout(self):
        """超过上限时应截断到 LLM_MAX_TIMEOUT_S。"""
        result = compute_dynamic_timeout(10_000)
        assert result == config.LLM_MAX_TIMEOUT_S

    def test_result_is_int(self):
        """返回值应为整数。"""
        result = compute_dynamic_timeout(50)
        assert isinstance(result, int)

    def test_formula_correctness(self):
        """公式验证：base + n*0.5，上限 max。"""
        base = config.LLM_TIMEOUT_S
        max_t = config.LLM_MAX_TIMEOUT_S
        assert compute_dynamic_timeout(0) == min(base, max_t)
        assert compute_dynamic_timeout(20) == min(base + 10, max_t)
        assert compute_dynamic_timeout(400) == min(base + 200, max_t)


# ---------------------------------------------------------------------------
# 2. _execute_chat_completion — 重试逻辑
# ---------------------------------------------------------------------------

def _make_client_with_mock_api() -> tuple[LLMClient, MagicMock]:
    """构造已跳过 API Key 检查的 LLMClient，并返回 mock client。"""
    with patch("agent.llm_client.LLM_API_KEY", "test-key"):
        client = LLMClient.__new__(LLMClient)
    mock_api = MagicMock()
    client.client = mock_api
    return client, mock_api


class TestExecuteChatCompletionRetry:
    def _make_messages(self):
        return [{"role": "user", "content": "test"}]

    def test_success_on_first_attempt(self):
        """首次调用成功时不应重试。"""
        llm, mock_api = _make_client_with_mock_api()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"result": "ok"}'
        mock_api.chat.completions.create.return_value = mock_resp

        result = llm._execute_chat_completion(self._make_messages(), timeout=30)

        assert result == '{"result": "ok"}'
        assert mock_api.chat.completions.create.call_count == 1

    def test_retries_on_timeout(self):
        """APITimeoutError 应触发重试，最终失败时抛出 LLMCallError。"""
        llm, mock_api = _make_client_with_mock_api()
        mock_api.chat.completions.create.side_effect = openai.APITimeoutError(
            request=MagicMock()
        )

        with patch("agent.llm_client.LLM_RETRY_ATTEMPTS", 3), \
             patch("agent.llm_client.LLM_RETRY_BACKOFF_S", 0), \
             patch("time.sleep"):
            with pytest.raises(LLMCallError) as exc_info:
                llm._execute_chat_completion(self._make_messages(), timeout=5)

        assert mock_api.chat.completions.create.call_count == 3
        assert exc_info.value.error_type in ("timeout", "read_timeout", "connect_timeout")

    def test_retries_on_connection_error(self):
        """APIConnectionError 应触发重试，最终失败时抛出 LLMCallError。"""
        llm, mock_api = _make_client_with_mock_api()
        mock_api.chat.completions.create.side_effect = openai.APIConnectionError(
            request=MagicMock()
        )

        with patch("agent.llm_client.LLM_RETRY_ATTEMPTS", 2), \
             patch("agent.llm_client.LLM_RETRY_BACKOFF_S", 0), \
             patch("time.sleep"):
            with pytest.raises(LLMCallError) as exc_info:
                llm._execute_chat_completion(self._make_messages(), timeout=5)

        assert mock_api.chat.completions.create.call_count == 2
        assert exc_info.value.error_type == "connect_error"

    def test_succeeds_on_second_attempt_after_timeout(self):
        """首次超时，第二次成功时应返回结果。"""
        llm, mock_api = _make_client_with_mock_api()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"ok": true}'
        mock_api.chat.completions.create.side_effect = [
            openai.APITimeoutError(request=MagicMock()),
            mock_resp,
        ]

        with patch("agent.llm_client.LLM_RETRY_ATTEMPTS", 3), \
             patch("agent.llm_client.LLM_RETRY_BACKOFF_S", 0), \
             patch("time.sleep"):
            result = llm._execute_chat_completion(self._make_messages(), timeout=30)

        assert result == '{"ok": true}'
        assert mock_api.chat.completions.create.call_count == 2

    def test_no_retry_on_auth_error(self):
        """AuthenticationError 不应重试，应立即抛出 LLMCallError(error_type='auth')。"""
        llm, mock_api = _make_client_with_mock_api()
        mock_api.chat.completions.create.side_effect = openai.AuthenticationError(
            message="Invalid key", response=MagicMock(), body={}
        )

        with patch("agent.llm_client.LLM_RETRY_ATTEMPTS", 3), \
             patch("time.sleep"):
            with pytest.raises(LLMCallError) as exc_info:
                llm._execute_chat_completion(self._make_messages(), timeout=30)

        # 只调用一次（无重试）
        assert mock_api.chat.completions.create.call_count == 1
        assert exc_info.value.error_type == "auth"

    def test_backoff_timing(self):
        """重试间应调用 time.sleep 并使用指数退避。"""
        llm, mock_api = _make_client_with_mock_api()
        mock_api.chat.completions.create.side_effect = openai.APITimeoutError(
            request=MagicMock()
        )

        with patch("agent.llm_client.LLM_RETRY_ATTEMPTS", 3), \
             patch("agent.llm_client.LLM_RETRY_BACKOFF_S", 1.0), \
             patch("time.sleep") as mock_sleep:
            with pytest.raises(LLMCallError):
                llm._execute_chat_completion(self._make_messages(), timeout=5)

        # 3 次尝试 → 2 次 sleep（attempt 1→2 和 2→3）
        assert mock_sleep.call_count == 2
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_args[0] == 1.0   # base * 2^0
        assert sleep_args[1] == 2.0   # base * 2^1

    def test_dynamic_timeout_passed_to_create(self):
        """_execute_chat_completion 传入 timeout 时应构建 openai.Timeout 并传给 create。"""
        llm, mock_api = _make_client_with_mock_api()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"x": 1}'
        mock_api.chat.completions.create.return_value = mock_resp

        llm._execute_chat_completion(self._make_messages(), timeout=45)

        _, call_kwargs = mock_api.chat.completions.create.call_args
        assert "timeout" in call_kwargs
        to = call_kwargs["timeout"]
        assert isinstance(to, openai.Timeout)
        assert to.read == 45


# ---------------------------------------------------------------------------
# 3. 回退行为（format_service._resolve_labels）
# ---------------------------------------------------------------------------

class TestFallbackBehavior:
    def _make_blocks(self, n=3):
        blocks = []
        for i in range(n):
            b = MagicMock()
            b.block_id = i
            b.paragraph_index = i
            b.text = f"段落{i}"
            blocks.append(b)
        return blocks

    def test_fallback_on_timeout_returns_rule_labels(self):
        """LLM 超时时应回退并返回规则标签，不抛出异常。"""
        from service.format_service import _resolve_labels

        blocks = self._make_blocks()
        mock_doc = MagicMock()

        timeout_err = LLMCallError("LLM 读取超时 (尝试 3/3): ...", error_type="read_timeout")

        with patch("service.format_service.rule_based_labels") as mock_rule, \
             patch("agent.mode_router.ModeRouter") as mock_router_cls:
            mock_rule.return_value = {0: "body", 1: "h1", 2: "body", "_source": "rule_based"}
            mock_router_cls.return_value.route.side_effect = timeout_err

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                labels = _resolve_labels(blocks, mock_doc, label_mode="hybrid")

        assert labels[0] == "body"
        assert labels[1] == "h1"
        # 应发出警告
        assert len(w) == 1
        assert "LLM labeling failed" in str(w[0].message)

    def test_fallback_warning_includes_mode_and_block_count(self):
        """回退警告应包含模式名和块数量，提高可诊断性。"""
        from service.format_service import _resolve_labels

        blocks = self._make_blocks(n=7)
        mock_doc = MagicMock()

        with patch("service.format_service.rule_based_labels") as mock_rule, \
             patch("agent.mode_router.ModeRouter") as mock_router_cls:
            mock_rule.return_value = {i: "body" for i in range(7)}
            mock_rule.return_value["_source"] = "rule_based"
            mock_router_cls.return_value.route.side_effect = LLMCallError(
                "timeout", error_type="timeout"
            )

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                _resolve_labels(blocks, mock_doc, label_mode="hybrid")

        warning_text = str(w[0].message)
        assert "hybrid" in warning_text
        assert "7" in warning_text

    def test_fallback_warning_dict_includes_error_type(self):
        """回退时 _warnings 条目应包含 error_type。"""
        from service.format_service import _resolve_labels

        blocks = self._make_blocks(n=2)
        mock_doc = MagicMock()

        with patch("service.format_service.rule_based_labels") as mock_rule, \
             patch("agent.mode_router.ModeRouter") as mock_router_cls:
            mock_rule.return_value = {0: "body", 1: "h1", "_source": "rule_based"}
            mock_router_cls.return_value.route.side_effect = LLMCallError(
                "connect error", error_type="connect_error"
            )

            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                labels = _resolve_labels(blocks, mock_doc, label_mode="hybrid")

        assert any("connect_error" in w for w in labels.get("_warnings", []))


