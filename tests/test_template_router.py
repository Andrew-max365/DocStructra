import importlib.util
import sys
from pathlib import Path


_mod_path = Path(__file__).resolve().parents[1] / "agent" / "template_router.py"
_spec = importlib.util.spec_from_file_location("template_router", _mod_path)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
resolve_template = _mod.resolve_template


def test_rule_route_to_gov():
    d = resolve_template("请按党政机关公文格式排版，红头通知")
    assert d.spec_path == "specs/gov.yaml"
    assert d.domain == "gov"


def test_rule_route_to_contract():
    d = resolve_template("这是采购合同，请统一甲乙方条款格式")
    assert d.spec_path == "specs/contract.yaml"
    assert d.domain == "contract"


def test_keep_current_when_weak_signal():
    d = resolve_template("帮我改得更美观一点", current_spec_path="specs/academic.yaml")
    assert d.spec_path == "specs/academic.yaml"
    assert d.source in {"keep", "rule"}


def test_llm_meta_overrides_rule():
    d = resolve_template(
        "按论文排版",
        llm_meta={"domain": "gov"},
        current_spec_path="specs/default.yaml",
    )
    assert d.spec_path == "specs/gov.yaml"
    assert d.source == "llm"
