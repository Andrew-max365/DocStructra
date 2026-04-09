"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.validate_review.doc_audit as _impl

sys.modules[__name__] = _impl
