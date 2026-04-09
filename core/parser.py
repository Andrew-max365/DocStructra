"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.ingest_parse.parser as _impl

sys.modules[__name__] = _impl
