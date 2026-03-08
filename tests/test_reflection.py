import pytest
from agent.graph.react_schemas import GraphState
from agent.graph.nodes import reflect_router

def test_reflect_router_disabled():
    """测试视觉审查功能关闭时的路由"""
    state = GraphState(
        doc=None, blocks=[], labels={}, spec_path="", output_path="",
        current_iter=1, max_iters=3, passed=True, observations=[],
        thoughts=[], actions=[], report={},
        needs_llm=False, triggered_indices=[], hybrid_triggers={},
        proofread_issues=[],
        # visual_review_enabled 默认 False
    )
    # 因为 passed=True，回退到老路由应该返回 end
    assert reflect_router(state) == "end"

def test_reflect_router_enabled_passed():
    """测试开启审查且通过时的路由"""
    state_dict = {
        "visual_review_enabled": True,
        "reflection_count": 0,
        "visual_review_result": {
            "needs_reformat": False,
            "overall_score": 95.0
        },
        "passed": True,  # 虽然验证通过了，但是 reflect_router 接管了
        "current_iter": 1,
        "max_iters": 3
    }
    
    # Needs reformat is False, should end
    assert reflect_router(state_dict) == "end"

def test_reflect_router_enabled_needs_reformat_under_limit():
    """测试开启审查、不通过且未超限时的路由"""
    state_dict = {
        "visual_review_enabled": True,
        "reflection_count": 0,
        "visual_review_result": {
            "needs_reformat": True,
            "overall_score": 60.0
        },
        "passed": True,
        "current_iter": 1,
        "max_iters": 3
    }
    
    # Needs reformat and under max reflection iters, should go to reason
    assert reflect_router(state_dict) == "reason"

def test_reflect_router_enabled_needs_reformat_over_limit(monkeypatch):
    """测试开启审查、不通过但已超限时的路由"""
    monkeypatch.setattr("config.REFLECTION_MAX_ITERS", 2)
    state_dict = {
        "visual_review_enabled": True,
        "reflection_count": 2,  # >= max, should end
        "visual_review_result": {
            "needs_reformat": True,
            "overall_score": 60.0
        },
    }
    assert reflect_router(state_dict) == "end"


def test_reflect_node_skips_when_disabled():
    """测试 reflect_node 在 visual_review_enabled=False 时直接跳过"""
    from agent.graph.nodes import reflect_node

    state = {
        "visual_review_enabled": False,
        "reflection_history": [],
        "reflection_count": 0,
        "thoughts": ["some thought"],
        "actions": [],
        "report": {"meta": {"paragraphs_before": 5}},
        "visual_feedback_for_reason": None,
        "output_path": "nonexistent.docx",
        "spec_path": "specs/default.yaml",
        "overrides": None,
        "current_iter": 1,
    }

    result = reflect_node(state)

    # 应该直接返回，不尝试任何 LibreOffice 转换
    assert result["visual_review_result"] is None
    assert result["visual_feedback_for_reason"] is None
    assert result["reflection_count"] == 0  # 不增加计数
    assert "视觉审查未启用" in result["thoughts"][-1]
    # report 应当原样保留
    assert result["report"]["meta"]["paragraphs_before"] == 5
