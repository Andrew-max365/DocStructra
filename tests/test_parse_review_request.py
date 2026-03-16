"""
测试 parse_review_request 的核心路径（不依赖真实 LLM API）。

覆盖：
  1. 空文本 / 仅空格 → 早期返回（has_requirements=False）
  2. 无 API Key → 早期返回（has_requirements=False）
  3. fallback 路径：LLM 调用失败时回退到 parse_formatting_intent + _split_meta_fields
"""
import sys
import os
import types
import asyncio
import importlib.util

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Stub external dependencies unavailable in test environment ───────────────

def _stub_module(name: str, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return sys.modules[name]

_stub_module("dotenv", load_dotenv=lambda **kw: None)
_stub_module("requests")
_stub_module("duckduckgo_search", DDGS=object)

# Stub openai with the attributes used by llm_client and intent_parser
_openai_mod = _stub_module(
    "openai",
    AsyncOpenAI=object,
    OpenAI=object,
    Timeout=object,
    APITimeoutError=Exception,
    APIConnectionError=Exception,
    AuthenticationError=Exception,
)

# Stub config before loading agent package
_config_mod = _stub_module(
    "config",
    LLM_API_KEY="",
    LLM_BASE_URL="https://api.openai.com/v1",
    LLM_MODEL="gpt-4o",
    LLM_MODE="hybrid",
    REACT_MAX_ITERS=5,
)

# Stub heavy agent sub-modules that have further dependencies we don't need
_stub_module("agent.llm_client", LLMClient=object, LLMCallError=Exception)
_stub_module("agent.doc_analyzer", DocAnalyzer=object)
_stub_module("agent.mode_router", ModeRouter=object)

# Load intent_parser directly by file path to bypass agent/__init__.py
_ip_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent", "intent_parser.py")
_ip_spec = importlib.util.spec_from_file_location("agent.intent_parser", _ip_path)
_ip_mod = importlib.util.module_from_spec(_ip_spec)
sys.modules["agent.intent_parser"] = _ip_mod
_ip_spec.loader.exec_module(_ip_mod)

parse_review_request = _ip_mod.parse_review_request


# ── 1. 空文本早期返回 ────────────────────────────────────────────────────────

def test_parse_review_request_empty_text():
    """空字符串直接返回 has_requirements=False，不调用 LLM。"""
    result = asyncio.run(parse_review_request(""))
    assert result["has_requirements"] is False
    assert result["overrides"] == {}
    assert result["hft_actions"] == {}
    assert result["description"] == ""


def test_parse_review_request_whitespace_only():
    """纯空白文本等同于空文本，直接返回 has_requirements=False。"""
    result = asyncio.run(parse_review_request("   \t\n  "))
    assert result["has_requirements"] is False


# ── 2. 无 API Key 早期返回 ────────────────────────────────────────────────────

def test_parse_review_request_no_api_key(monkeypatch):
    """LLM_API_KEY 为空时，直接返回 has_requirements=False，不发起网络请求。"""
    monkeypatch.setattr(_ip_mod, "LLM_API_KEY", "")
    result = asyncio.run(parse_review_request("把行距改为1.5倍"))
    assert result["has_requirements"] is False
    assert result["overrides"] == {}
    assert result["hft_actions"] == {}


# ── 3. fallback 路径 ────────────────────────────────────────────────────────

def test_parse_review_request_fallback_with_overrides(monkeypatch):
    """LLM 解析失败时 fallback 到 parse_formatting_intent + _split_meta_fields，
    若存在有效 overrides，返回 has_requirements=True。"""
    monkeypatch.setattr(_ip_mod, "LLM_API_KEY", "test-key")

    # Make openai.AsyncOpenAI raise on attribute access (simulates LLM error)
    class _FailingClient:
        @property
        def chat(self):
            raise RuntimeError("simulated LLM failure")

    monkeypatch.setattr(_openai_mod, "AsyncOpenAI", lambda **kw: _FailingClient())

    # Stub parse_formatting_intent to return a simple body override
    async def _fake_parse_intent(text):
        return {"body": {"line_spacing": 1.5}}

    monkeypatch.setattr(_ip_mod, "parse_formatting_intent", _fake_parse_intent)

    result = asyncio.run(parse_review_request("把正文行距改为1.5倍"))
    assert result["has_requirements"] is True
    assert result["overrides"].get("body", {}).get("line_spacing") == 1.5


def test_parse_review_request_fallback_no_overrides(monkeypatch):
    """fallback 路径中 parse_formatting_intent 返回空时，has_requirements=False。"""
    monkeypatch.setattr(_ip_mod, "LLM_API_KEY", "test-key")

    class _FailingClient:
        @property
        def chat(self):
            raise RuntimeError("fail")

    monkeypatch.setattr(_openai_mod, "AsyncOpenAI", lambda **kw: _FailingClient())

    async def _fake_parse_intent(text):
        return {}

    monkeypatch.setattr(_ip_mod, "parse_formatting_intent", _fake_parse_intent)

    result = asyncio.run(parse_review_request("帮我审阅文档"))
    assert result["has_requirements"] is False
