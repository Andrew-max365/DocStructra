# ui/chainlit_app.py
"""
Chainlit 前端入口 — MyAgent ReAct 文档格式化（增强版）。

功能：
  1. 点击按钮选择排版模式（无需手动输入命令）。
  2. 直接上传 .docx 文件即可处理，无需同时输入文字。
  3. Diff 视图：直接在页面中渲染修改前后对比（GFM ~~删除线~~ → 建议，含段落上下文）。
  4. 通用聊天：不上传文件时，支持直接与 LLM 对话（流式输出）。

启动方式：
    chainlit run ui/chainlit_app.py
"""
from __future__ import annotations

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import asyncio
import copy
import json
import tempfile
from typing import Any, Dict, List, Set

try:
    import chainlit as cl
except ImportError as e:
    raise ImportError("chainlit 未安装，请运行: pip install chainlit") from e

from config import LLM_MODE, REACT_MAX_ITERS, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from service.format_service import format_docx_bytes
from ui.diff_utils import (
    build_diff_items,
    parse_rejected_numbers,
    apply_and_save_proofread,
    generate_structural_diff,
    _ACCEPT_ALL_PATTERNS,
    DiffItem,
)

# Session state keys
_KEY_MAX_ITERS = "max_iters"
_KEY_STATE = "ui_state"        # "ready" | "awaiting_feedback"
_KEY_INPUT_BYTES = "input_bytes"
_KEY_OUTPUT_BYTES = "output_bytes"
_KEY_FILENAME = "filename"
_KEY_ISSUES = "pending_issues"
_KEY_DIFF_ITEMS = "diff_items"
_KEY_REPORT = "pending_report"
_KEY_CHAT_HISTORY = "chat_history"
_KEY_SPEC_OVERRIDES = "spec_overrides"
_KEY_SPEC_PATH = "spec_path"

# Maximum number of chat messages (user+assistant turns) to keep in session context.
_MAX_CHAT_HISTORY = 20


