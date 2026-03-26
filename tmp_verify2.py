import sys, os
sys.path.insert(0, r"c:\Users\糊涂涂\PycharmProjects\CodeX_Agent")
from core.parser import parse_docx_to_blocks
from core.judge import rule_based_labels
from core.formatter import detect_role, _detect_section_role, ROLE_LABELS_FALLBACK_TO_RULE
from core.docx_utils import iter_all_paragraphs

# 找到实际文件
samples_dir = r"c:\Users\糊涂涂\PycharmProjects\CodeX_Agent\tests\samples"
for f in os.listdir(samples_dir):
    print(f"Found: {f!r}")

sample = os.path.join(samples_dir, [f for f in os.listdir(samples_dir) if f.endswith(".docx")][0])
print(f"\nUsing: {sample!r}")

doc, blocks = parse_docx_to_blocks(sample)
labels = rule_based_labels(blocks, doc=doc)
labels["_source"] = "unified_workflow"

# 模拟 LLM 对前 25 段标记为 cover
index_to_block = {b.paragraph_index: b for b in blocks}
for pidx in range(0, 25):
    b = index_to_block.get(pidx)
    if b:
        labels[b.block_id] = "cover"

# 构建 label_by_elem（和 formatter 里一样的逻辑）
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

skip_count = 0
print("\nFirst 25 paragraphs:")
for i, p in enumerate(orig_paras[:25]):
    sec = section_role_by_elem.get(p._p)
    llm = label_by_elem.get(p._p)
    eff = sec or llm or detect_role(p)
    will_skip = eff in ("cover", "toc", "requirement")
    if will_skip:
        skip_count += 1
    print(f"  [{i}] sec={sec} llm={llm} eff={eff} skip={will_skip}")

print(f"\nTOTAL SKIP: {skip_count}/25")
print("PASS" if skip_count > 0 else "FAIL - LLM labels not being read!")
