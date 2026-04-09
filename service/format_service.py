"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.orchestrator.format_service as _impl

sys.modules[__name__] = _impl
