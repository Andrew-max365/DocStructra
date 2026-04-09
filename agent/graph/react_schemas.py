"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.orchestrator.graph.react_schemas as _impl

sys.modules[__name__] = _impl
