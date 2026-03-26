# tests/test_mode_differentiation.py
# 验证 hybrid 模式的职责边界与行为
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent.mode_router import (
    ModeRouter,
    _compute_hybrid_triggers,
    _collect_special_page_candidates,
    HYBRID_TRIGGER_UNKNOWN_MIN,
)
from agent.llm_client import LLMCallError
from agent.schema import (
    DocumentProofread, ProofreadIssue,
)
from core.parser import Block


# ---------------------------------------------------------------------------
# 测试辅助：构造 Block 列表
# ---------------------------------------------------------------------------

def _make_block(block_id: int, paragraph_index: int, text: str) -> Block:
    b = MagicMock(spec=Block)
    b.block_id = block_id
    b.paragraph_index = paragraph_index
    b.text = text
    return b


def _make_proofread(issues_data=None) -> DocumentProofread:
    """构造 DocumentProofread 测试对象。"""
    issues = [ProofreadIssue(**i) for i in (issues_data or [])]
    return DocumentProofread(doc_language="zh", issues=issues)


# ---------------------------------------------------------------------------
# 1. _compute_hybrid_triggers 触发条件测试
# ---------------------------------------------------------------------------

class TestHybridTriggers:
    def test_no_trigger_when_all_known_roles(self):
        """全部为确定角色时不应触发。"""
        blocks = [
            _make_block(0, 0, "第一章 引言"),
            _make_block(1, 1, "这是正文内容。"),
            _make_block(2, 2, "一、概述"),
        ]
        rule_labels = {0: "h1", 1: "body", 2: "h2"}
        result = _compute_hybrid_triggers(blocks, rule_labels)
        assert result["triggered"] is False
        assert result["reasons"] == []
        assert len(result["triggered_indices"]) == 0

    def test_trigger_on_unknown_label(self):
        """存在 unknown 标签时应触发。"""
        blocks = [
            _make_block(0, 0, "第一章 引言"),
            _make_block(1, 1, "这是不可识别的段落内容，规则无法判断类型。"),
        ]
        rule_labels = {0: "h1", 1: "unknown"}
        result = _compute_hybrid_triggers(blocks, rule_labels)
        assert result["triggered"] is True
        assert 1 in result["triggered_indices"]
        assert any("unknown" in r for r in result["reasons"])

    def test_trigger_on_long_heading(self):
        """超长标题文本（疑似误分类）应触发。"""
        long_text = "一、" + "这是一段超过三十字的所谓标题内容，实际上可能是正文段落" * 2
        blocks = [
            _make_block(0, 0, long_text),
        ]
        rule_labels = {0: "h2"}
        result = _compute_hybrid_triggers(blocks, rule_labels)
        assert result["triggered"] is True
        assert 0 in result["triggered_indices"]
        assert any("标题层级" in r for r in result["reasons"])

    def test_trigger_on_consecutive_short_body(self):
        """连续多个短正文段落（潜在列表）应触发。"""
        blocks = [
            _make_block(i, i, f"条目内容{i}，短文本") for i in range(4)
        ]
        rule_labels = {i: "body" for i in range(4)}
        result = _compute_hybrid_triggers(blocks, rule_labels)
        assert result["triggered"] is True
        assert any("结构化改写" in r for r in result["reasons"])

    def test_no_trigger_when_long_body_paragraphs(self):
        """长正文段落（非短）不应触发连续 body 触发器。"""
        long_text = "这是一段很长的正文内容，超过六十个字符，所以不应该被识别为潜在列表。" * 3
        blocks = [
            _make_block(i, i, long_text) for i in range(4)
        ]
        rule_labels = {i: "body" for i in range(4)}
        result = _compute_hybrid_triggers(blocks, rule_labels)
        # 长段落不满足"短 body"条件
        assert not any("结构化改写" in r for r in result["reasons"])

    def test_metrics_are_populated(self):
        """触发后 metrics 字典应包含各计数。"""
        blocks = [_make_block(0, 0, "X")]
        rule_labels = {0: "unknown"}
        result = _compute_hybrid_triggers(blocks, rule_labels)
        assert "unknown_count" in result["metrics"]
        assert "ambiguous_heading_count" in result["metrics"]
        assert result["metrics"]["unknown_count"] >= HYBRID_TRIGGER_UNKNOWN_MIN

    def test_collect_special_page_candidates(self):
        """cover/toc/requirement 应被收集为特殊页面候选。"""
        blocks = [
            _make_block(0, 0, "课程报告"),
            _make_block(1, 1, "目录"),
            _make_block(2, 2, "课程要求"),
            _make_block(3, 3, "正文内容"),
        ]
        rule_labels = {0: "cover", 1: "toc", 2: "requirement", 3: "body"}
        candidates = _collect_special_page_candidates(blocks, rule_labels)
        assert candidates == {0, 1, 2}


