"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.validate_review.llm_client as _impl

sys.modules[__name__] = _impl
