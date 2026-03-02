# ui/chainlit_app.py
"""
Chainlit 前端入口 — MyAgent ReAct 文档格式化（增强版）。

新增功能：
  1. ReAct 模式：以 cl.Step 实时展示每轮 Thought / Action / Observation。
  2. LLM 校对建议自动应用：将 evidence→suggestion 文本替换直接写入输出文档。
  3. Diff 界面：将每条修改建议编号展示，用户可说 "不要修改#3" 拒绝特定修改。
  4. 自然语言解析：支持 "不要修改第3条和第5条"、"全部接受"、"全部拒绝" 等表达。

启动方式：
    chainlit run ui/chainlit_app.py
"""
from __future__ import annotations

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import json
import tempfile
from typing import List, Optional, Set

try:
    import chainlit as cl
except ImportError as e:
    raise ImportError("chainlit 未安装，请运行: pip install chainlit") from e

from config import LLM_MODE, REACT_MAX_ITERS
from service.format_service import format_docx_bytes
from ui.diff_utils import (
    build_diff_items,
    parse_rejected_numbers,
    apply_and_save_proofread,
    generate_structural_diff,
    DiffItem,
)

LABEL_MODES = ["rule", "llm", "hybrid", "react"]

# Session state keys
_KEY_LABEL_MODE = "label_mode"
_KEY_USE_REACT = "use_react"
_KEY_MAX_ITERS = "max_iters"
_KEY_STATE = "ui_state"        # "ready" | "awaiting_feedback"
_KEY_INPUT_BYTES = "input_bytes"
_KEY_OUTPUT_BYTES = "output_bytes"
_KEY_FILENAME = "filename"
_KEY_ISSUES = "pending_issues"
_KEY_DIFF_ITEMS = "diff_items"
_KEY_REPORT = "pending_report"


@cl.on_chat_start
async def on_chat_start():
    await cl.Message(
        content=(
            "👋 欢迎使用 **MyAgent 文档格式化智能体**！\n\n"
            "### 使用流程\n"
            "1. 先发送排版模式：`rule` / `llm` / `hybrid` / `react`\n"
            "2. 上传 `.docx` 文件，Agent 将自动排版并展示修改建议（Diff）\n"
            "3. 如不满意某条修改，直接回复 `不要修改#3`（可同时拒绝多条，如 `不要修改#2 #5`）\n"
            "4. 回复 `全部接受` 或 `确认` 应用所有建议，`全部拒绝` 保留原文\n\n"
            f"当前默认模式：**{LLM_MODE}**"
        )
    ).send()
    cl.user_session.set(_KEY_LABEL_MODE, LLM_MODE)
    cl.user_session.set(_KEY_USE_REACT, False)
    cl.user_session.set(_KEY_MAX_ITERS, REACT_MAX_ITERS)
    cl.user_session.set(_KEY_STATE, "ready")


@cl.on_message
async def on_message(message: cl.Message):
    state = cl.user_session.get(_KEY_STATE, "ready")

    if state == "awaiting_feedback":
        await _handle_feedback(message)
        return

    text = message.content.strip().lower()

    # ── Mode selection ──────────────────────────────────────────────────────
    if text in LABEL_MODES:
        if text == "react":
            cl.user_session.set(_KEY_LABEL_MODE, "rule")
            cl.user_session.set(_KEY_USE_REACT, True)
        else:
            cl.user_session.set(_KEY_LABEL_MODE, text)
            cl.user_session.set(_KEY_USE_REACT, False)
        await cl.Message(content=f"✅ 已切换到 **{text}** 模式，请上传 .docx 文件。").send()
        return

    # ── File upload ──────────────────────────────────────────────────────────
    docx_file = None
    for f in (message.elements or []):
        if hasattr(f, "name") and f.name.lower().endswith(".docx"):
            docx_file = f
            break

    if docx_file is None:
        await cl.Message(
            content="⚠️ 请上传 .docx 文件，或发送模式选择（rule / llm / hybrid / react）。"
        ).send()
        return

    label_mode = cl.user_session.get(_KEY_LABEL_MODE, LLM_MODE)
    use_react = cl.user_session.get(_KEY_USE_REACT, False)
    max_iters = cl.user_session.get(_KEY_MAX_ITERS, REACT_MAX_ITERS)

    with open(docx_file.path, "rb") as fp:
        input_bytes = fp.read()

    cl.user_session.set(_KEY_INPUT_BYTES, input_bytes)
    cl.user_session.set(_KEY_FILENAME, docx_file.name)

    await _process_file(input_bytes, docx_file.name, label_mode, use_react, max_iters)


