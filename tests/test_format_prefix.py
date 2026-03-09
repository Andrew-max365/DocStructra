"""
测试排版指令前缀检测逻辑（物理隔离方案）。

验证 _is_format_command 和 _extract_format_content 能稳定区分
排版指令与普通聊天输入。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

# 直接从模块导入（跳过 chainlit 运行时）
import importlib, types

# 由于 chainlit_app.py 顶层会 import chainlit，先用 stub 避免依赖
chainlit_stub = types.ModuleType("chainlit")
sys.modules.setdefault("chainlit", chainlit_stub)

# 动态加载两个纯函数
import ast, textwrap

with open(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "chainlit_app.py"),
    encoding="utf-8",
) as _fp:
    _SRC = _fp.read()

_GLOBS: dict = {}
for node in ast.parse(_SRC).body:
    if isinstance(node, ast.FunctionDef) and node.name in (
        "_is_format_command", "_extract_format_content"
    ):
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<ast>", "exec"), _GLOBS)

_is_format_command = _GLOBS["_is_format_command"]
_extract_format_content = _GLOBS["_extract_format_content"]


# ── _is_format_command ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "/f 把正文字号改成12",
    "/format 标题居中",
    "/f",
    "/format",
    "  /f   ",
    "  /format   ",
])
def test_is_format_command_positive(text):
    assert _is_format_command(text) is True


@pytest.mark.parametrize("text", [
    "你好",
    "谢谢",
    "帮我排版一下",          # 无前缀，即使含排版词也是普通聊天
    "format 标题居中",       # 没有斜杠
    "/ff 什么格式",          # 错误前缀
    "/formats 测试",         # 错误前缀
    "",
    "  ",
])
def test_is_format_command_negative(text):
    assert _is_format_command(text) is False


# ── _extract_format_content ─────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("/f 把正文字号改成12", "把正文字号改成12"),
    ("/format 标题居中", "标题居中"),
    ("  /f   正文加粗  ", "正文加粗"),
    ("  /format   行距1.5倍  ", "行距1.5倍"),
    ("/f", ""),      # 无内容
    ("/format", ""), # 无内容
])
def test_extract_format_content(text, expected):
    assert _extract_format_content(text) == expected
