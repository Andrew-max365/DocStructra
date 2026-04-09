"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.orchestrator.cluster.master_control_agent as _impl

sys.modules[__name__] = _impl
