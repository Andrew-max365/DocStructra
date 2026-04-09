"""Public API for format_act subpackage."""

from agent.subagents.format_act.formatter import apply_formatting, detect_role
from agent.subagents.format_act.header_footer_toc import parse_header_footer_command
from agent.subagents.format_act.judge import SmartJudge, rule_based_labels
from agent.subagents.format_act.spec import Spec, load_spec
from agent.subagents.format_act.writer import save_docx

__all__ = [
    "Spec",
    "load_spec",
    "rule_based_labels",
    "SmartJudge",
    "apply_formatting",
    "detect_role",
    "save_docx",
    "parse_header_footer_command",
]
