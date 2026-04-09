# agent/visual_reviewer.py
"""
Phase 3: 多模态视觉审查模块

职责：
1. 将排版后的 DOCX 通过 LibreOffice 转换为 PDF
2. 将 PDF 页面渲染为 PNG 图片
3. 将图片发送给多模态 LLM 进行视觉排版审查
4. 返回结构化的审查结果（VisualReviewResult）

依赖说明：
- LibreOffice (soffice)：DOCX → PDF 转换，需系统安装
- pdf2image + poppler：PDF → PNG 渲染，需系统安装 poppler
- 如果上述依赖不可用，模块会 graceful fallback 并跳过视觉审查
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional

import openai
import pydantic

from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_CONNECT_TIMEOUT_S,
    LLM_RETRY_ATTEMPTS,
    LLM_RETRY_BACKOFF_S,
    VISUAL_REVIEW_MODEL,
    VISUAL_REVIEW_MAX_PAGES,
    VISUAL_REVIEW_DPI,
    LIBREOFFICE_PATH,
)
from agent.prompt_templates import VISUAL_REVIEW_SYSTEM_PROMPT, build_visual_review_prompt
from agent.schema import VisualReviewResult, VisualIssue

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class VisualReviewError(Exception):
    """视觉审查过程中的错误"""
    def __init__(self, message: str, error_type: str = "unknown"):
        super().__init__(message)
        self.error_type = error_type  # "libreoffice" | "pdf2image" | "llm" | "parse" | "unknown"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def docx_to_pdf(docx_path: str, output_dir: Optional[str] = None) -> str:
    """
    使用 LibreOffice headless 将 DOCX 转换为 PDF。

    :param docx_path: 输入 DOCX 文件路径
    :param output_dir: 输出目录；None 时使用临时目录
    :return: 生成的 PDF 文件路径
    :raises VisualReviewError: LibreOffice 不可用或转换失败
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="visual_review_")

    docx_path = os.path.abspath(docx_path)
    if not os.path.isfile(docx_path):
        raise VisualReviewError(f"DOCX 文件不存在: {docx_path}", error_type="libreoffice")

    try:
        cmd = [
            LIBREOFFICE_PATH,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", output_dir,
            docx_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise VisualReviewError(
                f"LibreOffice 转换失败 (returncode={result.returncode}): {result.stderr[:500]}",
                error_type="libreoffice",
            )
    except FileNotFoundError:
        raise VisualReviewError(
            f"LibreOffice 未找到 (路径: {LIBREOFFICE_PATH})。"
            "请安装 LibreOffice 或设置 LIBREOFFICE_PATH 环境变量。",
            error_type="libreoffice",
        )
    except subprocess.TimeoutExpired:
        raise VisualReviewError("LibreOffice 转换超时（120s）", error_type="libreoffice")

    # 查找生成的 PDF 文件
    pdf_name = Path(docx_path).stem + ".pdf"
    pdf_path = os.path.join(output_dir, pdf_name)
    if not os.path.isfile(pdf_path):
        raise VisualReviewError(
            f"LibreOffice 转换完成但未找到输出 PDF: {pdf_path}",
            error_type="libreoffice",
        )
    return pdf_path


def pdf_to_images(
    pdf_path: str,
    dpi: int = VISUAL_REVIEW_DPI,
    max_pages: int = VISUAL_REVIEW_MAX_PAGES,
    output_dir: Optional[str] = None,
) -> List[str]:
    """
    将 PDF 页面渲染为 PNG 图片。

    :param pdf_path: PDF 文件路径
    :param dpi: 渲染分辨率
    :param max_pages: 最大渲染页数
    :param output_dir: 输出目录；None 时使用与 PDF 同目录
    :return: PNG 文件路径列表
    :raises VisualReviewError: pdf2image/poppler 不可用或渲染失败
    """
    if output_dir is None:
        output_dir = os.path.dirname(pdf_path)

    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise VisualReviewError(
            "pdf2image 未安装。请运行 pip install pdf2image 并确保系统已安装 poppler。",
            error_type="pdf2image",
        )

    try:
        images = convert_from_path(
            pdf_path,
            dpi=dpi,
            first_page=1,
            last_page=max_pages,
            fmt="png",
        )
    except Exception as e:
        raise VisualReviewError(
            f"PDF 转图片失败: {e}",
            error_type="pdf2image",
        )

    image_paths: List[str] = []
    for i, img in enumerate(images):
        img_path = os.path.join(output_dir, f"page_{i + 1}.png")
        img.save(img_path, "PNG")
        image_paths.append(img_path)

    return image_paths


def encode_image_base64(image_path: str) -> str:
    """将图片文件编码为 base64 字符串。"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ---------------------------------------------------------------------------
# 核心审查函数
# ---------------------------------------------------------------------------

def visual_review(
    docx_path: str,
    spec_summary: str = "",
) -> VisualReviewResult:
    """
    对排版后的 DOCX 文件进行多模态视觉审查。

    流程：DOCX → PDF → PNG → 多模态 LLM → VisualReviewResult

    :param docx_path: 排版后的 DOCX 文件路径
    :param spec_summary: 排版规范摘要文本
    :return: VisualReviewResult 审查结果
    :raises VisualReviewError: 任何步骤失败时抛出
    """
    # 1. DOCX → PDF
    tmp_dir = tempfile.mkdtemp(prefix="visual_review_")
    try:
        logger.info("视觉审查: DOCX → PDF (%s)", docx_path)
        pdf_path = docx_to_pdf(docx_path, output_dir=tmp_dir)

        # 2. PDF → PNG
        logger.info("视觉审查: PDF → PNG (dpi=%d, max_pages=%d)", VISUAL_REVIEW_DPI, VISUAL_REVIEW_MAX_PAGES)
        image_paths = pdf_to_images(pdf_path, output_dir=tmp_dir)
        if not image_paths:
            raise VisualReviewError("PDF 渲染结果为空", error_type="pdf2image")

        # 3. 组装多模态 prompt
        user_prompt = build_visual_review_prompt(
            spec_summary=spec_summary,
            page_count=len(image_paths),
        )

        # 构建多模态消息（包含图片）
        content_parts = [{"type": "text", "text": user_prompt}]
        for img_path in image_paths:
            b64 = encode_image_base64(img_path)
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64}",
                    "detail": "high",
                },
            })

        messages = [
            {"role": "system", "content": VISUAL_REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": content_parts},
        ]

        # 4. 调用多模态 LLM
        logger.info("视觉审查: 调用多模态模型 %s", VISUAL_REVIEW_MODEL)
        raw_response = _call_multimodal_llm(messages)

        # 5. 解析结果
        return _parse_visual_review_response(raw_response)

    finally:
        # 清理临时文件
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _call_multimodal_llm(messages: list) -> str:
    """
    调用多模态 LLM API，支持重试与超时。

    :param messages: 消息列表（含图片）
    :return: 模型输出文本
    :raises VisualReviewError: 调用失败
    """
    if not LLM_API_KEY:
        raise VisualReviewError(
            "LLM_API_KEY 未设置，无法进行视觉审查。",
            error_type="llm",
        )

    client = openai.OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        timeout=openai.Timeout(180, connect=LLM_CONNECT_TIMEOUT_S),
    )

    last_error: Optional[Exception] = None
    for attempt in range(1, LLM_RETRY_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(
                model=VISUAL_REVIEW_MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=4096,
            )
            return response.choices[0].message.content
        except openai.APITimeoutError as e:
            last_error = e
            logger.warning("视觉审查 LLM 超时 (尝试 %d/%d): %s", attempt, LLM_RETRY_ATTEMPTS, e)
        except openai.APIConnectionError as e:
            last_error = e
            logger.warning("视觉审查 LLM 连接失败 (尝试 %d/%d): %s", attempt, LLM_RETRY_ATTEMPTS, e)
        except openai.AuthenticationError as e:
            raise VisualReviewError(f"视觉审查 LLM 鉴权失败: {e}", error_type="llm") from e
        except Exception as e:
            raise VisualReviewError(f"视觉审查 LLM 调用失败: {e}", error_type="llm") from e

        if attempt < LLM_RETRY_ATTEMPTS:
            backoff = LLM_RETRY_BACKOFF_S * (2 ** (attempt - 1))
            time.sleep(backoff)

    raise VisualReviewError(
        f"视觉审查 LLM 调用失败（已重试 {LLM_RETRY_ATTEMPTS} 次）: {last_error}",
        error_type="llm",
    )


def _parse_visual_review_response(raw: str) -> VisualReviewResult:
    """
    解析多模态 LLM 的视觉审查响应。

    :param raw: 原始 JSON 文本
    :return: VisualReviewResult
    :raises VisualReviewError: 解析失败
    """
    try:
        text = _normalize_json_text(raw)
        data = json.loads(text)

        if not isinstance(data, dict):
            raise VisualReviewError("视觉审查响应非 JSON 对象", error_type="parse")

        # 规范化字段
        data = _canonicalize_visual_review(data)
        return VisualReviewResult(**data)

    except json.JSONDecodeError as e:
        raise VisualReviewError(f"视觉审查响应 JSON 解析失败: {e}", error_type="parse") from e
    except pydantic.ValidationError as e:
        raise VisualReviewError(f"视觉审查响应结构校验失败: {e}", error_type="parse") from e
    except VisualReviewError:
        raise
    except Exception as e:
        raise VisualReviewError(f"视觉审查响应解析失败: {e}", error_type="parse") from e


def _canonicalize_visual_review(data: dict) -> dict:
    """规范化视觉审查结果字段，防止 LLM 输出不规范导致解析失败。"""
    result = dict(data)

    # overall_score
    score = result.get("overall_score", 5.0)
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 5.0
    result["overall_score"] = max(0.0, min(10.0, score))

    # issues
    valid_types = {"margin", "alignment", "spacing", "font", "heading", "layout", "other"}
    valid_severities = {"low", "medium", "high"}
    issues = result.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    cleaned_issues = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        issue = dict(item)
        if issue.get("issue_type") not in valid_types:
            issue["issue_type"] = "other"
        if issue.get("severity") not in valid_severities:
            issue["severity"] = "low"
        issue.setdefault("description", "")
        issue.setdefault("suggestion", "")
        cleaned_issues.append(issue)
    result["issues"] = cleaned_issues

    # summary
    result.setdefault("summary", "")

    # needs_reformat
    if "needs_reformat" not in result:
        result["needs_reformat"] = result["overall_score"] < 7.0

    return result


def _normalize_json_text(raw: str) -> str:
    """兼容 Markdown 代码块包装的 JSON 文本。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