# ── Core processing ────────────────────────────────────────────────────────

async def _process_file(
    input_bytes: bytes,
    filename: str,
    label_mode: str,
    use_react: bool,
    max_iters: int,
) -> None:
    """Run the formatting pipeline and display results."""

    mode_display = "react" if use_react else label_mode
    await cl.Message(content=f"⏳ 正在处理文档（模式: **{mode_display}**）…").send()

    try:
        if use_react:
            out_bytes, report = await _run_react_with_steps(
                input_bytes, filename, max_iters
            )
        else:
            out_bytes, report = format_docx_bytes(
                input_bytes,
                filename_hint=filename,
                label_mode=label_mode,
            )
    except Exception as e:
        await cl.Message(content=f"❌ 处理失败：{e}").send()
        return

    # ── Store formatted doc (structural changes only, no text replacements yet)
    cl.user_session.set(_KEY_OUTPUT_BYTES, out_bytes)
    cl.user_session.set(_KEY_REPORT, report)

    # ── Structural diff summary ──────────────────────────────────────────────
    struct_diff = generate_structural_diff(report)
    if struct_diff:
        await cl.Message(
            content=f"### 📐 格式化变更摘要\n\n{struct_diff}"
        ).send()

    # ── LLM proofread diff cards ─────────────────────────────────────────────
    raw_issues: list = report.get("llm_proofread", {}).get("issues", [])
    diff_items = build_diff_items(raw_issues)
    cl.user_session.set(_KEY_ISSUES, raw_issues)
    cl.user_session.set(_KEY_DIFF_ITEMS, diff_items)

    if diff_items:
        await _show_diff_cards(diff_items)
        cl.user_session.set(_KEY_STATE, "awaiting_feedback")
        await cl.Message(
            content=(
                "🤔 以上是 LLM 校对建议。请选择如何处理：\n\n"
                "- 回复 **`确认`** 或 **`全部接受`** → 应用所有建议\n"
                "- 回复 **`全部拒绝`** → 不应用任何建议，直接下载格式化结果\n"
                "- 回复 **`不要修改#3`**（可多个，如 `不要#2 #5`）→ 跳过指定建议，其余应用"
            )
        ).send()
    else:
        # No proofread issues – provide download immediately
        await _provide_download(out_bytes, report, filename, applied=0)


async def _run_react_with_steps(
    input_bytes: bytes,
    filename: str,
    max_iters: int,
) -> tuple:
    """Run the ReAct agent and display each iteration as cl.Steps."""
    tmp_in = tmp_out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(input_bytes)
            tmp_in = f.name
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            tmp_out = f.name

        from agent.graph.workflow import run_react_agent

        async with cl.Step(name="ReAct 初始化", type="tool") as step:
            step.input = f"文件: {filename}，最大迭代: {max_iters}"
            result_state = run_react_agent(
                tmp_in, tmp_out, label_mode="rule", max_iters=max_iters
            )
            step.output = f"共 {result_state.get('current_iter', 0)} 轮迭代完成"

        # Show each iteration
        thoughts = result_state.get("thoughts", [])
        observations = result_state.get("observations", [])
        import itertools
        _sentinel = object()
        for idx, (thought, obs) in enumerate(
            itertools.zip_longest(thoughts, observations, fillvalue=None), start=1
        ):
            if thought is None:
                thought = "(无 Thought 记录)"
            if obs is None:
                obs = {}
            async with cl.Step(name=f"迭代 {idx}", type="run") as step:
                step.input = f"**Thought**: {thought}"
                passed = obs.get("passed", False)
                errors = obs.get("errors", [])
                status = "✅ 通过" if passed else f"❌ 失败 ({len(errors)} 错误)"
                details = "\n".join(f"  - {e}" for e in errors) if errors else "  无错误"
                step.output = f"**Observation**: {status}\n{details}"

        with open(tmp_out, "rb") as f:
            out_bytes = f.read()
        report = result_state.get("report", {})
        return out_bytes, report

    except Exception as e:
        await cl.Message(
            content=f"⚠️ ReAct 模式失败，已回退到 rule 模式: {e}"
        ).send()
        out_bytes, report = format_docx_bytes(
            input_bytes, filename_hint=filename, label_mode="rule"
        )
        return out_bytes, report

    finally:
        for p in (tmp_in, tmp_out):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass


