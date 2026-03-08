import os
import json
import pytest
from unittest.mock import patch, MagicMock

from agent.visual_reviewer import visual_review
from agent.schema import VisualReviewResult

def test_visual_review_disabled(monkeypatch):
    """测试：visual_review() 本身不检查 config，调用方负责；确认函数可被正常 import"""
    # visual_review 函数签名可正常访问（不抛 ImportError）
    assert callable(visual_review)

@patch("agent.visual_reviewer.docx_to_pdf")
@patch("agent.visual_reviewer.pdf_to_images")
@patch("agent.visual_reviewer._call_multimodal_llm")
def test_visual_review_success(mock_llm, mock_pdf2img, mock_docx2pdf):
    """测试正常调用链，校验能否解析LLM结果"""
    mock_docx2pdf.return_value = "dummy.pdf"
    mock_pdf2img.return_value = ["page_1.png"]
    
    # 构造合法的 JSON 返回
    mock_llm.return_value = json.dumps({
        "issues": [
            {
                "issue_type": "margin",
                "severity": "high",
                "page": 1,
                "region": "top",
                "description": "页边距太小",
                "suggestion": "增加段前距"
            }
        ],
        "overall_score": 6.5,
        "summary": "结构偏上",
        "needs_reformat": True
    })

    with patch("agent.visual_reviewer.encode_image_base64", return_value="base64ABC") as mock_b64:
        res = visual_review("dummy.docx")
        
        assert isinstance(res, VisualReviewResult)
        assert res.overall_score == 6.5
        assert res.needs_reformat is True
        assert len(res.issues) == 1
        assert res.issues[0].issue_type == "margin"
        assert res.issues[0].severity == "high"

@patch("agent.visual_reviewer.docx_to_pdf")
def test_visual_review_exception(mock_docx2pdf):
    """测试抛出异常的场景"""
    mock_docx2pdf.side_effect = Exception("LibreOffice error")
    
    with pytest.raises(Exception):
        visual_review("dummy.docx")
