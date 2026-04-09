# agent/subagents/orchestrator/graph/workflow.py
from __future__ import annotations

from langgraph.graph import StateGraph, END, START
from agent.subagents.orchestrator.graph.react_schemas import GraphState
from agent.subagents.orchestrator.graph.nodes import (
    ingest_node, trigger_node, reason_node, act_node, validate_node, 
    reflect_node, retry_router, route_trigger, reflect_router
)


def build_react_graph():
    g = StateGraph(GraphState)
    g.add_node("ingest", ingest_node)
    g.add_node("trigger", trigger_node)
    g.add_node("reason", reason_node)
    g.add_node("act", act_node)
    g.add_node("validate", validate_node)
    g.add_node("reflect", reflect_node)  # Phase 3 新增

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "trigger")

    g.add_conditional_edges("trigger", route_trigger, {"reason": "reason", "validate": "validate"})

    g.add_edge("reason", "act")
    g.add_edge("act", "validate")
    
    # 验证后不再直接结束，而是进入视觉审查环节（控制权在反思路由）
    g.add_edge("validate", "reflect")
    g.add_conditional_edges("reflect", reflect_router, {"reason": "reason", "end": END})
    return g.compile()


async def run_react_agent_stream(
        input_path: str,
        output_path: str,
        spec_path: str = "specs/default.yaml",
        max_iters: int = 0,
        overrides: dict = None,
        visual_review_enabled: bool = False,
):
    """异步流式运行 ReAct Agent，实时生成每个图节点的状态更新"""
    from config import REACT_MAX_ITERS

    graph = build_react_graph()
    initial_state: GraphState = {
        "input_path": input_path,
        "output_path": output_path,
        "spec_path": spec_path,
        "label_mode": "unified",
        "max_iters": max_iters or REACT_MAX_ITERS,
        "current_iter": 0,
        "thoughts": [],
        "actions": [],
        "observations": [],
        "errors": [],
        "passed": False,
        "finished": False,
        "report": {},
        "blocks": None,
        "labels": None,
        "doc": None,
        "overrides": overrides,
        "triggered_indices": [],
        "hybrid_triggers": {},
        "proofread_issues": [],
        "visual_review_result": None,
        "reflection_count": 0,
        "reflection_history": [],
        "visual_review_enabled": visual_review_enabled,
        "visual_feedback_for_reason": None,
    }

    # astream() 会在每个节点执行完毕后，向外产出 (yield) 最新的状态
    async for event in graph.astream(initial_state):
        yield event


def run_react_agent(
    input_path: str,
    output_path: str,
    spec_path: str = "specs/default.yaml",
    #label_mode: str = "rule",  # 已弃用，使用 unified workflow
    max_iters: int = 0,
    overrides: dict = None,
    visual_review_enabled: bool = False,
) -> dict:
    from config import REACT_MAX_ITERS

    graph = build_react_graph()
    initial_state: GraphState = {
        "input_path": input_path,
        "output_path": output_path,
        "spec_path": spec_path,
        "label_mode": "unified",
        "max_iters": max_iters or REACT_MAX_ITERS,
        "current_iter": 0,
        "thoughts": [],
        "actions": [],
        "observations": [],
        "errors": [],
        "passed": False,
        "finished": False,
        "report": {},
        "blocks": None,
        "labels": None,
        "doc": None,
        "overrides": overrides,
        "triggered_indices": [],
        "hybrid_triggers": {},
        "proofread_issues": [],
        # Phase 3: 视觉反思初始值
        "visual_review_result": None,
        "reflection_count": 0,
        "reflection_history": [],
        "visual_review_enabled": visual_review_enabled,
        "visual_feedback_for_reason": None,
    }
    result = graph.invoke(initial_state)
    return result