# ---------------------------------------------------------------------------
# 2. hybrid 模式：无触发时不调用 LLM
# ---------------------------------------------------------------------------

class TestHybridNoTrigger:
    def test_hybrid_skips_llm_when_no_trigger(self):
        """hybrid 模式在无触发时不应调用 LLM。"""
        router = ModeRouter(mode="hybrid")
        doc = MagicMock()
        blocks = [
            _make_block(0, 0, "第一章 引言"),
            _make_block(1, 1, "这是正文内容，描述了本文的主要研究方向和目标。"),
        ]
        rule_labels = {0: "h1", 1: "body"}

        result = router.route(doc, blocks, rule_labels)

        assert result["_source"] == "hybrid"
        assert result["_hybrid_triggers"]["triggered"] is False
        assert result["_hybrid_triggers"]["llm_called"] is False
        # 从未初始化 DocAnalyzer（即从未尝试调用 LLM）
        assert router._analyzer is None
        # 标签来自规则
        assert result[0] == "h1"
        assert result[1] == "body"
        # 无 llm_proofread
        assert "_llm_proofread" not in result

    def test_hybrid_preserves_rule_labels_when_no_trigger(self):
        """无触发时 hybrid 结果的排版标签应来自规则。"""
        hybrid_router = ModeRouter(mode="hybrid")
        doc = MagicMock()
        blocks = [
            _make_block(0, 0, "第一章"),
            _make_block(1, 1, "这是一段正文内容，描述详细信息，超过了六十个字符的限制以避免触发连续 body 触发器。"),
            _make_block(2, 2, "第二章"),
        ]
        rule_labels = {0: "h1", 1: "body", 2: "h1"}

        hybrid_result = hybrid_router.route(doc, blocks, rule_labels)

        # 标签来自规则
        assert hybrid_result[0] == "h1"
        assert hybrid_result[1] == "body"
        assert hybrid_result[2] == "h1"


# ---------------------------------------------------------------------------
# 3. hybrid 模式：触发时调用 LLM（仅对触发段落校对）
# ---------------------------------------------------------------------------

