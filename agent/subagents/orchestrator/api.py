"""Public API for orchestrator subpackage."""

from agent.subagents.orchestrator.cluster.functional_agents import (
    FormattingExecutionAgent,
    HeaderFooterIntentFallbackAgent,
    IntentUnderstandingAgent,
    JsonGenerationAgent,
    TemplateRoutingAgent,
)
from agent.subagents.orchestrator.cluster.master_control_agent import MasterControlAgent
from agent.subagents.orchestrator.format_service import (
    FormatResult,
    format_docx_bytes,
    format_docx_file,
)
from agent.subagents.orchestrator.graph.react_schemas import Action, ActionPlan, GraphState, Observation
from agent.subagents.orchestrator.graph.workflow import build_react_graph, run_react_agent
from agent.subagents.orchestrator.structura_agent import (
    AgentArtifacts,
    AgentResult,
    build_summary,
    run_doc_agent_bytes,
    run_doc_agent_file,
)

__all__ = [
    "MasterControlAgent",
    "IntentUnderstandingAgent",
    "JsonGenerationAgent",
    "TemplateRoutingAgent",
    "HeaderFooterIntentFallbackAgent",
    "FormattingExecutionAgent",
    "FormatResult",
    "format_docx_file",
    "format_docx_bytes",
    "Action",
    "ActionPlan",
    "Observation",
    "GraphState",
    "build_react_graph",
    "run_react_agent",
    "AgentArtifacts",
    "AgentResult",
    "build_summary",
    "run_doc_agent_file",
    "run_doc_agent_bytes",
]
