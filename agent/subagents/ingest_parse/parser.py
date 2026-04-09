# agent/subagents/ingest_parse/parser.py
from dataclasses import dataclass
from typing import List, Tuple
from docx import Document

from .docx_utils import iter_all_paragraphs


@dataclass
class Block:
    block_id: int
    kind: str              # "paragraph"
    text: str
    paragraph_index: int


def parse_docx_to_blocks(docx_path: str = None, doc: Document = None) -> Tuple[Document, List[Block]]:
    if doc is None:
        if docx_path is None:
            raise ValueError("Must provide either docx_path or doc object")
        doc = Document(docx_path)
    blocks: List[Block] = []
    for i, p in enumerate(iter_all_paragraphs(doc), start=1):
        blocks.append(Block(
            block_id=i,
            kind="paragraph",
            text=(p.text or ""),
            paragraph_index=i - 1,
        ))
    return doc, blocks
