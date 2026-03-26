"""
测试混合排版意图检测辅助函数：_has_hft_intent 和 _has_non_hft_format_intent。

验证这两个函数能够正确识别包含页眉/页脚/页码/目录关键词的文本，
以及包含一般格式排版关键词（颜色、字号、字体、行距等）的文本。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import ast
import re
import types
import pytest

# ── Stubs for heavy dependencies (openai, dotenv, etc.) ─────────────────────
# Stub agent package to avoid importing LLMClient which needs openai/dotenv
_agent_stub = types.ModuleType("agent")
_agent_stub.__path__ = []
sys.modules.setdefault("agent", _agent_stub)

# Extract _HEADER_FOOTER_TOC_KEYWORDS and _match_any from intent_classifier.py
# without triggering agent/__init__.py
_IC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "agent", "intent_classifier.py"
)
with open(_IC_PATH, encoding="utf-8") as _fp:
    _IC_SRC = _fp.read()

_IC_GLOBS: dict = {"re": re}
_IC_TREE = ast.parse(_IC_SRC)
_IC_TARGETS = {"_HEADER_FOOTER_TOC_KEYWORDS", "_match_any"}
for _node in _IC_TREE.body:
    if isinstance(_node, ast.Assign):
        for _t in _node.targets:
            if isinstance(_t, ast.Name) and _t.id in _IC_TARGETS:
                exec(
                    compile(ast.Module(body=[_node], type_ignores=[]), "<ast>", "exec"),
                    _IC_GLOBS,
                )
    elif isinstance(_node, ast.FunctionDef) and _node.name in _IC_TARGETS:
        exec(
            compile(ast.Module(body=[_node], type_ignores=[]), "<ast>", "exec"),
            _IC_GLOBS,
        )

# Inject a minimal agent.intent_classifier stub with the extracted symbols
_ic_stub = types.ModuleType("agent.intent_classifier")
_ic_stub._HEADER_FOOTER_TOC_KEYWORDS = _IC_GLOBS["_HEADER_FOOTER_TOC_KEYWORDS"]
_ic_stub._match_any = _IC_GLOBS["_match_any"]
sys.modules["agent.intent_classifier"] = _ic_stub

# ── Extract tested functions from chainlit_app.py via AST ───────────────────
# Stub chainlit to avoid runtime dependency
_cl_stub = types.ModuleType("chainlit")
sys.modules.setdefault("chainlit", _cl_stub)

_APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "ui", "chainlit_app.py"
)
with open(_APP_PATH, encoding="utf-8") as _fp:
    _APP_SRC = _fp.read()

_APP_GLOBS: dict = {"re": re}
_APP_TREE = ast.parse(_APP_SRC)
_APP_TARGETS = {"_NON_HFT_FORMAT_KEYWORDS", "_has_hft_intent", "_has_non_hft_format_intent"}

for _node in _APP_TREE.body:
    if isinstance(_node, ast.Assign):
        for _t in _node.targets:
            if isinstance(_t, ast.Name) and _t.id in _APP_TARGETS:
                exec(
                    compile(ast.Module(body=[_node], type_ignores=[]), "<ast>", "exec"),
                    _APP_GLOBS,
                )
    elif isinstance(_node, ast.FunctionDef) and _node.name in _APP_TARGETS:
        exec(
            compile(ast.Module(body=[_node], type_ignores=[]), "<ast>", "exec"),
            _APP_GLOBS,
        )

_has_hft_intent = _APP_GLOBS["_has_hft_intent"]
_has_non_hft_format_intent = _APP_GLOBS["_has_non_hft_format_intent"]


# ── _has_hft_intent ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "在页脚添加页码",
    "增加页眉",
    "设置页脚",
    "插入目录",
    "添加目录",
    "增加页码",
    "标题改为黑色并加上页码",       # 混合：同时含颜色和页码
    "请在页眉中写上'华中科技大学'",
])
def test_has_hft_intent_positive(text):
    assert _has_hft_intent(text) is True


@pytest.mark.parametrize("text", [
    "标题改为黑色",
    "正文字号改为12pt",
    "行距设为1.5倍",
    "加粗一级标题",
    "你好",
    "",
])
def test_has_hft_intent_negative(text):
    assert _has_hft_intent(text) is False


# ── _has_non_hft_format_intent ───────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "标题改为黑色",
    "正文字号改为12pt",
    "行距设为1.5倍",
    "加粗一级标题",
    "字体改为宋体",
    "标题颜色改为红色",
    "首行缩进两字符",
    "标题改为黑色并加上页码",       # 混合：同时含颜色和页码
])
def test_has_non_hft_format_intent_positive(text):
    assert _has_non_hft_format_intent(text) is True


@pytest.mark.parametrize("text", [
    "在页脚添加页码",
    "增加页眉",
    "插入目录",
    "你好",
    "",
])
def test_has_non_hft_format_intent_negative(text):
    assert _has_non_hft_format_intent(text) is False


# ── 组合场景：混合命令同时触发两者 ──────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "标题改为黑色并加上页码",
    "把正文字号改为12pt，同时在页脚添加页码",
    "行距1.5倍，并在页眉写上文章标题",
])
def test_mixed_command_triggers_both(text):
    assert _has_hft_intent(text) is True
    assert _has_non_hft_format_intent(text) is True

