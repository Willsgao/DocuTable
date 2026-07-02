"""披露表保护 vs 原结构分裂：中间重复表头应走原逻辑。"""
import copy
import sys

from codes.table_validator.table_content_splitter import (
    is_pillar_disclosure_table_body,
    split_mixed_table_entry,
)
from codes.table_validator.hybrid_segmenter import _split_fused_table_by_structure

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# 模拟 page5 贷款阶段表：2024 块 + 中间日期/列名 + 2023 块
LOAN_STAGE = [
    ["", "2024年", "2023年", "2024年", "2023年"],
    ["", "阶段一", "阶段二", "阶段三", "合计"],
    ["2024年12月31日", "", "", "", ""],
    ["1", "label", "100", "200", "300", "600"],
    ["2", "x", "10", "20", "30", "60"],
    ["3", "y", "1", "2", "3", "6"],
    ["2023年12月31日", "", "", "", ""],
    ["", "阶段一", "阶段二", "阶段三", "合计"],
    ["1", "label", "90", "80", "70", "240"],
    ["2", "x", "9", "8", "7", "24"],
    ["3", "y", "1", "1", "1", "3"],
]

check("loan stage NOT pillar protected", not is_pillar_disclosure_table_body(LOAN_STAGE))

table = {"type": "table", "page": 5, "data": copy.deepcopy(LOAN_STAGE), "y0": 100, "y1": 400}
parts = split_mixed_table_entry(table)
check("loan stage uses original split path", len(parts) >= 1, f"n={len(parts)}")

struct = _split_fused_table_by_structure([copy.deepcopy(table)], None)
check("loan stage structure split allowed", len(struct) >= 1, f"n={len(struct)}")

print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
