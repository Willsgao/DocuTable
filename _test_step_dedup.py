# -*- coding: utf-8 -*-
"""测试跨表去重：deduplicate_adjacent_tables

覆盖场景：
1. 无重叠 → 不修改
2. 表头重叠 + 碎片T1 → 从上方表删除
2b. 表头重叠 + 非碎片T1 → V5 保护（不删除）
3. 数据重叠（T2后方+高数值占比）→ 从下方表删除
4. 混合重叠 + 碎片T1 → 按位置+数值综合判定
5. 三表连续重叠 + 碎片T1/T2
6. 空表格 & 单表
7. 元数据内容验证 + 碎片T1
8. 内容指纹容错（空格差异 + 大小写+碎片T1b）
9. repair_info 正确更新 repaired_rows

V5 规则：仅碎片表（num_data==0 或 len≤5且data≤1且hdr_cnt≤1）才允许从其删除表头行
"""

from codes.table_validator.rule_based_repair import deduplicate_adjacent_tables

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  FAILED: {detail}")


# ============================================================
# Case 1: 无重叠 — 两张完全不同的表
# ============================================================
print("\n--- Case 1: 无重叠 ---")
t1 = [["流动资产", "100", "200"], ["固定资产", "50", "80"]]
t2 = [["无形资产", "30", "40"], ["长期投资", "20", "10"]]
info1 = {"original_rows": 2, "repaired_rows": 2}
info2 = {"original_rows": 2, "repaired_rows": 2}
result = deduplicate_adjacent_tables([(t1, info1), (t2, info2)])
check("返回 2 个表", len(result) == 2)
check("表1 不变", result[0][0] == [["流动资产", "100", "200"], ["固定资产", "50", "80"]])
check("表2 不变", result[1][0] == [["无形资产", "30", "40"], ["长期投资", "20", "10"]])
check("info1 无 overlap_removed", "overlap_removed" not in result[0][1])
check("info2 无 overlap_removed", "overlap_removed" not in result[1][1])

# ============================================================
# Case 2: 表头重叠 + 碎片T1 → 从上方删除
#   T1 无数据行（num_data=0），是碎片 → 允许删除其表头行
# ============================================================
print("\n--- Case 2: 表头重叠 + 碎片T1 → 从上方删除 ---")
t1 = [
    ["前置说明文本", "", ""],         # non-overlap, non-numeric
    ["项目", "金额", "占比"],         # overlaps T2[0], non-numeric
    ["科目名称", "描述A", "描述B"],    # overlaps T2[1], non-numeric
]
t2 = [
    ["项目", "金额", "占比"],         # pos 0
    ["科目名称", "描述A", "描述B"],    # pos 1
    ["货币资金", "100", "200"],
    ["应收账款", "50", "80"],
]
info1 = {"original_rows": 3, "repaired_rows": 3}
info2 = {"original_rows": 4, "repaired_rows": 4}
result = deduplicate_adjacent_tables([(t1, info1), (t2, info2)])

check("返回 2 个表", len(result) == 2)
check("表1 删除 2 行重叠", len(result[0][0]) == 1)
check("表1 只保留前置说明", result[0][0][0][0] == "前置说明文本")
check("表2 保持完整 4 行", len(result[1][0]) == 4)
check("info1 overlap_removed count=2",
      result[0][1]["overlap_removed"]["count"] == 2)
check("info1 direction=removed_from_upper",
      result[0][1]["overlap_removed"]["direction"] == "removed_from_upper")
check("info2 无 overlap_removed", "overlap_removed" not in result[1][1])

# ============================================================
# Case 2b: 表头重叠 + 非碎片T1 → V5 保护（不删除）
#   T1 有 3 个数据行，是完整表 → 表头行受保护
# ============================================================
print("\n--- Case 2b: 表头重叠 + 非碎片T1 → V5 保护（不删除）---")
t1 = [
    ["货币资金", "100", "200"],       # data row
    ["应收账款", "50", "80"],         # data row
    ["项目", "金额", "占比"],         # overlaps T2[0], non-numeric
    ["资产", "2024", "2023"],         # overlaps T2[1], numeric
]
t2 = [
    ["项目", "金额", "占比"],         # pos 0
    ["资产", "2024", "2023"],         # pos 1
    ["固定资产", "200", "30%"],
    ["无形资产", "80", "12%"],
]
info1 = {"original_rows": 4, "repaired_rows": 4}
info2 = {"original_rows": 4, "repaired_rows": 4}
result = deduplicate_adjacent_tables([(t1, info1), (t2, info2)])

