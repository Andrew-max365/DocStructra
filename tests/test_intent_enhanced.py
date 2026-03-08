import pytest
from agent.intent_classifier import (
    IntentType, IntentContext, IntentResult,
    classify_intent, classify_intent_enhanced
)

def test_classify_intent_visual_review_keyword():
    """测试通过关键字规则准确分类视觉审查需求"""
    res = classify_intent("帮我检查一下排版的美观度")
    assert res.intent == IntentType.VISUAL_REVIEW
    assert res.confidence >= 0.8
    
    res2 = classify_intent("这页面视觉效果跑偏了，调整下")
    assert res2.intent == IntentType.VISUAL_REVIEW

def test_classify_intent_enhanced_context_feedback():
    """测试在上下文加持下短输入被识别为FEEDBACK"""
    # 模拟刚收到一条视觉反馈
    ctx = IntentContext(has_pending_visual_review=True)
    # 给一个排版关键词，但很短，比如"再紧凑点"
    res = classify_intent_enhanced("再紧凑点", context=ctx)
    
    assert res.intent == IntentType.FEEDBACK
    assert res.source == "context"
    
def test_classify_intent_enhanced_llm_fallback(monkeypatch):
    """测试无法被规则匹配的长文本 Fallback 到 LLM"""
    def mock_llm_classify(text, context):
        return IntentResult(intent=IntentType.FORMAT, confidence=0.88, source="llm")
        
    monkeypatch.setattr("agent.intent_classifier.classify_intent_with_llm", mock_llm_classify)
    
    # 一个模糊的长句，没有触发明显的FORMAT关键字，也没有触发 QUERY 关键词（注意避开“报告”等词）
    ambiguous_text = "我这篇文档第一部分好像和大家期望的风格不太一样，你能帮忙让它变得符合学术期刊的标准吗？"
    
    res = classify_intent_enhanced(ambiguous_text)
    assert res.intent == IntentType.FORMAT
    assert res.source == "llm"
    assert res.confidence == 0.88

def test_intent_context_history():
    """测试上下文记忆轮数和计数统计"""
    ctx = IntentContext()
    for _ in range(3):
        ctx.add(IntentResult(intent=IntentType.FORMAT, confidence=0.9))
        
    assert ctx.last_intent == IntentType.FORMAT
    assert ctx.recent_format_count == 3
    
    ctx.add(IntentResult(intent=IntentType.QUERY, confidence=0.9))
    assert ctx.last_intent == IntentType.QUERY
    assert len(ctx.history) == 4
    # 连续 format 段被打断
    assert ctx.recent_format_count == 0
