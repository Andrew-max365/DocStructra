"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.ingest_parse.docling_adapter as _impl

sys.modules[__name__] = _impl