class TestHybridWithTrigger:
    def _make_proofread_for_triggered(self, triggered_idx: int) -> DocumentProofread:
        return _make_proofread(
            issues_data=[
                {
                    "issue_type": "punctuation",
                    "severity": "medium",
                    "paragraph_index": triggered_idx,
                    "evidence": "条目内容，短文本",
                    "suggestion": "句末应加句号",
                    "rationale": "该句缺少句末标点",
                }
            ],
        )

    def test_hybrid_calls_llm_when_triggered(self):
        """hybrid 模式在触发时应调用 LLM 并记录 llm_called=True。"""
        blocks = [
            _make_block(i, i, f"条目{i}，短文本") for i in range(4)
        ]
        rule_labels = {i: "body" for i in range(4)}

        mock_proofread = self._make_proofread_for_triggered(0)
        router = ModeRouter(mode="hybrid")
        mock_client = MagicMock()
        mock_client.call_proofread.return_value = mock_proofread
        mock_analyzer = MagicMock()
        mock_analyzer.client = mock_client
        router._analyzer = mock_analyzer

        with patch.object(ModeRouter, "_extract_paragraphs", return_value=["条目0", "条目1", "条目2", "条目3"]):
            result = router.route(MagicMock(), blocks, rule_labels)

        assert result["_hybrid_triggers"]["triggered"] is True
        assert result["_hybrid_triggers"]["llm_called"] is True
        mock_client.call_proofread.assert_called_once()

    def test_hybrid_llm_proofread_contains_issues(self):
        """hybrid 触发后结果中应包含 _llm_proofread 及问题列表。"""
        blocks = [
            _make_block(i, i, f"条目{i}，短文本") for i in range(4)
        ]
        rule_labels = {i: "body" for i in range(4)}

        mock_proofread = self._make_proofread_for_triggered(0)
        router = ModeRouter(mode="hybrid")
        mock_client = MagicMock()
        mock_client.call_proofread.return_value = mock_proofread
        mock_analyzer = MagicMock()
        mock_analyzer.client = mock_client
        router._analyzer = mock_analyzer

        with patch.object(ModeRouter, "_extract_paragraphs", return_value=["条目0", "条目1", "条目2", "条目3"]):
            result = router.route(MagicMock(), blocks, rule_labels)

        assert "_llm_proofread" in result
        proofread = result["_llm_proofread"]
        assert isinstance(proofread["issues"], list)
        assert len(proofread["issues"]) > 0
        # 验证问题字段完整性
        issue = proofread["issues"][0]
        assert "issue_type" in issue
        assert "severity" in issue
        assert "evidence" in issue
        assert "suggestion" in issue
        assert "rationale" in issue

    def test_hybrid_llm_called_true_on_connection_error(self):
        """hybrid 模式 LLM 调用失败时，llm_called 应为 True（已尝试），llm_error 应记录错误信息。"""
        blocks = [
            _make_block(i, i, f"条目{i}，短文本") for i in range(4)
        ]
        rule_labels = {i: "body" for i in range(4)}

        router = ModeRouter(mode="hybrid")
        mock_client = MagicMock()
        mock_client.call_proofread.side_effect = LLMCallError(
            "LLM 网络连接失败 (尝试 3/3): Connection error.",
            error_type="connect_error",
        )
        mock_analyzer = MagicMock()
        mock_analyzer.client = mock_client
        router._analyzer = mock_analyzer

        with patch.object(ModeRouter, "_extract_paragraphs", return_value=[f"条目{i}" for i in range(4)]):
            result = router.route(MagicMock(), blocks, rule_labels)

        triggers = result["_hybrid_triggers"]
        assert triggers["triggered"] is True
        assert triggers["llm_called"] is True, (
            "llm_called 应为 True：LLM 已被调用（尝试了 3 次），即使最终连接失败"
        )
        assert "llm_error" in triggers
        assert "Connection error" in triggers["llm_error"]
        # 规则标签应被保留
        for i in range(4):
            assert result[i] == "body"

    def test_hybrid_only_proofreads_triggered_paragraphs(self):
        """hybrid 模式 call_proofread 调用时应传入 paragraph_indices（非 None）。"""
        blocks = [
            _make_block(0, 0, "第一章"),               # h1 - 不触发
            _make_block(1, 1, "条目一，短文本"),         # body - 触发
            _make_block(2, 2, "条目二，短文本"),         # body - 触发
            _make_block(3, 3, "条目三，短文本"),         # body - 触发
            _make_block(4, 4, "条目四，短文本"),         # body - 触发
        ]
        rule_labels = {0: "h1", 1: "body", 2: "body", 3: "body", 4: "body"}

        mock_proofread = _make_proofread()
        router = ModeRouter(mode="hybrid")
        mock_client = MagicMock()
        mock_client.call_proofread.return_value = mock_proofread
        mock_analyzer = MagicMock()
        mock_analyzer.client = mock_client
        router._analyzer = mock_analyzer

        with patch.object(ModeRouter, "_extract_paragraphs", return_value=["第一章", "条目一", "条目二", "条目三", "条目四"]):
            router.route(MagicMock(), blocks, rule_labels)

        call_kwargs = mock_client.call_proofread.call_args
        # paragraph_indices 应非 None，且不包含 0（h1 未触发）
        if call_kwargs.kwargs.get("paragraph_indices") is not None:
            indices = call_kwargs.kwargs["paragraph_indices"]
        elif len(call_kwargs.args) > 1:
            indices = call_kwargs.args[1]
        else:
            indices = None
        assert indices is not None, "call_proofread 应传入 paragraph_indices"
        assert 0 not in indices, "未触发的段落不应包含在 paragraph_indices 中"

    def test_hybrid_rule_labels_unchanged_after_proofread(self):
        """hybrid 触发后，排版标签仍应来自规则（LLM 校对不影响结构标签）。"""
        blocks = [
            _make_block(0, 0, "第一章"),
            _make_block(1, 1, "条目一，短文本"),
            _make_block(2, 2, "条目二，短文本"),
            _make_block(3, 3, "条目三，短文本"),
            _make_block(4, 4, "条目四，短文本"),
        ]
        rule_labels = {0: "h1", 1: "body", 2: "body", 3: "body", 4: "body"}

        mock_proofread = _make_proofread()
        router = ModeRouter(mode="hybrid")
        mock_client = MagicMock()
        mock_client.call_proofread.return_value = mock_proofread
        mock_analyzer = MagicMock()
        mock_analyzer.client = mock_client
        router._analyzer = mock_analyzer

        with patch.object(ModeRouter, "_extract_paragraphs", return_value=["第一章", "条目一", "条目二", "条目三", "条目四"]):
            result = router.route(MagicMock(), blocks, rule_labels)

        # 排版标签来自规则，不受 LLM 影响
        assert result[0] == "h1"
        for i in range(1, 5):
            assert result[i] == "body"

    def test_hybrid_special_pages_structure_only_no_proofread(self):
        """
        仅存在特殊页面候选时，hybrid 应调用结构分析复核，但不做校对。
        该用例特意使用不会命中短正文连续触发条件的正文文本，
        以验证“特殊页面识别”可独立触发结构分析。
        """
        blocks = [
            _make_block(0, 0, "课程设计报告"),
            _make_block(1, 1, "目录"),
            _make_block(2, 2, "课程要求"),
            _make_block(3, 3, "第一章 引言"),
            _make_block(4, 4, "这是一段较长正文内容，不会触发短正文连续规则。"),
        ]
        rule_labels = {0: "cover", 1: "toc", 2: "requirement", 3: "h1", 4: "body"}

        from agent.schema import DocumentStructureAnalysis, ParagraphRole
        mock_structure = DocumentStructureAnalysis(
            paragraphs=[
                ParagraphRole(paragraph_index=0, role="cover", confidence=0.95, reason="封面"),
                ParagraphRole(paragraph_index=1, role="toc", confidence=0.95, reason="目录"),
                ParagraphRole(paragraph_index=2, role="requirement", confidence=0.95, reason="要求"),
            ]
        )

        router = ModeRouter(mode="hybrid")
        mock_client = MagicMock()
        mock_client.call_structure_analysis.return_value = mock_structure
        mock_analyzer = MagicMock()
        mock_analyzer.client = mock_client
        router._analyzer = mock_analyzer

        with patch.object(
            ModeRouter,
            "_extract_paragraphs",
            return_value=["课程设计报告", "目录", "课程要求", "第一章 引言", "这是一段较长正文内容"],
        ):
            result = router.route(MagicMock(), blocks, rule_labels)

        triggers = result["_hybrid_triggers"]
        assert triggers["triggered"] is False
        assert triggers["special_page_candidate_count"] == 3
        assert triggers["special_page_llm_called"] is True
        assert triggers["llm_called"] is False
        mock_client.call_structure_analysis.assert_called_once()
        mock_client.call_proofread.assert_not_called()


