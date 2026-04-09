"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.validate_review.visual_reviewer as _impl

sys.modules[__name__] = _impl
