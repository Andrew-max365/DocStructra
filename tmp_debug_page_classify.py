"""
诊断脚本：用《网安中国》直接测试页面分类逻辑
运行：python tmp_debug_page_classify.py
"""
import sys, os
sys.path.insert(0, r"c:\Users\糊涂涂\PycharmProjects\CodeX_Agent")

from core.parser import parse_docx_to_blocks
from core.judge import rule_based_labels
from core.docx_utils import iter_all_paragraphs
from core.formatter import detect_role, _detect_section_role

SAMPLE = r"c:\Users\糊涂涂\PycharmProjects\CodeX_Agent\tests\samples\《网安中国》.docx"

print("=== 1. 解析文档 ===")
doc, blocks = parse_docx_to_blocks(SAMPLE)
all_paras = list(iter_all_paragraphs(doc))
print(f"总段落数: {len(all_paras)}, 总 block 数: {len(blocks)}")

print("\n=== 2. 前 50 段的 detect_role 结果 ===")
for i, p in enumerate(all_paras[:50]):
    t = (p.text or "").strip()
    role = detect_role(p)
    style = ""
    try:
        style = p.style.name or ""
    except:
        pass
    print(f"  [{i:3d}] role={role:12s} style={style:20s} text={t[:40]!r}")

print("\n=== 3. _detect_section_role 结果（section-level 状态机）===")
section_map = _detect_section_role(all_paras, detect_role)
# 统计各类
from collections import Counter
sec_counter = Counter()
for p in all_paras:
    sc = section_map.get(p._p, None)
    sec_counter[sc] += 1

print(f"section_role 分布: {dict(sec_counter)}")

print("\n=== 4. rule_based_labels 结果（前50段）===")
labels = rule_based_labels(blocks, doc=doc)
index_to_block = {b.paragraph_index: b for b in blocks}
for i in range(min(50, len(all_paras))):
    b = index_to_block.get(i)
    if b:
        rule_role = labels.get(b.block_id, "N/A")
        # 与 detect_role 对比
        det_role = detect_role(all_paras[i])
        sec_role = section_map.get(all_paras[i]._p, None)
        t = (all_paras[i].text or "").strip()
        print(f"  [{i:3d}] block_id={b.block_id:4d} rule={rule_role:12s} detect={det_role:10s} section={str(sec_role):12s} text={t[:35]!r}")

print("\n=== 5. get_effective_role 结果（前50段）===")
# 模拟 formatter 里的 get_effective_role
def get_effective_role(p, label_by_elem, section_map):
    sec_role = section_map.get(p._p)
    if sec_role:
        return sec_role
    det = detect_role(p)
    if det in {"cover", "toc", "requirement", "reference"}:
        return det
    return label_by_elem.get(p._p) or det

label_by_elem = {}
para_by_index = {i: p for i, p in enumerate(all_paras)}
for b in blocks:
    from core.formatter import ROLE_LABELS_FALLBACK_TO_RULE
    role = labels.get(b.block_id)
    if not role or role in ROLE_LABELS_FALLBACK_TO_RULE:
        continue
    p = para_by_index.get(b.paragraph_index)
    if p is not None:
        label_by_elem[p._p] = role

skipped = []
for i, p in enumerate(all_paras[:50]):
    eff_role = get_effective_role(p, label_by_elem, section_map)
    t = (p.text or "").strip()
    will_skip = eff_role in ("cover", "toc", "requirement")
    marker = "⏭ SKIP" if will_skip else "      "
    print(f"  [{i:3d}] {marker} eff_role={eff_role:12s} text={t[:40]!r}")
    if will_skip:
        skipped.append(i)

print(f"\n>>> 总计 {len(skipped)} 个段落会被跳过排版: indices={skipped}")
print("\n=== 6. 问题诊断 ===")

# 检查封面和目录识别情况（只看前20段）
cover_count = sum(1 for i in range(min(20, len(all_paras))) 
                  if detect_role(all_paras[i]) == "cover")
toc_count = sum(1 for i in range(min(30, len(all_paras)))
                if section_map.get(all_paras[i]._p) == "toc")
print(f"前20段中 detect_role==cover 的: {cover_count}")
print(f"前30段中 section_map==toc 的: {toc_count}")

# 看第一段是什么
if all_paras:
    p0 = all_paras[0]
    print(f"\n第0段: text={p0.text!r}, style={p0.style.name!r}, detect_role={detect_role(p0)!r}")
