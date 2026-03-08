import pytest
from core.spec import Spec
from agent.spec_summarizer import summarize_spec

def test_summarize_spec_default():
    """测试默认规范的摘要生成是否包含关键排版要素"""
    raw_spec = {
        "fonts": {"zh": "宋体", "en": "Times New Roman"},
        "body": {"font_size_pt": 12, "line_spacing": 1.5, "first_line_chars": 2, "space_before_pt": 0, "space_after_pt": 0},
        "paragraph": {"alignment": "justify"},
        "heading": {
            "h1": {"font_size_pt": 16, "bold": True, "alignment": "center", "space_before_pt": 12, "space_after_pt": 6},
            "h2": {"font_size_pt": 14, "bold": True, "alignment": "left", "space_before_pt": 10, "space_after_pt": 4},
            "h3": {"font_size_pt": 12, "bold": True, "alignment": "left", "space_before_pt": 8, "space_after_pt": 2},
        },
        "caption": {"font_size_pt": 10.5, "alignment": "center"},
        "cleanup": {"max_consecutive_blank_paragraphs": 1}
    }
    
    spec = Spec(raw=raw_spec)
    summary = summarize_spec(spec)
    
    # 验证关键信息是否包含
    assert "宋体" in summary
    assert "Times New Roman" in summary
    assert "正文" in summary
    assert "12pt" in summary
    assert "1.5 倍行距" in summary
    assert "一级标题" in summary
    assert "16pt" in summary
    assert "加粗" in summary
    assert "居中" in summary
    assert "段落默认对齐：两端对齐" in summary
    assert "题注" in summary
    assert "10.5pt" in summary
    assert "1 个连续空段" in summary

def test_summarize_spec_remove_all_blanks():
    """测试特殊清理策略的描述"""
    raw_spec = {"cleanup": {"remove_all_blank_paragraphs": True}}
    spec = Spec(raw=raw_spec)
    summary = summarize_spec(spec)
    
    assert "删除所有空段落" in summary
