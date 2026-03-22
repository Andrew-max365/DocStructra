from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import secrets
import zipfile
from dataclasses import asdict
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from agent.Structura_agent import run_doc_agent_bytes
from config import REQUIRE_AUTH, SERVER_API_KEY
from core.awdp import (
    AWDPValidationError,
    get_awdp_prompt_template,
    render_awdp_markdown_to_docx_bytes,
    validate_awdp_markdown,
)

logger = logging.getLogger(__name__)
_RE_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]+$")

# 生产环境 fail-fast：REQUIRE_AUTH=true 时若 SERVER_API_KEY 未设置则拒绝启动
if REQUIRE_AUTH and not SERVER_API_KEY:
    raise RuntimeError(
        "REQUIRE_AUTH=true 但 SERVER_API_KEY 未设置，服务拒绝启动。"
        "请通过环境变量 SERVER_API_KEY 提供鉴权密钥，或将 REQUIRE_AUTH 设为 false（仅限本地 Demo）。"
    )

app = FastAPI(
    title="Structura DOCX Agent API",
    version="0.1.0",
    description="API-ready Word 中文排版 Agent：上传 docx，返回排版结果与可解释报告。",
)


def _verify_api_key(x_api_key: str = Header(default="")) -> None:
    """若 SERVER_API_KEY 已配置，则验证请求头中的 X-API-Key。"""
    if SERVER_API_KEY and not secrets.compare_digest(x_api_key, SERVER_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _validate_spec_path(spec_path: str) -> None:
    """拒绝能逃出 specs/ 目录的路径，防止路径穿越攻击。"""
    if os.path.isabs(spec_path):
        raise HTTPException(status_code=400, detail="spec_path must be a relative path within specs/")
    normalized = os.path.normpath(spec_path)
    if not (normalized.startswith("specs" + os.sep)):
        raise HTTPException(status_code=400, detail="spec_path must point within the specs/ directory")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/awdp/prompt", dependencies=[Depends(_verify_api_key)])
def awdp_prompt_template() -> dict:
    return {"status": "ok", "protocol": "AWDP-1.0", "prompt": get_awdp_prompt_template()}


@app.post("/v1/awdp/validate", dependencies=[Depends(_verify_api_key)])
async def awdp_validate(markdown: str = Form(..., description="待校验的 AWDP Markdown 文本")):
    try:
        validate_awdp_markdown(markdown)
    except AWDPValidationError as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "protocol": "AWDP-1.0", "errors": e.errors},
        )
    return {"status": "ok", "protocol": "AWDP-1.0"}


@app.post("/v1/awdp/render", dependencies=[Depends(_verify_api_key)])
async def awdp_render(
    markdown: str = Form(..., description="符合 AWDP-1.0 的 Markdown 文本"),
    filename: str = Form("awdp_output.docx"),
):
    try:
        out_bytes = render_awdp_markdown_to_docx_bytes(markdown)
    except AWDPValidationError as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "protocol": "AWDP-1.0", "errors": e.errors},
        )
    safe_name = os.path.basename((filename or "").strip() or "awdp_output.docx")
    if not safe_name.lower().endswith(".docx"):
        safe_name += ".docx"
    if not _RE_SAFE_FILENAME.match(safe_name):
        safe_name = "awdp_output.docx"
    return StreamingResponse(
        io.BytesIO(out_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@app.post("/v1/agent/format", dependencies=[Depends(_verify_api_key)])
async def format_docx_json(
    file: UploadFile = File(..., description="待排版的 .docx 文件"),
    spec_path: str = Form("specs/default.yaml"),
    label_mode: Literal["hybrid"] = Form("hybrid"),
):
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    _validate_spec_path(spec_path)

    input_bytes = await file.read()
    if not input_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        out_bytes, agent_res = run_doc_agent_bytes(
            input_bytes,
            spec_path=spec_path,
            filename_hint=file.filename,
            label_mode=label_mode,
        )
    except Exception as e:  # pragma: no cover
        logger.error("format_docx_json failed for %r: %s", file.filename, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e

    return JSONResponse(
        {
            "status": "ok",
            "filename": file.filename,
            "output_docx_base64": base64.b64encode(out_bytes).decode("utf-8"),
            "report": agent_res.report,
            "agent_result": asdict(agent_res),
        }
    )


@app.post("/v1/agent/format/bundle", dependencies=[Depends(_verify_api_key)])
async def format_docx_bundle(
    file: UploadFile = File(..., description="待排版的 .docx 文件"),
    spec_path: str = Form("specs/default.yaml"),
    label_mode: Literal["hybrid"] = Form("hybrid"),
):
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    _validate_spec_path(spec_path)

    input_bytes = await file.read()
    if not input_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        out_bytes, agent_res = run_doc_agent_bytes(
            input_bytes,
            spec_path=spec_path,
            filename_hint=file.filename,
            label_mode=label_mode,
        )
    except Exception as e:  # pragma: no cover
        logger.error("format_docx_bundle failed for %r: %s", file.filename, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("output.docx", out_bytes)
        zf.writestr("report.json", json.dumps(agent_res.report, ensure_ascii=False, indent=2))
        zf.writestr("agent_result.json", json.dumps(asdict(agent_res), ensure_ascii=False, indent=2))

    payload.seek(0)
    return StreamingResponse(
        payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="structura_bundle.zip"'},
    )
