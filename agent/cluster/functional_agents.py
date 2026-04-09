"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.orchestrator.cluster.functional_agents as _impl

sys.modules[__name__] = _impl
