from __future__ import annotations

from dataclasses import dataclass

from agent.cluster.functional_agents import (
    FormattingExecutionAgent,
    HeaderFooterIntentFallbackAgent,
    IntentUnderstandingAgent,
    JsonGenerationAgent,
    TemplateRoutingAgent,
)


@dataclass
class MasterControlAgent:
    intent_agent: IntentUnderstandingAgent | None = None
    json_agent: JsonGenerationAgent | None = None
    template_agent: TemplateRoutingAgent | None = None
    hft_fallback_agent: HeaderFooterIntentFallbackAgent | None = None
    formatting_agent: FormattingExecutionAgent | None = None

    async def parse_formatting_request(
        self,
        user_text: str,
        *,
        current_spec_path: str = "specs/default.yaml",
    ) -> dict:
        """协调意图理解、JSON 拆分、模板路由与 HFT 兜底，返回统一排版请求协议。"""
        if self.intent_agent is None or self.json_agent is None or self.template_agent is None:
            raise RuntimeError("intent/json/template agents are required for parse_formatting_request")

        raw = await self.intent_agent.run(user_text)
        overrides, llm_meta, hft_actions = self.json_agent.run(raw)

        if self.hft_fallback_agent is not None:
            try:
                local_hft = self.hft_fallback_agent.run(user_text)
                for key, val in local_hft.items():
                    if key not in hft_actions:
                        hft_actions[key] = val
            except Exception:
                pass

        decision = self.template_agent.run(
            user_text=user_text,
            current_spec_path=current_spec_path,
            llm_meta=llm_meta,
        )
        return {
            "overrides": overrides,
            "hft_actions": hft_actions,
            "spec_path": decision.spec_path,
            "template": {
                "domain": decision.domain,
                "confidence": decision.confidence,
                "source": decision.source,
                "reason": decision.reason,
            },
        }

    def execute_docx_file(self, **kwargs):
        """协调格式执行 Agent 处理文件路径输入并返回原有 format_docx_file 结果。"""
        if self.formatting_agent is None:
            raise RuntimeError("formatting_agent is not configured")
        return self.formatting_agent.run_file(**kwargs)

    def execute_docx_bytes(self, **kwargs):
        """协调格式执行 Agent 处理二进制输入并返回原有 format_docx_bytes 结果。"""
        if self.formatting_agent is None:
            raise RuntimeError("formatting_agent is not configured")
        return self.formatting_agent.run_bytes(**kwargs)
