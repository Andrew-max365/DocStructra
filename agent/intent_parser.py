"""Compatibility wrapper for migrated module."""
from pathlib import Path

_TARGET = Path(__file__).resolve().parent / "subagents" / "intent_route" / "intent_parser.py"
_CODE = _TARGET.read_text(encoding="utf-8")
exec(compile(_CODE, str(_TARGET), "exec"), globals(), globals())