# ---------------------------------------------------------------------------
# 5. ProofreadIssue / DocumentProofread 字段完整性
# ---------------------------------------------------------------------------

class TestProofreadSchema:
    def test_proofread_issue_required_fields(self):
        """ProofreadIssue 的必要字段应均可设置并序列化。"""
        issue = ProofreadIssue(
            issue_type="typo",
            severity="high",
            paragraph_index=2,
            evidence="公文",
            suggestion="公务",
            rationale="字形相似，易混淆",
        )
        d = issue.model_dump()
        for field in ("issue_type", "severity", "evidence", "suggestion", "rationale"):
            assert field in d, f"字段 {field} 缺失"

    def test_proofread_issue_all_types_valid(self):
        """所有 issue_type 枚举值应可构造。"""
        for itype in ("typo", "punctuation", "standardization"):
            issue = ProofreadIssue(
                issue_type=itype,
                severity="low",
                evidence="e",
                suggestion="s",
                rationale="r",
            )
            assert issue.issue_type == itype

    def test_document_proofread_issues_optional(self):
        """DocumentProofread 的 issues 应默认为空列表。"""
        proofread = DocumentProofread()
        assert proofread.issues == []


# ---------------------------------------------------------------------------
# 7. LLMClient canonicalize proofreading
# ---------------------------------------------------------------------------

class TestLLMClientCanonicalizeProofread:
    def test_normalize_json_text_accepts_plain_json(self):
        from agent.llm_client import LLMClient
        raw = '{"doc_language":"zh","total_paragraphs":0,"paragraphs":[]}'
        assert LLMClient._normalize_json_text(raw) == raw

    def test_normalize_json_text_strips_markdown_json_fence(self):
        from agent.llm_client import LLMClient
        raw = """```json
{"doc_language":"zh","total_paragraphs":0,"paragraphs":[]}
```"""
        assert LLMClient._normalize_json_text(raw) == (
            '{"doc_language":"zh","total_paragraphs":0,"paragraphs":[]}'
        )

    def test_canonicalize_proofread_issue_normalizes_fields(self):
        from agent.llm_client import LLMClient
        raw = {
            "issue_type": "not_valid",  # -> standardization
            "severity": "extreme",      # -> low
        }
        result = LLMClient._canonicalize_proofread_issue(raw)
        assert result["issue_type"] == "standardization"
        assert result["severity"] == "low"
        assert result["evidence"] == ""
        assert result["suggestion"] == ""
        assert result["rationale"] == ""

    def test_canonicalize_proofread_payload_with_issues(self):
        from agent.llm_client import LLMClient
        payload = {
            "doc_language": "zh",
            "issues": [
                {
                    "issue_type": "typo",
                    "severity": "high",
                    "paragraph_index": 0,
                    "evidence": "公文",
                    "suggestion": "公务",
                    "rationale": "错别字",
                }
            ],
        }
        result = LLMClient._canonicalize_proofread_payload(payload)
        assert len(result["issues"]) == 1
        assert result["issues"][0]["issue_type"] == "typo"

    def test_canonicalize_proofread_payload_missing_issues(self):
        from agent.llm_client import LLMClient
        payload = {"doc_language": "zh"}
        result = LLMClient._canonicalize_proofread_payload(payload)
        assert result["issues"] == []
