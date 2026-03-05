# agent/graph/nodes.py
from __future__ import annotations

from typing import Any

from agent.graph.react_schemas import Action, ActionPlan, Observation, GraphState


def ingest_node(state: GraphState) -> dict:
    from core.parser import parse_docx_to_blocks

    doc, blocks = parse_docx_to_blocks(state["input_path"])
    return {"doc": doc, "blocks": blocks, "labels": {}}


def reason_node(state: GraphState) -> dict:
    iteration = state["current_iter"] + 1
    errors = state["errors"]
    thought = (
        f"迭代 {iteration}: 当前错误 {len(errors)} 条。"
        + (f"准备修复：{str(errors[:3])}" if errors else "开始首轮处理。")
    )

    plan = ActionPlan(
        thought=thought,
        actions=[Action(action_type="no_op", block_id=-1)],
        rationale="规则模式下直接使用规则标签",
    )

    new_thoughts = list(state["thoughts"]) + [thought]
    new_actions = list(state["actions"]) + [plan.model_dump()["actions"]]

    return {
        "thoughts": new_thoughts,
        "actions": new_actions,
        "current_iter": iteration,
    }


def act_node(state: GraphState) -> dict:
    from core.judge import rule_based_labels

    blocks = state["blocks"]
    doc = state["doc"]

    labels = rule_based_labels(blocks, doc=doc)
    labels["_source"] = "react_rule"

    latest_actions = state["actions"][-1] if state["actions"] else []
    for action in latest_actions:
        if isinstance(action, dict):
            atype = action.get("action_type")
            block_id = action.get("block_id", -1)
            params = action.get("params", {})
            if atype == "set_role" and block_id >= 0:
                labels[block_id] = params.get("role", labels.get(block_id, "body"))
            elif atype == "fix_heading_level" and block_id >= 0:
                level = params.get("level", 1)
                labels[block_id] = f"h{level}"

    return {"labels": labels}


def validate_node(state: GraphState) -> dict:
    from core.formatter import apply_formatting
    from core.writer import save_docx
    from core.spec import load_spec

    doc = state["doc"]
    blocks = state["blocks"]
    labels = state["labels"]
    spec = load_spec(state["spec_path"], overrides=state.get("overrides"))

    errors: list = []
    passed = True

    try:
        report = apply_formatting(doc, blocks, labels, spec)
        if not report:
            errors.append("apply_formatting 返回空报告")
            passed = False
    except Exception as e:
        errors.append(f"格式化失败: {e}")
        passed = False
        report = state.get("report", {})

    if passed or state["current_iter"] >= state["max_iters"]:
        try:
            save_docx(doc, state["output_path"])
        except Exception as e:
            errors.append(f"保存文档失败: {e}")

    obs = Observation(
        iteration=state["current_iter"],
        passed=passed,
        errors=errors,
        summary=f"迭代 {state['current_iter']}: {'通过' if passed else '失败'}",
    )

    new_observations = list(state["observations"]) + [obs.model_dump()]

    return {
        "observations": new_observations,
        "errors": errors,
        "passed": passed,
        "report": report if isinstance(report, dict) else {},
        "finished": passed or state["current_iter"] >= state["max_iters"],
    }


def retry_router(state: GraphState) -> str:
    if state["passed"] or state["current_iter"] >= state["max_iters"]:
        return "end"
    return "reason"
