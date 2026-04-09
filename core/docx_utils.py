"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.ingest_parse.docx_utils as _impl

sys.modules[__name__] = _impl
