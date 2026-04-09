"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.validate_review.schema as _impl

sys.modules[__name__] = _impl
