# core/spec.py
from dataclasses import dataclass
from typing import Any, Dict, Optional

import yaml


@dataclass
class Spec:
    raw: Dict[str, Any]


def _ensure_dict(value: Any, key: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"spec.{key} must be a mapping")
    return value


def _deep_merge(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """
    递归深度合并两个配置字典。update 中的值覆盖 base 中对应键的值；
    若两者均为 dict，则递归合并而非直接替换。
    """
    result = dict(base)
    for k, v in update.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _validate_and_fill_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(data)

    fonts = _ensure_dict(cfg.get("fonts"), "fonts")
    if not fonts.get("zh") or not fonts.get("en"):
        raise ValueError("spec.fonts.zh and spec.fonts.en are required")

    body = _ensure_dict(cfg.get("body"), "body")
    for key in ["font_size_pt", "line_spacing", "space_before_pt", "space_after_pt", "first_line_chars"]:
        if key not in body:
            raise ValueError(f"spec.body.{key} is required")
    body.setdefault("color", None)

    heading = _ensure_dict(cfg.get("heading"), "heading")
    for h in ["h1", "h2", "h3"]:
        hc = dict(_ensure_dict(heading.get(h), f"heading.{h}"))
        for key in ["font_size_pt", "bold", "space_before_pt", "space_after_pt"]:
            if key not in hc:
                raise ValueError(f"spec.heading.{h}.{key} is required")
        hc.setdefault("alignment", "center" if h == "h1" else "left")
        hc.setdefault("color", None)
        heading[h] = hc
    cfg["heading"] = heading

    cleanup = dict(cfg.get("cleanup") or {})
    if cleanup.get("remove_all_blank_paragraphs"):
        cleanup["max_consecutive_blank_paragraphs"] = 0
    cleanup.setdefault("max_consecutive_blank_paragraphs", 1)
    cleanup.setdefault("remove_blank_after_roles", ["h1", "h2", "h3", "caption"])
    cfg["cleanup"] = cleanup

    list_item = dict(cfg.get("list_item") or {})
    list_item.setdefault("left_indent_pt", 18)
    list_item.setdefault("hanging_indent_pt", 18)
    list_item.setdefault("min_run_len", 1)
    list_item.setdefault("num_left_twips", 720)
    list_item.setdefault("num_hanging_twips", 360)
    list_item.setdefault("convert_text_numbers", True)
    cfg["list_item"] = list_item

    paragraph = dict(cfg.get("paragraph") or {})
    paragraph.setdefault("alignment", "justify")
    cfg["paragraph"] = paragraph

    caption = dict(cfg.get("caption") or {})
    caption.setdefault("center", True)
    caption.setdefault("alignment", "center" if caption.get("center", True) else "left")
    cfg["caption"] = caption

    # Semantic roles: abstract / keyword / reference / footer
    # Each role inherits body defaults unless explicitly overridden in the spec.
    body_font_size = float(cfg["body"]["font_size_pt"])
    body_before = float(cfg["body"]["space_before_pt"])
    body_after = float(cfg["body"]["space_after_pt"])

    for role_key, role_defaults in (
        ("abstract", {
            "font_size_pt": body_font_size,
            "bold": False,
            "italic": True,
            "first_line_chars": 2,
            "space_before_pt": body_before,
            "space_after_pt": body_after,
            "alignment": "justify",
        }),
        ("keyword", {
            "font_size_pt": body_font_size,
            "bold": False,
            "italic": False,
            "first_line_chars": 0,
            "space_before_pt": body_before,
            "space_after_pt": body_after,
            "alignment": "left",
        }),
        ("reference", {
            "font_size_pt": body_font_size - 1.5,
            "bold": False,
            "italic": False,
            "first_line_chars": 0,
            "hanging_indent_pt": 18,
            "space_before_pt": 0,
            "space_after_pt": 2,
            "alignment": "left",
        }),
        ("footer", {
            "font_size_pt": 9,
            "bold": False,
            "italic": False,
            "first_line_chars": 0,
            "space_before_pt": 0,
            "space_after_pt": 0,
            "alignment": "center",
        }),
    ):
        role_cfg = dict(cfg.get(role_key) or {})
        for k, v in role_defaults.items():
            role_cfg.setdefault(k, v)
        cfg[role_key] = role_cfg

    return cfg


def load_spec(path: str, overrides: Optional[Dict[str, Any]] = None) -> Spec:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Spec file not found: {path!r}")

    if not isinstance(data, dict):
        raise ValueError("spec file must be a YAML mapping at top-level")

    validated = _validate_and_fill_defaults(data)
    if overrides:
        validated = _deep_merge(validated, overrides)
    return Spec(raw=validated)
