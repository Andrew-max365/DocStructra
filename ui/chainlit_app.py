# ui/chainlit_app.py
"""
Chainlit 前端入口 — MyAgent ReAct 文档格式化。

启动方式：
    chainlit run ui/chainlit_app.py
"""
from __future__ import annotations

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import json
import tempfile

try:
    import chainlit as cl
except ImportError as e:
    raise ImportError("chainlit 未安装，请运行: pip install chainlit") from e

from config import LLM_MODE, REACT_MAX_ITERS
from service.format_service import format_docx_bytes

LABEL_MODES = ["rule", "llm", "hybrid", "react"]


@cl.on_chat_start
async def on_chat_start():
    await cl.Message(
        content=(
            "👋 欢迎使用 **MyAgent 文档格式化智能体**！\n\n"
            "请上传一个 `.docx` 文件，我将自动分析并排版。\n\n"
            "**支持模式**：`rule` / `llm` / `hybrid` / `react`（ReAct 迭代闭环）\n\n"
            "请先发送模式选择（如 `hybrid`），然后上传文件。"
        )
    ).send()
    cl.user_session.set("label_mode", LLM_MODE)
    cl.user_session.set("max_iters", REACT_MAX_ITERS)


@cl.on_message
async def on_message(message: cl.Message):
    # Check if user selected a mode
    text = message.content.strip().lower()
    if text in LABEL_MODES:
        # 'react' is a meta-mode: it runs the ReAct loop with 'rule' as the base label_mode
        cl.user_session.set("label_mode", text if text != "react" else "rule")
        cl.user_session.set("use_react", text == "react")
        await cl.Message(content=f"✅ 已切换到 **{text}** 模式，请上传 .docx 文件。").send()
        return

    # Process file upload
    files = message.elements
    docx_file = None
    for f in files:
        if hasattr(f, "name") and f.name.endswith(".docx"):
            docx_file = f
            break

    if docx_file is None:
        await cl.Message(
            content="⚠️ 请上传 .docx 文件，或发送模式选择（rule/llm/hybrid/react）。"
        ).send()
        return

    label_mode = cl.user_session.get("label_mode", LLM_MODE)
    use_react = cl.user_session.get("use_react", False)
    max_iters = cl.user_session.get("max_iters", REACT_MAX_ITERS)

    msg = cl.Message(content=f"⏳ 正在处理文档（模式: **{label_mode}**）...")
    await msg.send()

    try:
        with open(docx_file.path, "rb") as fp:
            input_bytes = fp.read()

        steps: list = []

        if use_react:
            await cl.Message(content="🔄 启动 ReAct 迭代闭环...").send()

            tmp_in_path: Optional[str] = None
            tmp_out_path: Optional[str] = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_in:
                    tmp_in.write(input_bytes)
                    tmp_in_path = tmp_in.name

                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_out:
                    tmp_out_path = tmp_out.name

                from agent.graph.workflow import run_react_agent
                result_state = run_react_agent(
                    tmp_in_path,
                    tmp_out_path,
                    label_mode="rule",
                    max_iters=max_iters,
                )

                for i, thought in enumerate(result_state.get("thoughts", [])):
                    steps.append(f"**迭代 {i+1} - Thought**: {thought}")

                for obs in result_state.get("observations", []):
                    steps.append(
                        f"**Observation {obs.get('iteration', '?')}**: "
                        f"passed={obs.get('passed')}, errors={obs.get('errors', [])}"
                    )

                with open(tmp_out_path, "rb") as f:
                    out_bytes = f.read()
                report = result_state.get("report", {})

            except Exception as e:
                await cl.Message(content=f"⚠️ ReAct 模式失败，回退 rule 模式: {e}").send()
                out_bytes, report = format_docx_bytes(
                    input_bytes, filename_hint=docx_file.name, label_mode="rule"
                )
            finally:
                for _tmp in (tmp_in_path, tmp_out_path):
                    if _tmp is not None:
                        try:
                            os.remove(_tmp)
                        except OSError:
                            pass
        else:
            out_bytes, report = format_docx_bytes(
                input_bytes,
                filename_hint=docx_file.name,
                label_mode=label_mode,
            )

        summary_lines = ["✅ **处理完成！**", ""]
        if steps:
            summary_lines.append("### 🔄 ReAct 迭代摘要")
            summary_lines.extend(steps)
            summary_lines.append("")

        meta = report.get("meta", {})
        summary_lines.append(
            f"📊 段落处理：{meta.get('paragraphs_before', '?')} → {meta.get('paragraphs_after', '?')}"
        )

        warnings_list = report.get("warnings", [])
        if warnings_list:
            summary_lines.append(f"⚠️ 警告：{len(warnings_list)} 条")

        proofread = report.get("llm_proofread", {})
        issues = proofread.get("issues", [])
        if issues:
            summary_lines.append(f"🔍 校对问题：{len(issues)} 条")

        await cl.Message(content="\n".join(summary_lines)).send()

        output_el = cl.File(
            name="output.docx",
            content=out_bytes,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        report_el = cl.File(
            name="report.json",
            content=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
            mime="application/json",
        )
        await cl.Message(
            content="📥 下载产物：",
            elements=[output_el, report_el],
        ).send()

    except Exception as e:
        await cl.Message(content=f"❌ 处理失败：{e}").send()
