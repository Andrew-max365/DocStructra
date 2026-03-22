from io import BytesIO

from docx import Document

from core.awdp import (
    AWDPValidationError,
    get_awdp_prompt_template,
    render_awdp_markdown_to_docx_bytes,
    validate_awdp_markdown,
)


def _valid_awdp_markdown() -> str:
    return """---
protocol: AWDP-1.0
title: 示例文档
lang: zh-CN
---
# 一级标题

这是第一段正文。

这是第二段正文。

## 二级标题

| 列1 | 列2 |
| --- | --- |
| A | B |

```python
print("hello")
```

![流程图](https://example.com/img.png "系统流程图")
"""


def test_prompt_template_contains_protocol_and_rules():
    prompt = get_awdp_prompt_template()
    assert "AWDP-1.0" in prompt
    assert "YAML Front Matter" in prompt
    assert "只允许三层标题" in prompt


def test_validate_awdp_markdown_ok():
    parsed = validate_awdp_markdown(_valid_awdp_markdown())
    assert parsed.front_matter["protocol"] == "AWDP-1.0"
    assert parsed.body.startswith("# 一级标题")


def test_validate_awdp_markdown_rejects_missing_caption_and_code_lang():
    bad = """---
protocol: AWDP-1.0
---
# 标题

``` 
no lang
```

![img](https://example.com/x.png)
"""
    try:
        validate_awdp_markdown(bad)
        assert False, "expected validation error"
    except AWDPValidationError as e:
        assert any("代码块缺少语言声明" in msg for msg in e.errors)
        assert any("图片缺少标题" in msg for msg in e.errors)


def test_render_awdp_markdown_to_docx_bytes_generates_document():
    out = render_awdp_markdown_to_docx_bytes(_valid_awdp_markdown())
    assert isinstance(out, bytes)
    assert len(out) > 0

    doc = Document(BytesIO(out))
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert any("一级标题" in t for t in texts)
    assert any("第一段正文" in t for t in texts)
