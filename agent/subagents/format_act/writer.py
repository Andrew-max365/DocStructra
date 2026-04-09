# core/writer.py
from docx.document import Document as DocxDocument

def save_docx(doc: DocxDocument, output_path: str) -> None:
    """将 Document 写入指定路径，写入失败时抛出 IOError 并附带路径信息。"""
    try:
        doc.save(output_path)
    except Exception as e:
        raise IOError(f"保存 DOCX 失败，目标路径：{output_path!r}") from e