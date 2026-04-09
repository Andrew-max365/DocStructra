"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.orchestrator.graph.nodes as _impl

sys.modules[__name__] = _impl
