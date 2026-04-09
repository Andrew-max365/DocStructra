"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.orchestrator.structura_agent as _impl

sys.modules[__name__] = _impl