def _deep_merge_dicts(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并两个配置字典，update 中的叶子值覆盖 base，不整层替换。委托给 core.spec._deep_merge。"""
    from core.spec import _deep_merge
    return _deep_merge(base, update)


@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set(_KEY_MAX_ITERS, REACT_MAX_ITERS)
    cl.user_session.set(_KEY_STATE, "ready")
    cl.user_session.set(_KEY_CHAT_HISTORY, [])
    cl.user_session.set(_KEY_SPEC_OVERRIDES, {})
    cl.user_session.set(_KEY_SPEC_PATH, "specs/default.yaml")

    await cl.Message(
        content=(
            "👋 欢迎使用 **Structura 智能文档排版助手**！\n\n"
            "直接上传 `.docx` 文件即可开始全自动排版（极速规则 + 大模型智能纠错）。\n\n"
            "📝 **排版指令**：使用 `/f` 或 `/format` 开头，对上次已排版的文档做增量修改，例如：\n"
            "> `/f 把大标题改成红色，正文字号改成 14`\n\n"
            "💬 **自由聊天**：直接发送消息（不加前缀）与我对话。"
        )
    ).send()

"""   #引入 Slash 命令（/f 或 /format） 来实现物理隔离
@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set(_KEY_LABEL_MODE, LLM_MODE)
    cl.user_session.set(_KEY_USE_REACT, False)
    cl.user_session.set(_KEY_MAX_ITERS, REACT_MAX_ITERS)
    cl.user_session.set(_KEY_STATE, "ready")
    cl.user_session.set(_KEY_CHAT_HISTORY, [])
    cl.user_session.set(_KEY_SPEC_OVERRIDES, {})
    cl.user_session.set(_KEY_SPEC_PATH, "specs/default.yaml")

    await cl.Message(
        content=(
            "👋 欢迎使用 **Sturctra 文档排版智能体**！\n\n"
            f"当前模式：**{LLM_MODE}**。点击下方按钮切换模式，直接上传 `.docx` 文件即可开始排版。\n\n"
            "💬 **自由交谈**：直接发送消息与我对话。\n"
            "🎨 **修改排版**：请使用 `/f` 或 `/format` 开头。例如：\n"
            "> `/f 把大标题改成红色，正文字号改成 14`"
        ),
        actions=_make_mode_actions(),
    ).send()
"""



@cl.action_callback("accept_all_action")
async def on_accept_all(action: cl.Action):
    await _execute_feedback("accept_all", [])

@cl.action_callback("reject_all_action")
async def on_reject_all(action: cl.Action):
    await _execute_feedback("reject_all", [])


@cl.on_message
async def on_message(message: cl.Message):
    state = cl.user_session.get(_KEY_STATE, "ready")

    # Allow uploading a new file even while awaiting feedback (starts fresh)
    docx_file = None
    for f in (message.elements or []):
        if hasattr(f, "name") and f.name.lower().endswith(".docx"):
            docx_file = f
            break

    if state == "awaiting_feedback" and docx_file is None:
        await _handle_feedback(message)
        return

    text = message.content.strip()

    # ── File upload (text is optional) ──────────────────────────────────────
    if docx_file is not None:
        max_iters = cl.user_session.get(_KEY_MAX_ITERS, REACT_MAX_ITERS)
        overrides = cl.user_session.get(_KEY_SPEC_OVERRIDES, {})
        spec_path = cl.user_session.get(_KEY_SPEC_PATH, "specs/default.yaml")

        with open(docx_file.path, "rb") as fp:
            input_bytes = fp.read()

        cl.user_session.set(_KEY_INPUT_BYTES, input_bytes)
        cl.user_session.set(_KEY_FILENAME, docx_file.name)

        # ── 新增：若用户在发送文件时附带了文字要求，先解析为 overrides ──
        if text:
            thinking_msg = cl.Message(content="⏳ 正在解析您的排版要求...")
            await thinking_msg.send()
            actual_cmd = _extract_format_content(text) if _is_format_command(text) else text

            try:
                from agent.intent_parser import parse_formatting_request
                parsed = await parse_formatting_request(actual_cmd, current_spec_path=spec_path)
                formatting_intent = parsed.get("overrides", {})
                routed_spec = parsed.get("spec_path", spec_path)
            except Exception as e:
                formatting_intent = None
                print(f"解析排版意图异常: {e}")

            if formatting_intent:
                new_overrides = _deep_merge_dicts(copy.deepcopy(overrides), formatting_intent)
                cl.user_session.set(_KEY_SPEC_OVERRIDES, new_overrides)
                cl.user_session.set(_KEY_SPEC_PATH, routed_spec)
                overrides = new_overrides
                spec_path = routed_spec

                pretty = json.dumps(formatting_intent, ensure_ascii=False, indent=2)
                thinking_msg.content = (
                    f"✅ **排版指令已确认！**\n\n"
                    f"```json\n{pretty}\n```\n\n"
                    f"📚 模板：`{routed_spec}`\n"
                    f"⏳ 正在处理文档..."
                )
                await thinking_msg.update()
            else:
                # 没有解析出排版意图 → 可能是普通备注，忽略即可
                await thinking_msg.remove()

        await _process_file(input_bytes, docx_file.name, max_iters, overrides=overrides if overrides else None, spec_path=spec_path)
        return

    # ── General chat fallback ────────────────────────────────────────────────
    if text:
        await _handle_chat(text)
    else:
        await cl.Message(
            content="💡 请上传 `.docx` 文件开始排版，或直接发送消息与我对话。",
        ).send()


# ── Core processing ────────────────────────────────────────────────────────

async def _process_file(
        input_bytes: bytes,
        filename: str,
        max_iters: int,
        overrides: dict = None,
        spec_path: str = "specs/default.yaml",
) -> None:
    """Run the formatting pipeline and display results."""

    processing_msg = cl.Message(content=f"🚀 任务已启动：正在全自动处理文档...")
    await processing_msg.send()

    try:
        # 🌟 调用流式流程
        out_bytes, report = await _run_react_with_steps(
            input_bytes, filename, max_iters, overrides=overrides, spec_path=spec_path
        )
    except Exception as e:
        processing_msg.content = f"❌ 处理失败：{e}"
        await processing_msg.update()
        return

    # 隐藏刚才的过渡消息
    await processing_msg.remove()

    # 1. 保存当前状态，防止用户点按钮时找不到数据
    cl.user_session.set(_KEY_OUTPUT_BYTES, out_bytes)
    cl.user_session.set(_KEY_REPORT, report)
    cl.user_session.set(_KEY_FILENAME, filename)

    try:
        # 2. 生成排版结构变更摘要
        from ui.diff_utils import generate_structural_diff, build_diff_items
        struct_diff = generate_structural_diff(report)
        if struct_diff:
            await cl.Message(
                content=f"### 📐 排版格式化变更摘要\n\n{struct_diff}"
            ).send()

        # 3. 提取错别字校对建议
        # ⚠️ 注意：这里必须用 get 的链式调用，防止大模型没返回 proofread 导致报错
        raw_issues = report.get("llm_proofread", {}).get("issues", [])
        diff_items = build_diff_items(raw_issues)

        cl.user_session.set(_KEY_ISSUES, raw_issues)
        cl.user_session.set(_KEY_DIFF_ITEMS, diff_items)

        # 4. 如果有错别字建议，展示卡片让用户选；如果没有，直接给下载链接！
        if diff_items:
            await _show_diff_cards(diff_items)
            cl.user_session.set(_KEY_STATE, "awaiting_feedback")
        else:
            # 没有任何错别字，直接出锅！
            await _provide_download(out_bytes, report, filename, applied=0)
    except Exception as e:
        # 后处理出错时，仍然尝试提供下载，不让用户白等
        await cl.Message(content=f"⚠️ 后处理阶段出现异常：{e}，但文档已成功排版。").send()
        await _provide_download(out_bytes, report, filename, applied=0)


async def _run_react_with_steps(
        input_bytes: bytes,
        filename: str,
        max_iters: int,
        overrides: dict = None,
        spec_path: str = "specs/default.yaml",
) -> tuple:
    """Run the ReAct agent and display progress dynamically via streaming."""
    tmp_in = tmp_out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(input_bytes)
            tmp_in = f.name
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            tmp_out = f.name

        # 🚀 引入我们刚刚写好的流式生成函数
        from agent.graph.workflow import run_react_agent_stream

        result_state = {}

        # 替代原本阻塞式的 asyncio.to_thread，使用 async for 优雅地“倾听”Agent的进展
        async for event in run_react_agent_stream(
                tmp_in, tmp_out, spec_path=spec_path, max_iters=max_iters, overrides=overrides
        ):
            for node_name, state_update in event.items():
                # 记录最终状态
                result_state.update(state_update)

                # 🚀 核心：根据不同节点的完成状态，向前端“直播”
                if node_name == "ingest":
                    await cl.Message(content="✅ **阶段 1/4**：读取成功，已完成基础规则格式解析。").send()
                elif node_name == "trigger":
                    if state_update.get("needs_llm"):
                        await cl.Message(
                            content="🔍 **阶段 2/4**：雷达扫描到异常段落！\n"
                                    "> 正在唤醒大模型进行深度结构分析与错别字校对...\n"
                                    "*(此过程大约需要 30~60 秒，请您先喝口水 ☕)* ⏳"
                        ).send()
                    else:
                        await cl.Message(
                            content="⚡ **阶段 2/4**：文档结构极其清晰！\n"
                                    "> 无需大模型介入，已为您切换至极速排版模式。"
                        ).send()
                elif node_name == "reason":
                    await cl.Message(content="🧠 **阶段 3/4**：大模型结构分析完毕，正在为您应用智能排版策略...").send()
                elif node_name == "validate":
                    passed = state_update.get("passed", False)
                    if passed:
                        await cl.Message(content="✨ **阶段 4/4**：所有排版格式应用成功！").send()
                    else:
                        errors = state_update.get("errors", [])
                        await cl.Message(content=f"⚠️ **排版异常警告**：\n{errors}").send()
                elif node_name == "reflect":
                    await cl.Message(content="👀 **附加阶段**：视觉多模态审查完毕。").send()

        # 读取最终生成的二进制文档
        with open(tmp_out, "rb") as f:
            out_bytes = f.read()

        return out_bytes, result_state.get("report", {})

    except Exception as e:
        import traceback
        await cl.Message(
            content=f"⚠️ 排版流水线发生崩溃: {e}\n```\n{traceback.format_exc()}\n```"
        ).send()

        # 原有的安全回退机制
        from service.format_service import format_docx_bytes
        out_bytes, report = await asyncio.to_thread(
            format_docx_bytes,
            input_bytes, filename_hint=filename, label_mode="hybrid",
            overrides=overrides,
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
    """Display diff items as plain markdown with action buttons at the bottom."""
    lines = [f"### 🔍 LLM 校对建议（共 {len(diff_items)} 条）\n"]
    for item in diff_items:
        lines.append(item.to_markdown())
        lines.append("")  # blank line between items

    lines.append("---\n🤔 **请点击下方按钮快捷操作，或者直接打字告诉我您的决定：**")

    # 核心修改：将按钮直接挂载在输出建议的这条消息上！
    msg = cl.Message(
        content="\n".join(lines),
        actions=[
            cl.Action(name="accept_all_action", payload={"action": "accept"}, label="✅ 全部接受"),
            cl.Action(name="reject_all_action", payload={"action": "reject"}, label="❌ 全部拒绝"),
        ]
    )
    await msg.send()
    cl.user_session.set("diff_msg", msg)


async def _execute_feedback(intent: str, rejected: List[int]) -> None:
    """执行校对反馈操作并输出文档"""
    cl.user_session.set(_KEY_STATE, "ready")  # 恢复状态

    # 移除界面上的按钮（无论是通过点击还是聊天触发该流程，都应令历史按钮消失）
    msg = cl.user_session.get("diff_msg")
    if msg:
        msg.actions = []
        await msg.update()
        cl.user_session.set("diff_msg", None)

    diff_items = cl.user_session.get(_KEY_DIFF_ITEMS, [])
    raw_issues = cl.user_session.get(_KEY_ISSUES, [])
    out_bytes = cl.user_session.get(_KEY_OUTPUT_BYTES, b"")
    report = cl.user_session.get(_KEY_REPORT, {})
    filename = cl.user_session.get(_KEY_FILENAME, "output.docx")
    total = len(diff_items)

    if intent == "reject_all":
        await cl.Message(content="⏭️ 已跳过所有校对建议，正在生成最终文档…").send()
        await _provide_download(out_bytes, report, filename, applied=0)
        return

    if rejected:
        kept = total - len(rejected)
        await cl.Message(
            content=f"⏳ 已拒绝 **#{', #'.join(str(n) for n in sorted(rejected))}**，"
                    f"正在应用其余 **{kept}** 条建议…"
        ).send()
    else:
        await cl.Message(content=f"⏳ 正在应用全部 **{total}** 条校对建议…").send()

    try:
        from ui.diff_utils import apply_and_save_proofread
        final_bytes, applied = apply_and_save_proofread(
            out_bytes, raw_issues, excluded_numbers=rejected
        )
    except Exception as e:
        await cl.Message(content=f"⚠️ 应用校对建议出错: {e}").send()
        final_bytes, applied = out_bytes, 0

    await _provide_download(final_bytes, report, filename, applied=applied)


# ── General chat ────────────────────────────────────────────────────────────
'''  # 物理隔离版本的配套代码
async def _handle_chat(text: str) -> None:
    """处理用户输入：隔离 Slash 命令与普通聊天"""
    if not LLM_API_KEY:
        await cl.Message(
            content="💬 未配置 LLM API Key，暂无法进行对话或解析指令。",
            actions=_make_mode_actions(),
        ).send()
        return

    text_strip = text.strip()
    is_format_cmd = text_strip.startswith("/f ") or text_strip.startswith("/format ") or text_strip == "/f" or text_strip == "/format"

    # ════════════════════════════════════════════════════════════════════════
    # 分支 A：用户明确下达排版指令 (Slash Command)
    # ════════════════════════════════════════════════════════════════════════
    if is_format_cmd:
        # 提取真实的指令内容
        cmd_content = text_strip.replace("/format", "").replace("/f", "").strip()
        
        if not cmd_content:
            await cl.Message(content="⚠️ 请在命令后输入具体要求，例如：`/f 所有标题居中`").send()
            return
            
        thinking_msg = cl.Message(content="⏳ 正在将您的指令翻译为排版配置...")
        await thinking_msg.send()

        try:
            from agent.intent_parser import parse_formatting_request
            current_spec_path = cl.user_session.get(_KEY_SPEC_PATH, "specs/default.yaml")
            parsed = await parse_formatting_request(cmd_content, current_spec_path=current_spec_path)
            formatting_intent = parsed.get("overrides", {})
            routed_spec = parsed.get("spec_path", current_spec_path)
        except Exception as e:
            formatting_intent = None
            print(f"解析报错: {e}")

        if formatting_intent:
            current_overrides: Dict[str, Any] = cl.user_session.get(_KEY_SPEC_OVERRIDES, {})
            new_overrides = _deep_merge_dicts(copy.deepcopy(current_overrides), formatting_intent)
            cl.user_session.set(_KEY_SPEC_OVERRIDES, new_overrides)
            cl.user_session.set(_KEY_SPEC_PATH, routed_spec)

            pretty_intent = json.dumps(formatting_intent, ensure_ascii=False, indent=2)
            pretty_overrides = json.dumps(new_overrides, ensure_ascii=False, indent=2)

            input_bytes: bytes = cl.user_session.get(_KEY_INPUT_BYTES)
            if input_bytes:
                thinking_msg.content = (
                    f"✅ **指令已确认！**\n\n"
                    f"**增量修改：**\n```json\n{pretty_intent}\n```\n"
                    f"🚀 正在为您**重新生成文档**..."
                )
                await thinking_msg.update()

                filename: str = cl.user_session.get(_KEY_FILENAME, "document.docx")
                label_mode = cl.user_session.get(_KEY_LABEL_MODE, LLM_MODE)
                use_react = cl.user_session.get(_KEY_USE_REACT, False)
                max_iters = cl.user_session.get(_KEY_MAX_ITERS, REACT_MAX_ITERS)
                
                await _process_file(
                    input_bytes, filename, label_mode, use_react, max_iters,
                    overrides=new_overrides,
                    spec_path=routed_spec,
                )
            else:
                thinking_msg.content = (
                    f"✅ **已记录您的排版偏好！** 下次上传文档时将自动应用。\n\n"
                    f"**当前完整配置：**\n```json\n{pretty_overrides}\n```\n"
                    f"💡 请直接上传 `.docx` 文件。"
                )
                await thinking_msg.update()
        else:
            thinking_msg.content = "❌ 抱歉，未能从您的指令中提取出有效的排版属性，请换种说法重试。"
            await thinking_msg.update()
            
        return

    # ════════════════════════════════════════════════════════════════════════
    # 分支 B：普通自由交谈 (无需解析意图，速度极快)
    # ════════════════════════════════════════════════════════════════════════
    import openai as _openai
    history: List[dict] = cl.user_session.get(_KEY_CHAT_HISTORY, [])
    history.append({"role": "user", "content": text})

    try:
        client = _openai.AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        msg = cl.Message(content="")
        await msg.send()
        reply_parts: List[str] = []
        
        async with await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 MyAgent 文档格式化助手。"
                        "你可以帮助用户了解文档格式化知识、解答关于本工具的使用问题，也可以进行一般性的中文对话。"
                        "如果用户在聊天中提出了排版要求，请提醒他使用 '/f + 需求' 的命令格式。" # 顺便让大模型也知道这个规则
                    ),
                },
                *history[-_MAX_CHAT_HISTORY:],
            ],
            stream=True,
        ) as stream:
            async for chunk in stream:
                token = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if token:
                    reply_parts.append(token)
                    await msg.stream_token(token)
        await msg.update()
        
        reply = "".join(reply_parts)
        history.append({"role": "assistant", "content": reply})
        cl.user_session.set(_KEY_CHAT_HISTORY, history)
    except Exception as e:
        await cl.Message(content=f"💬 对话失败：{e}").send()
'''


def _is_format_command(text: str) -> bool:
    """判断输入是否为排版指令（以 /f 或 /format 开头）。"""
    t = text.strip()
    return t == "/f" or t == "/format" or t.startswith("/f ") or t.startswith("/format ")


def _extract_format_content(text: str) -> str:
    """从排版指令中提取实际内容（去掉前缀 /f 或 /format）。"""
    t = text.strip()
    if t.startswith("/format "):
        return t[len("/format "):].strip()
    if t.startswith("/f "):
        return t[len("/f "):].strip()
    return ""


async def _handle_chat(text: str) -> None:
    """处理用户输入：通过指令前缀物理隔离排版需求与普通聊天。

    - 以 /f 或 /format 开头 → 排版指令，对上次已排版文档做增量修改。
    - 其他输入 → 普通聊天，以 Structura 角色直接回复。
    """
    if not LLM_API_KEY:
        await cl.Message(
            content="💬 未配置 LLM API Key，暂无法进行对话或解析指令。"
        ).send()
        return

    # ════════════════════════════════════════════════════════════════════════
    # 分支 A：排版指令（以 /f 或 /format 开头）
    # ════════════════════════════════════════════════════════════════════════
    if _is_format_command(text):
        cmd_content = _extract_format_content(text)

        if not cmd_content:
            await cl.Message(
                content="⚠️ 请在命令后输入具体要求，例如：`/f 所有标题居中`"
            ).send()
            return

        thinking_msg = cl.Message(content="⏳ 正在将您的指令翻译为排版配置...")
        await thinking_msg.send()

        try:
            from agent.intent_parser import parse_formatting_request
            current_spec_path = cl.user_session.get(_KEY_SPEC_PATH, "specs/default.yaml")
            parsed = await parse_formatting_request(cmd_content, current_spec_path=current_spec_path)
            formatting_intent = parsed.get("overrides", {})
            routed_spec = parsed.get("spec_path", current_spec_path)
        except Exception as e:
            formatting_intent = None
            print(f"解析排版意图异常: {e}")

        if formatting_intent:
            current_overrides: Dict[str, Any] = cl.user_session.get(_KEY_SPEC_OVERRIDES, {})
            new_overrides = _deep_merge_dicts(copy.deepcopy(current_overrides), formatting_intent)
            cl.user_session.set(_KEY_SPEC_OVERRIDES, new_overrides)
            cl.user_session.set(_KEY_SPEC_PATH, routed_spec)

            pretty_intent = json.dumps(formatting_intent, ensure_ascii=False, indent=2)

            # 优先使用上一次已排版完成的文档做增量修改，保证不重新处理原始全文
            base_bytes: bytes = (
                cl.user_session.get(_KEY_OUTPUT_BYTES)
                or cl.user_session.get(_KEY_INPUT_BYTES)
            )
            if base_bytes:
                thinking_msg.content = (
                    f"✅ **指令已确认！**\n\n"
                    f"**增量修改：**\n```json\n{pretty_intent}\n```\n"
                    f"📚 当前模板：`{routed_spec}`\n"
                    f"🚀 正在对上次已排版文档进行增量修改..."
                )
                await thinking_msg.update()

                filename: str = cl.user_session.get(_KEY_FILENAME, "document.docx")
                max_iters = cl.user_session.get(_KEY_MAX_ITERS, REACT_MAX_ITERS)

                await _process_file(
                    base_bytes, filename, max_iters,
                    overrides=new_overrides,
                    spec_path=routed_spec,
                )
            else:
                pretty_overrides = json.dumps(new_overrides, ensure_ascii=False, indent=2)
                thinking_msg.content = (
                    f"✅ **已记录您的排版偏好！** 下次上传文档时将自动应用。\n\n"
                    f"**当前完整配置：**\n```json\n{pretty_overrides}\n```\n"
                    f"💡 请直接上传 `.docx` 文件。"
                )
                await thinking_msg.update()
        else:
            thinking_msg.content = (
                "❌ 抱歉，未能从您的指令中提取出有效的排版属性，请换种说法重试。"
            )
            await thinking_msg.update()

        return

    # ════════════════════════════════════════════════════════════════════════
    # 分支 B：普通聊天（无 /f 或 /format 前缀，直接以 Structura 角色回复）
    # ════════════════════════════════════════════════════════════════════════
    import openai as _openai
    history: List[dict] = cl.user_session.get(_KEY_CHAT_HISTORY, [])
    history.append({"role": "user", "content": text})

    try:
        from config import LLM_BASE_URL, LLM_MODEL
        client = _openai.AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=30.0)
        msg = cl.Message(content="")
        await msg.send()
        reply_parts: List[str] = []

        async with await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 Structura 文档格式化助手。"
                        "你可以帮助用户了解文档格式化知识、解答关于本工具的使用问题，也可以进行一般性的中文对话。"
                        "如果用户想对文档进行排版调整，请提醒他使用 `/f + 需求` 的命令格式。"
                    ),
                },
                *history[-_MAX_CHAT_HISTORY:],
            ],
            stream=True,
        ) as stream:
            async for chunk in stream:
                token = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if token:
                    reply_parts.append(token)
                    await msg.stream_token(token)
        await msg.update()

        reply = "".join(reply_parts)
        history.append({"role": "assistant", "content": reply})
        cl.user_session.set(_KEY_CHAT_HISTORY, history)
    except Exception as e:
        await cl.Message(content=f"💬 对话失败：{e}").send()
# ── User feedback handling ─────────────────────────────────────────────────

async def _handle_feedback(message: cl.Message) -> None:
    """Handle user's natural language response for the pending diff items."""
    text = message.content.strip()
    diff_items: List[DiffItem] = cl.user_session.get(_KEY_DIFF_ITEMS, [])
    total = len(diff_items)

    # 显示过渡动画
    thinking_msg = cl.Message(content="⏳ 正在理解您的处理决定...")
    await thinking_msg.send()

    # 🚀 召唤大模型解析意图！
    from agent.intent_parser import parse_feedback_intent
    result = await parse_feedback_intent(text, total)
    await thinking_msg.remove()

    intent = result.get("intent", "unknown")
    rejected = result.get("rejected_indices", [])

    if intent == "unknown":
        await cl.Message(
            content="❓ 没太听懂您的意思，请明确说明您想保留或拒绝哪些建议，或者直接点击上方的按钮哦。"
        ).send()
        return

    # 交给执行引擎
    await _execute_feedback(intent, rejected)

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
