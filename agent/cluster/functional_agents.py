from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple


@dataclass
class IntentUnderstandingAgent:
    parse_intent: Callable[[str], Awaitable[dict]]

    async def run(self, user_text: str) -> dict:
        return await self.parse_intent(user_text)


@dataclass
class JsonGenerationAgent:
    split_meta_fields: Callable[[dict], Tuple[dict, dict, dict]]

    def run(self, payload: dict) -> Tuple[dict, dict, dict]:
        return self.split_meta_fields(payload)


@dataclass
class TemplateRoutingAgent:
    resolve_template: Callable[..., Any]

    def run(
        self,
        *,
        user_text: str,
        current_spec_path: str,
        llm_meta: Optional[dict] = None,
    ):
        return self.resolve_template(
            user_text,
            current_spec_path=current_spec_path,
            llm_meta=llm_meta,
        )


@dataclass
class HeaderFooterIntentFallbackAgent:
    parse_hft_command: Callable[[str], Dict[str, Any]]

    def run(self, user_text: str) -> Dict[str, Any]:
        return self.parse_hft_command(user_text)


@dataclass
class FormattingExecutionAgent:
    format_docx_file: Callable[..., Any]
    format_docx_bytes: Callable[..., Any]

    def run_file(self, **kwargs):
        return self.format_docx_file(**kwargs)

    def run_bytes(self, **kwargs):
        return self.format_docx_bytes(**kwargs)
