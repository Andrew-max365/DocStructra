"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.format_act.header_footer_toc as _impl

sys.modules[__name__] = _impl
