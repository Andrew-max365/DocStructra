"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.validate_review.doc_analyzer as _impl

sys.modules[__name__] = _impl
