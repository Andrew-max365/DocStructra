from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import api.server as server_module
from agent.Structura_agent import AgentArtifacts, AgentResult
from api.server import app


@pytest.fixture()
def client():
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_format_docx_json_endpoint(client):
    sample = Path("tests/samples/sample.docx")
    with sample.open("rb") as f:
        resp = client.post(
            "/v1/agent/format",
            files={"file": ("sample.docx", f.read(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"label_mode": "hybrid", "spec_path": "specs/default.yaml"},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert "output_docx_base64" in payload
    assert "report" in payload
    assert payload["agent_result"]["status"] == "ok"


def test_format_docx_bundle_endpoint(client):
    sample = Path("tests/samples/sample.docx")
    with sample.open("rb") as f:
        resp = client.post(
            "/v1/agent/format/bundle",
            files={"file": ("sample.docx", f.read(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"label_mode": "hybrid", "spec_path": "specs/default.yaml"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
    assert len(resp.content) > 0


def test_format_docx_json_endpoint_uses_default_label_mode(client, monkeypatch):
    captured = {}

    def _fake_run_doc_agent_bytes(input_bytes, spec_path, filename_hint, label_mode):
        captured["label_mode"] = label_mode
        return b"fake-docx", AgentResult(
            status="ok",
            task="docx_format_and_audit",
            goal="goal",
            steps=[],
            summary="summary",
            report={},
            artifacts=AgentArtifacts(output_docx_path=None, report_json_path=None),
        )

    monkeypatch.setattr(server_module, "run_doc_agent_bytes", _fake_run_doc_agent_bytes)

    sample = Path("tests/samples/sample.docx")
    with sample.open("rb") as f:
        resp = client.post(
            "/v1/agent/format",
            files={"file": ("sample.docx", f.read(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"spec_path": "specs/default.yaml"},
        )

    assert resp.status_code == 200
    assert captured["label_mode"] == "hybrid"


def test_awdp_prompt_endpoint(client):
    resp = client.get("/v1/awdp/prompt")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["protocol"] == "AWDP-1.0"
    assert "AWDP-1.0" in payload["prompt"]


def test_awdp_validate_and_render_endpoints(client):
    markdown = """---
protocol: AWDP-1.0
title: 示例
---
# 标题

正文段落。
"""
    v_resp = client.post("/v1/awdp/validate", data={"markdown": markdown})
    assert v_resp.status_code == 200
    assert v_resp.json()["status"] == "ok"

    r_resp = client.post(
        "/v1/awdp/render",
        data={"markdown": markdown, "filename": "demo"},
    )
    assert r_resp.status_code == 200
    assert r_resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert b"PK" == r_resp.content[:2]


def test_awdp_validate_rejects_invalid_markdown(client):
    bad = """---
protocol: AWDP-1.0
---
#### 四级标题
"""
    resp = client.post("/v1/awdp/validate", data={"markdown": bad})
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["status"] == "error"
    assert any("标题层级超过三级" in x for x in payload["errors"])


@pytest.mark.parametrize("bad_path", [
    "../../etc/passwd",
    "/etc/passwd",
    "specs/../../../etc/passwd",
    "../secrets.yaml",
])
def test_format_rejects_traversal_spec_path(client, bad_path):
    sample = Path("tests/samples/sample.docx")
    with sample.open("rb") as f:
        data = f.read()

    for endpoint in ["/v1/agent/format", "/v1/agent/format/bundle"]:
        resp = client.post(
            endpoint,
            files={"file": ("sample.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"label_mode": "hybrid", "spec_path": bad_path},
        )
        assert resp.status_code == 400, f"{endpoint} should reject spec_path={bad_path!r}"