async def _show_diff_cards(diff_items: List[DiffItem]) -> None:
    """Display each diff item as a numbered card."""
    lines = [f"### 🔍 LLM 校对建议（共 {len(diff_items)} 条）\n"]
    for item in diff_items:
        lines.append(item.to_markdown())
        lines.append("")   # blank line between cards
    await cl.Message(content="\n".join(lines)).send()


# ── User feedback handling ─────────────────────────────────────────────────

async def _handle_feedback(message: cl.Message) -> None:
    """Handle user's accept/reject response for the pending diff items."""
    text = message.content.strip()
    diff_items: List[DiffItem] = cl.user_session.get(_KEY_DIFF_ITEMS, [])
    raw_issues: list = cl.user_session.get(_KEY_ISSUES, [])
    out_bytes: bytes = cl.user_session.get(_KEY_OUTPUT_BYTES, b"")
    report: dict = cl.user_session.get(_KEY_REPORT, {})
    filename: str = cl.user_session.get(_KEY_FILENAME, "output.docx")

    total = len(diff_items)
    rejected, intent = parse_rejected_numbers(text, total)

    # Reset state before doing work (avoid double-processing)
    cl.user_session.set(_KEY_STATE, "ready")

    if intent == "reject_all":
        await cl.Message(
            content="⏭️ 已跳过所有校对建议，正在生成格式化文档…"
        ).send()
        await _provide_download(out_bytes, report, filename, applied=0)
        return

    if rejected:
        kept = total - len(rejected)
        await cl.Message(
            content=f"⏳ 已拒绝 **#{', #'.join(str(n) for n in sorted(rejected))}**，"
            f"正在应用其余 **{kept}** 条建议…"
        ).send()
    else:
        await cl.Message(
            content=f"⏳ 正在应用全部 **{total}** 条校对建议…"
        ).send()

    try:
        final_bytes, applied = apply_and_save_proofread(
            out_bytes, raw_issues, excluded_numbers=rejected
        )
    except Exception as e:
        await cl.Message(
            content=f"⚠️ 应用校对建议时出错，将提供未修改的格式化文档: {e}"
        ).send()
        final_bytes, applied = out_bytes, 0

    await _provide_download(final_bytes, report, filename, applied=applied)


# ── Download helper ────────────────────────────────────────────────────────

async def _provide_download(
    out_bytes: bytes,
    report: dict,
    filename: str,
    *,
    applied: int,
) -> None:
    """Send download links for the output docx and report JSON."""
    meta = report.get("meta", {})
    para_before = meta.get("paragraphs_before", "?")
    para_after = meta.get("paragraphs_after", "?")

    summary_lines = [
        "✅ **处理完成！**",
        "",
        f"📊 段落数：{para_before} → {para_after}",
    ]
    if applied:
        summary_lines.append(f"✏️ 文本校对应用：{applied} 处")

    warnings_list = report.get("warnings", [])
    if warnings_list:
        summary_lines.append(f"⚠️ 警告：{len(warnings_list)} 条")

    await cl.Message(content="\n".join(summary_lines)).send()

    base_name = os.path.splitext(os.path.basename(filename))[0]
    output_el = cl.File(
        name=f"{base_name}_formatted.docx",
        content=out_bytes,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    report_el = cl.File(
        name=f"{base_name}_report.json",
        content=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
        mime="application/json",
    )
    await cl.Message(
        content="📥 下载产物：",
        elements=[output_el, report_el],
    ).send()
