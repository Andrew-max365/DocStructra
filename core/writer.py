"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.format_act.writer as _impl

sys.modules[__name__] = _impl
