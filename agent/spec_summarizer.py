"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.validate_review.spec_summarizer as _impl

sys.modules[__name__] = _impl
