"""
端到端验证：LLM labels → get_effective_role → 排版跳过
"""
import sys
sys.path.insert(0, r"c:\Users\糊涂涂\PycharmProjects\CodeX_Agent")

from core.parser import parse_docx_to_blocks
from core.judge import rule_based_labels
from core.formatter import apply_formatting, detect_role
from core.docx_utils import iter_all_paragraphs
from core.spec import load_spec
from collections import Counter

SAMPLE = r"c:\Users\糊涂涂\PycharmProjects\CodeX_Agent\tests\samples\《网安中国》.docx"
OUTPUT = r"c:\Users\糊涂涂\PycharmProjects\CodeX_Agent\tmp_output_page_classify.docx"
SPEC = r"c:\Users\糊涂涂\PycharmProjects\CodeX_Agent\specs\default.yaml"

doc, blocks = parse_docx_to_blocks(SAMPLE)
labels = rule_based_labels(blocks, doc=doc)
labels["_source"] = "unified_workflow"

# 模拟 LLM 识别：前 25 段是封面（含课程要求）
index_to_block = {b.paragraph_index: b for b in blocks}
all_paras = list(iter_all_paragraphs(doc))

print("模拟 LLM 对前 25 段标记为 cover...")
for pidx in range(0, 25):
    b = index_to_block.get(pidx)
    if b:
        labels[b.block_id] = "cover"

# 验证：formatter 里的 get_effective_role 能否读到这些 cover 标签？
# 导入内部函数来验证
from core.formatter import (
    _detect_section_role,
    ROLE_LABELS_FALLBACK_TO_RULE,
)
from docx.text.paragraph import Paragraph

orig_paras = list(iter_all_paragraphs(doc))
para_by_index = {i: p for i, p in enumerate(orig_paras)}
label_by_elem = {}

for b in blocks:
    role = labels.get(b.block_id)
    if not role or role in ROLE_LABELS_FALLBACK_TO_RULE:
        continue
    p = para_by_index.get(b.paragraph_index)
    if p is not None:
        label_by_elem[p._p] = role

section_role_by_elem = _detect_section_role(orig_paras, detect_role)

def get_effective_role_test(p):
    sec_role = section_role_by_elem.get(p._p)
    if sec_role:
        return sec_role
    llm_role = label_by_elem.get(p._p)
    if llm_role:
        return llm_role
    detected = detect_role(p)
    if detected in {"cover", "toc", "requirement", "reference"}:
        return detected
    return detected

print("\n前 25 段的 get_effective_role 结果（修复后）:")
skip_count = 0
for i, p in enumerate(orig_paras[:25]):
    eff = get_effective_role_test(p)
    t = (p.text or "").strip()
    will_skip = eff in ("cover", "toc", "requirement")
    marker = "✅SKIP" if will_skip else "❌PASS"
    print(f"  [{i:2d}] {marker} role={eff:12s} text={t[:40]!r}")
    if will_skip:
        skip_count += 1

print(f"\n>>> 前25段中 {skip_count}/25 会被正确跳过排版")

# 应用实际排版
print("\n应用排版...")
spec = load_spec(SPEC)
report = apply_formatting(doc, blocks, labels, spec)
fc = report.get("summary", {}).get("formatted_counter", {}) or report.get("actions", {}).get("formatted_counter", {})
print(f"formatted_counter: {fc}")

doc.save(OUTPUT)
print(f"\n✅ 输出：{OUTPUT}")
print("请手动打开验证封面/课程要求区是否保留了原始格式")
