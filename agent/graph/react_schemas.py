# agent/graph/react_schemas.py
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from typing_extensions import TypedDict

from pydantic import BaseModel

ActionType = Literal[
    "set_role",
    "fix_heading_level",
    "normalize_list",
    "adjust_paragraph_style",
    "no_op",
]


class Action(BaseModel):
    action_type: ActionType
    block_id: int
    params: Dict[str, Any] = {}


class ActionPlan(BaseModel):
    thought: str
    actions: List[Action]
    rationale: str


class Observation(BaseModel):
    iteration: int
    passed: bool
    errors: List[str] = []
    applied_actions: List[str] = []
    summary: str = ""


class GraphState(TypedDict):
    input_path: str
    output_path: str
    spec_path: str
    label_mode: str
    max_iters: int
    current_iter: int
    thoughts: List[str]
    actions: List[List[dict]]
    observations: List[dict]
    errors: List[str]
    passed: bool
    finished: bool
    report: dict
    blocks: Any
    labels: Any
    doc: Any
    overrides: Any