check("返回 2 个表", len(result) == 2)
check("表1 保持完整 4 行（V5保护）", len(result[0][0]) == 4)
check("表1 首行货币资金", result[0][0][0][0] == "货币资金")
check("表1 末行资产", result[0][0][-1][0] == "资产")
check("表2 保持完整 4 行", len(result[1][0]) == 4)
check("info1 无 overlap_removed（被V5保护）", "overlap_removed" not in result[0][1])
check("info2 无 overlap_removed", "overlap_removed" not in result[1][1])

# ============================================================
# Case 3: 数据重叠 — T2 后方+高数值 → 从 T2 删除
# ============================================================
print("\n--- Case 3: 数据重叠（T2后方+高数值）→ 从下方删除 ---")
t1 = [
    ["科目", "2024", "2023"],
    ["货币资金", "100", "200"],
    ["应收账款", "50", "80"],
    ["固定资产", "200", "30%"],
    ["长期投资", "150", "25%"],
]
t2 = [
    ["一些描述", "", ""],           # pos 0
    ["另一个描述", "", ""],          # pos 1
    ["固定资产", "200", "30%"],     # pos 2, 数值比=0.67≥0.5 → 数据 → 从T2删
    ["无形资产", "80", "12%"],
]
info1 = {"original_rows": 5, "repaired_rows": 5}
info2 = {"original_rows": 4, "repaired_rows": 4}
result = deduplicate_adjacent_tables([(t1, info1), (t2, info2)])

check("返回 2 个表", len(result) == 2)
check("表1 保持完整 5 行", len(result[0][0]) == 5)
check("表2 删除数据重叠行", len(result[1][0]) == 3)
check("表2 剩余: 描述+描述+无形资产",
      result[1][0][0][0] == "一些描述"
      and result[1][0][1][0] == "另一个描述"
      and result[1][0][2][0] == "无形资产")
check("info1 无 overlap_removed", "overlap_removed" not in result[0][1])
check("info2 overlap_removed count=1",
      result[1][1]["overlap_removed"]["count"] == 1)
check("info2 direction=removed_from_lower",
      result[1][1]["overlap_removed"]["direction"] == "removed_from_lower")

# ============================================================
# Case 4: 混合重叠 + 碎片T1 → 按位置+数值综合判定
#   T1 无数据行 → 碎片 → 表头行删除生效
# ============================================================
print("\n--- Case 4: 混合重叠 + 碎片T1 → 综合判定 ---")
t1 = [
    ["前言文字", "", ""],            # non-overlap, non-numeric
    ["项目", "金额", "占比"],         # overlaps T2[0], non-numeric
    ["科目", "描述", "备注"],         # overlaps T2[1], non-numeric
]
t2 = [
    ["项目", "金额", "占比"],         # pos 0
    ["科目", "描述", "备注"],         # pos 1
    ["无形资产", "80", "12%"],
]
info1 = {"original_rows": 3, "repaired_rows": 3}
info2 = {"original_rows": 3, "repaired_rows": 3}
result = deduplicate_adjacent_tables([(t1, info1), (t2, info2)])

check("返回 2 个表", len(result) == 2)
check("表1 删除 2 行重叠", len(result[0][0]) == 1)
check("表1 保留前言文字", result[0][0][0][0] == "前言文字")
check("表2 保持完整 3 行", len(result[1][0]) == 3)
check("info1 overlap_removed count=2",
      result[0][1]["overlap_removed"]["count"] == 2)
check("info2 无 overlap_removed", "overlap_removed" not in result[1][1])

# ============================================================
# Case 5: 三表连续重叠 + 碎片T1/T2
#   T1 无数据行 → 碎片; T2 仅1数据行+1表头行+len=3 → 碎片
# ============================================================
print("\n--- Case 5: 三表连续重叠 + 碎片T1/T2 ---")
t1 = [
    ["前置说明", "", ""],            # non-overlap, non-numeric
    ["科目", "说明A", "说明B"],       # overlaps T2[0], non-numeric
]
t2 = [
    ["科目", "说明A", "说明B"],       # non-numeric
    ["货币资金", "50", "80"],         # data row
    ["类别", "分类A"],               # overlaps T3[0], non-numeric
]
t3 = [
    ["类别", "分类A"],
    ["固定资产", "200"],
    ["无形资产", "80"],
]
info1 = {"original_rows": 2, "repaired_rows": 2}
info2 = {"original_rows": 3, "repaired_rows": 3}
info3 = {"original_rows": 3, "repaired_rows": 3}
result = deduplicate_adjacent_tables([(t1, info1), (t2, info2), (t3, info3)])

