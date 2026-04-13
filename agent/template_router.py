"""Compatibility wrapper for migrated module."""
import sys
import agent.subagents.intent_route.template_router as _impl

globals().update(_impl.__dict__)
sys.modules[__name__] = _impl
