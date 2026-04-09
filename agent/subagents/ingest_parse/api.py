"""Public API for ingest_parse subpackage."""

from agent.subagents.ingest_parse.docling_adapter import parse_with_docling, parse_with_fallback
from agent.subagents.ingest_parse.docx_utils import (
    copy_run_style,
    delete_paragraph,
    is_drawing_paragraph,
    is_drawing_run,
    is_effectively_blank_paragraph,
    is_mostly_ascii,
    is_pure_drawing_paragraph,
    iter_all_paragraphs,
    iter_paragraph_runs,
    normalize_mixed_runs,
    set_run_fonts,
    split_text_by_script,
)
from agent.subagents.ingest_parse.parser import Block, parse_docx_to_blocks

__all__ = [
    "Block",
    "parse_docx_to_blocks",
    "parse_with_docling",
    "parse_with_fallback",
    "iter_paragraph_runs",
    "iter_all_paragraphs",
    "delete_paragraph",
    "set_run_fonts",
    "normalize_mixed_runs",
    "split_text_by_script",
    "copy_run_style",
    "is_drawing_paragraph",
    "is_pure_drawing_paragraph",
    "is_drawing_run",
    "is_mostly_ascii",
    "is_effectively_blank_paragraph",
]