check("返回 3 个表", len(result) == 3)
check("表1 只有前置说明", len(result[0][0]) == 1 and result[0][0][0][0] == "前置说明")
check("表2: 科目+货币资金（类别被删）", len(result[1][0]) == 2)
check("表2 首行科目", result[1][0][0][0] == "科目")
check("表3 保持完整 3 行", len(result[2][0]) == 3)
check("info1 有 overlap_removed", "overlap_removed" in result[0][1])
check("info2 有 overlap_removed", "overlap_removed" in result[1][1])

# ============================================================
# Case 6: 空表格 & 单表
# ============================================================
print("\n--- Case 6: 空表格 & 单表 ---")
result = deduplicate_adjacent_tables([])
check("空列表返回空", result == [])

result = deduplicate_adjacent_tables(
    [([["A", "1"], ["B", "2"]], {"needed": True})]
)
check("单表返回原样", len(result) == 1 and result[0][0][0][0] == "A")

t1e = []
t2e = [["X", "10"], ["Y", "20"]]
result = deduplicate_adjacent_tables([(t1e, {}), (t2e, {})])
check("空表+正常表不报错", len(result) == 2)
check("表2 不受影响", result[1][0] == [["X", "10"], ["Y", "20"]])

# ============================================================
# Case 7: 元数据内容验证 + 碎片T1
#   T1 无数据行 → 碎片 → 表头行删除生效
# ============================================================
print("\n--- Case 7: 元数据内容验证 + 碎片T1 ---")
t1 = [["说明", ""], ["项目", "金额"], ["科目", "描述"]]
t2 = [["项目", "金额"], ["科目", "描述"], ["固定资产", "200"]]
info1 = {"original_rows": 3, "repaired_rows": 3}
info2 = {"original_rows": 3, "repaired_rows": 3}
result = deduplicate_adjacent_tables([(t1, info1), (t2, info2)])

overlap = result[0][1].get("overlap_removed", {})
check("overlap 有 count", "count" in overlap)
check("overlap 有 rows", "rows" in overlap)
check("overlap 有 duplicate_with", "duplicate_with" in overlap)
check("overlap 有 direction", "direction" in overlap)
check("rows 是列表", isinstance(overlap.get("rows"), list))
if overlap.get("rows"):
    row = overlap["rows"][0]
    check("row 有 index", "index" in row)
    check("row 有 cells", "cells" in row)
    check("row 有 numeric_ratio", "numeric_ratio" in row)
    check("row 有 reason", "reason" in row)

# ============================================================
# Case 8: 内容指纹容错（空格差异 + 大小写+碎片T1b）
# ============================================================
print("\n--- Case 8: 内容指纹容错 ---")
# 空格差异：不应匹配（空格可能是不同内容）
t1a = [["A", "100"], [" 货币 资金 ", " 200 "]]
t2a = [["货币资金", "200"], ["B", "300"]]
info1 = {"original_rows": 2, "repaired_rows": 2}
info2 = {"original_rows": 2, "repaired_rows": 2}
result = deduplicate_adjacent_tables([(t1a, info1), (t2a, info2)])
check("空格差异不应匹配", len(result[0][0]) == 2 and len(result[1][0]) == 2)

# 大小写差异 + 碎片T1b：应匹配（T1b 无数据行→碎片→允许删除）
t1b = [["Cash", "text"], ["AR", "desc"]]
t2b = [["cash", "text"], ["AR", "desc"]]
result = deduplicate_adjacent_tables([(t1b, {}), (t2b, {})])
# Cash vs cash → 匹配; AR vs AR → 匹配
# T1b num_data=0 → 碎片 → 两行都在 T2 pos≤1 → 全部从 T1 删除
check("大小写忽略匹配 (2行全去重)", len(result[0][0]) == 0 and len(result[1][0]) == 2)

# ============================================================
# Case 9: repair_info 正确更新 repaired_rows
# ============================================================
print("\n--- Case 9: repaired_rows 更新 ---")
t1 = [["A", "1"], ["项目", "金额"]]
t2 = [["项目", "金额"], ["B", "2"], ["C", "3"]]
info1 = {"original_rows": 2, "repaired_rows": 2}
info2 = {"original_rows": 3, "repaired_rows": 3}
result = deduplicate_adjacent_tables([(t1, info1), (t2, info2)])
check("info1 repaired_rows 更新为 1",
      result[0][1].get("repaired_rows") == 1)

# ============================================================
# 汇总
# ============================================================
print(f"\n{'='*50}")
print(f"  总计: {PASS+FAIL} 项, 通过={PASS}, 失败={FAIL}")
if FAIL == 0:
    print("  ALL PASS!")
else:
    print(f"  {FAIL} FAILED!")
print(f"{'='*50}")
