# agent/graph/workflow.py
from __future__ import annotations

from langgraph.graph import StateGraph, END, START

from agent.graph.react_schemas import GraphState
from agent.graph.nodes import ingest_node, reason_node, act_node, validate_node, retry_router


def build_react_graph():
    g = StateGraph(GraphState)
    g.add_node("ingest", ingest_node)
    g.add_node("reason", reason_node)
    g.add_node("act", act_node)
    g.add_node("validate", validate_node)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "reason")
    g.add_edge("reason", "act")
    g.add_edge("act", "validate")
    g.add_conditional_edges("validate", retry_router, {"reason": "reason", "end": END})
    return g.compile()


def run_react_agent(
    input_path: str,
    output_path: str,
    spec_path: str = "specs/default.yaml",
    label_mode: str = "rule",
    max_iters: int = 0,
    overrides: dict = None,
) -> dict:
    from config import REACT_MAX_ITERS

    graph = build_react_graph()
    initial_state: GraphState = {
        "input_path": input_path,
        "output_path": output_path,
        "spec_path": spec_path,
        "label_mode": label_mode,
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
    }
    result = graph.invoke(initial_state)
    return result
