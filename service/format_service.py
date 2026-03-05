# service/format_service.py
from __future__ import annotations

import json
import os
import tempfile
import warnings
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from config import LLM_MODE, ENABLE_DOCLING
from core.spec import load_spec
from core.judge import rule_based_labels
from core.formatter import apply_formatting
from core.writer import save_docx
from core.docling_adapter import parse_with_fallback


VALID_LABEL_MODES = {"hybrid"}


def ensure_docx_path(path: str) -> str:
    """若用户传入不带扩展名，则默认补 .docx（兼容你目前 CLI 行为）"""
    root, ext = os.path.splitext(path)
    if ext == "":
        return path + ".docx"
    return path


def default_report_path(output_path: str) -> str:
    root, _ = os.path.splitext(output_path)
    return root + ".report.json"


@dataclass
class FormatResult:
    output_path: str
    report_path: Optional[str]
    report: Dict[str, Any]



def _resolve_labels(blocks, doc, label_mode: str) -> Dict[Any, str]:
    mode = (label_mode or "hybrid").strip().lower()
    if mode not in VALID_LABEL_MODES:
        raise ValueError(f"label_mode must be one of {sorted(VALID_LABEL_MODES)}, got: {label_mode}")

    rule = rule_based_labels(blocks, doc=doc)
    rule["_source"] = "rule_based"

    try:
        from agent.mode_router import ModeRouter
        router = ModeRouter(mode=mode)
        return router.route(doc, blocks, rule)
    except Exception as e:
        n_blocks = len(blocks)
        error_type = getattr(e, "error_type", "unknown")
        warnings.warn(
            f"[format_service] LLM labeling failed ({mode} mode, {n_blocks} blocks, "
            f"error_type={error_type}), falling back to rule-based: {e}",
            stacklevel=2,
        )
        # 兜底：LLM 不可用时回退，保证主流程可用
        rule.setdefault("_warnings", [])
        rule["_warnings"].append(
            f"LLM labeling failed [{error_type}] ({mode} mode, {n_blocks} blocks), "
            f"fallback to rule-based: {e}"
        )
        return rule



def format_docx_file(
    input_path: str,
    output_path: str,
    spec_path: str = "specs/default.yaml",
    report_path: Optional[str] = None,
    write_report: bool = True,
    *,
    label_mode: str = LLM_MODE,
    overrides: dict = None,
) -> FormatResult:
    """
    文件路径版：适合 CLI 或服务端落盘场景。

    - input_path: 输入 docx
    - output_path: 输出 docx（允许不写扩展名，会自动补 .docx）
    - spec_path: YAML 规范文件
    - report_path: 诊断报告路径；None 则默认与 output 同名 .report.json
    - write_report: 是否写 report.json 到磁盘
    - label_mode: rule / llm / hybrid

    返回：FormatResult(output_path, report_path, report_dict)
    """
    input_path = ensure_docx_path(input_path)
    output_path = ensure_docx_path(output_path)
    if report_path is None and write_report:
        report_path = default_report_path(output_path)

    spec = load_spec(spec_path, overrides=overrides)

    doc, blocks = parse_with_fallback(input_path, use_docling=ENABLE_DOCLING)
    labels = _resolve_labels(blocks, doc, label_mode=label_mode)

    report = apply_formatting(doc, blocks, labels, spec)

    for w in labels.get("_warnings", []):
        report.setdefault("warnings", [])
        report["warnings"].append(w)

    # 将 llm/hybrid 模式产出的校对数据写入报告
    if "_llm_proofread" in labels:
        report["llm_proofread"] = labels["_llm_proofread"]
    if "_hybrid_triggers" in labels:
        report["hybrid_triggers"] = labels["_hybrid_triggers"]

    save_docx(doc, output_path)

    if write_report and report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return FormatResult(output_path=output_path, report_path=report_path, report=report)



def format_docx_bytes(
    input_bytes: bytes,
    spec_path: str = "specs/default.yaml",
    *,
    filename_hint: str = "input.docx",
    keep_temp_files: bool = False,
    label_mode: str = LLM_MODE,
    overrides: dict = None,
) -> Tuple[bytes, Dict[str, Any]]:
    """
    bytes 版：适合 UI/API（上传文件）场景。
    返回：(output_docx_bytes, report_dict)

    - filename_hint: 仅用于生成更可读的临时文件名
    - keep_temp_files: 调试用；True 则不删除临时目录
    - label_mode: rule / llm / hybrid
    """
    tmpdir_obj = tempfile.TemporaryDirectory(prefix="docx_agent_")
    tmpdir = tmpdir_obj.name

    try:
        safe_name = os.path.basename(filename_hint) or "input.docx"
        if not safe_name.lower().endswith(".docx"):
            safe_name += ".docx"

        in_path = os.path.join(tmpdir, safe_name)
        out_path = os.path.join(tmpdir, "output.docx")
        report_path = os.path.join(tmpdir, "output.report.json")

        with open(in_path, "wb") as f:
            f.write(input_bytes)

        res = format_docx_file(
            input_path=in_path,
            output_path=out_path,
            spec_path=spec_path,
            report_path=report_path,
            write_report=True,
            label_mode=label_mode,
            overrides=overrides,
        )

        with open(res.output_path, "rb") as f:
            output_bytes = f.read()

        return output_bytes, res.report

    finally:
        if keep_temp_files:
            print(f"[debug] temp files kept at: {tmpdir}")
        else:
            tmpdir_obj.cleanup()
