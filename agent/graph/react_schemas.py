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
    # --- 新增：雷达触发与校对数据 ---
    needs_llm: bool
    triggered_indices: List[int]
    hybrid_triggers: dict
    proofread_issues: List[dict]
    # --- 页面分类：LLM 识别出的特殊页面（封面/目录等），排版时跳过 ---
    special_page_indices: List[int]           # 需要跳过的段落序号列表
    special_page_region_map: Dict[int, str]   # paragraph_index -> region 类型
    # --- Phase 3: 视觉反思 ---
    visual_review_result: Optional[dict]     # VisualReviewResult 序列化
    reflection_count: int                     # 当前反思迭代次数
    reflection_history: List[dict]            # 反思历史记录
    visual_review_enabled: bool               # 是否启用视觉审查
    visual_feedback_for_reason: Optional[str] # 视觉审查反馈摘要（注入 reason_node）
