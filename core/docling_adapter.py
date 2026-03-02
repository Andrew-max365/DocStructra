# core/docling_adapter.py
"""
Docling 文档解析适配器。
当 Docling 可用时，使用 Docling 解析文档并映射到现有 Block/role 体系；
不可用或失败时自动回退到原 parser。
"""
from __future__ import annotations

import warnings
from typing import Any, Tuple

DOCLING_AVAILABLE = False
try:
    from docling_core.types.doc import DoclingDocument  # type: ignore
    DOCLING_AVAILABLE = True
except ImportError:
    pass


DOCLING_LABEL_MAP = {
    "title": "h1",
    "section_header": "h2",
    "list_item": "list_item",
    "table": "body",
    "figure": "body",
    "text": "body",
    "caption": "caption",
    "footnote": "footer",
    "page_header": "h1",
    "page_footer": "footer",
    "abstract": "abstract",
    "reference": "reference",
}


def parse_with_docling(file_path: str) -> Tuple[Any, list]:
    """
    使用 Docling 解析文档，返回 (doc_object, blocks_with_roles)。
    doc_object 与原 parser 兼容（python-docx Document）。
    失败时抛出 RuntimeError，调用方应回退原 parser。
    """
    if not DOCLING_AVAILABLE:
        raise RuntimeError("docling-core 未安装，无法使用 Docling 解析")

    try:
        from docling.document_converter import DocumentConverter  # type: ignore
        converter = DocumentConverter()
        result = converter.convert(file_path)
        return result.document, _map_docling_to_blocks(result.document)
    except Exception as e:
        raise RuntimeError(f"Docling 解析失败: {e}") from e


def _map_docling_to_blocks(docling_doc: Any) -> list:
    """将 Docling 文档结构映射到 role hint 列表。"""
    hints: list = []
    try:
        items = getattr(docling_doc, "texts", [])
        for i, item in enumerate(items):
            label = getattr(item, "label", "text")
            role = DOCLING_LABEL_MAP.get(str(label).lower(), "body")
            hints.append({"index": i, "role": role, "text": getattr(item, "text", "")})
    except Exception as e:
        warnings.warn(f"[docling_adapter] 映射 Docling 文档结构失败: {e}", stacklevel=2)
    return hints


def parse_with_fallback(file_path: str, use_docling: bool = False):
    """
    主入口：优先使用 Docling（如果 use_docling=True），失败自动回退原 parser。
    始终返回 (doc, blocks) 元组，与 parse_docx_to_blocks 兼容。
    """
    from core.parser import parse_docx_to_blocks

    if use_docling and DOCLING_AVAILABLE:
        try:
            doc, blocks = parse_with_docling(file_path)
            return doc, blocks
        except Exception as e:
            warnings.warn(
                f"[docling_adapter] Docling 解析失败，自动回退原 parser: {e}",
                stacklevel=2,
            )

    return parse_docx_to_blocks(file_path)
