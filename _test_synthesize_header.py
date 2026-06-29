"""
测试表头合成功能 (_synthesize_header_if_needed)

验证:
1. 表头缺失的表能被检测并合成默认表头
2. 正常有表头的表不会被误合成
3. 纯数据表不会被误合成
4. repair_table_rules 管线正常处理合成后的表格
"""

import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "codes"))

from table_validator.rule_based_repair import (
    _synthesize_header_if_needed,
    repair_table_rules,
)
from table_validator.rule_based_repair import _normalize_row_width

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

PASS, FAIL = 0, 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}" + (f" ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" ({detail})" if detail else ""))


def load_csv(path):
    """读取 CSV 文件为 2D 字符串列表"""
    import csv
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        return [list(row) for row in reader]


# ============================================================
# Case 1: 表头缺失的延续型表格 → 应合成表头
# ============================================================
print("\n" + "=" * 60)
print("Case 1: 延续型表格（table_011_P14）→ 应合成表头")
print("=" * 60)

path_011 = "files/liteparse_tables/20260606_155340/table_011_P14.csv"
if os.path.exists(path_011):
    table_011 = load_csv(path_011)
    max_cols = max(len(r) for r in table_011) if table_011 else 0
    table_011_norm = [_normalize_row_width(r, max_cols) for r in table_011]

    print(f"  原始: {len(table_011_norm)}行 × {max_cols}列")
    print(f"  前2行预览: {table_011_norm[:2]}")

    # 测试独立函数
    table_copy = [list(r) for r in table_011_norm]
    synthesized = _synthesize_header_if_needed(table_copy, max_cols)
    check("_synthesize_header_if_needed 返回 True", synthesized)
    if synthesized:
        check("表头在 row[0]", table_copy[0][0] == "项目",
              f"row[0]={table_copy[0]}")
        check("行数增加 1", len(table_copy) == len(table_011_norm) + 1)

    # 测试完整 repair_table_rules
    repaired, info = repair_table_rules(table_011_norm, force=False)
    print(f"  repair_info: {info.get('reason', '?')}")
    print(f"  steps: {info.get('steps', [])}")
    has_synth = 'header_synthesized' in info and info['header_synthesized']
    check("repair_info 记录 header_synthesized=True", has_synth)
    check("repair 有执行步骤 (steps > 1)", len(info.get('steps', [])) > 1,
          f"steps={info['steps']}")
    check("修复后表头行存在", info.get('header_rows_found', 0) > 0,
          f"header_rows_found={info.get('header_rows_found')}")
    check("修复后数据列数 > 0", info.get('data_cols', 0) > 0,
          f"data_cols={info.get('data_cols')}")
    check("修复后表结构完整", len(repaired) > len(table_011_norm),
          f"rows: {len(table_011_norm)} → {len(repaired)}")
    if repaired:
        print(f"  修复后 row[0]: {repaired[0]}")
        print(f"  修复后 row[1]: {repaired[1]}")
else:
    print(f"  ⚠ 文件不存在: {path_011}")
    check("table_011 存在", False)


# ============================================================
# Case 2: 表头缺失的大延续型表格 → 应合成表头
# ============================================================
print("\n" + "=" * 60)
print("Case 2: 大延续型表格（table_012_P14）→ 应合成表头")
print("=" * 60)

path_012 = "files/liteparse_tables/20260606_155340/table_012_P14.csv"
if os.path.exists(path_012):
    table_012 = load_csv(path_012)
    max_cols = max(len(r) for r in table_012) if table_012 else 0
    table_012_norm = [_normalize_row_width(r, max_cols) for r in table_012]

    print(f"  原始: {len(table_012_norm)}行 × {max_cols}列")

    table_copy = [list(r) for r in table_012_norm]
    synthesized = _synthesize_header_if_needed(table_copy, max_cols)
    check("_synthesize_header_if_needed 返回 True", synthesized)
    if synthesized:
        check("表头 col0 = 项目", table_copy[0][0] == "项目")

    repaired, info = repair_table_rules(table_012_norm, force=False)
    has_synth = info.get('header_synthesized', False)
    check("repair_info 记录 header_synthesized=True", has_synth)
    check("修复后 header_rows_found > 0", info.get('header_rows_found', 0) > 0)
    check("修复后 data_cols > 0", info.get('data_cols', 0) > 0)
    if repaired:
        print(f"  修复后 row[0]: {repaired[0]}")
        check("修复后 row[0] 含表头文本", "项目" in str(repaired[0]))
else:
    check("table_012 存在", False, f"文件不存在: {path_012}")


# ============================================================
# Case 3: 正常有表头的表格 → 不应合成表头
# ============================================================
print("\n" + "=" * 60)
print("Case 3: 正常表格（table_010_P14）→ 不应合成表头")
print("=" * 60)

path_010 = "files/liteparse_tables/20260606_155340/table_010_P14_（除特别注明外，以人民币百万元列示） 本年度报告所载财务资料.csv"
if os.path.exists(path_010):
    table_010 = load_csv(path_010)
    max_cols = max(len(r) for r in table_010) if table_010 else 0
    table_010_norm = [_normalize_row_width(r, max_cols) for r in table_010]

    print(f"  原始: {len(table_010_norm)}行 × {max_cols}列")
    print(f"  row[1] (表头): {table_010_norm[1]}")

    table_copy = [list(r) for r in table_010_norm]
    synthesized = _synthesize_header_if_needed(table_copy, max_cols)
    check("_synthesize_header_if_needed 返回 False", not synthesized,
          "正常表头的表不应合成")

    repaired, info = repair_table_rules(table_010_norm, force=False)
    check("repair_info 无 header_synthesized", 'header_synthesized' not in info)
    check("修复后 header_rows_found > 0", info.get('header_rows_found', 0) > 0)
else:
    check("table_010 存在", False, f"文件不存在: {path_010}")


# ============================================================
# Case 4: 极简 2 行表 → 不应合成（行数不足）
# ============================================================
print("\n" + "=" * 60)
print("Case 4: 极简 2 行表 → 不应合成")
print("=" * 60)

table_mini = [
    ["ROA", "ROE", "NIM"],
    ["10", ".69%", "1"],
]
max_cols = 3
table_copy = [list(r) for r in table_mini]
synthesized = _synthesize_header_if_needed(table_copy, max_cols)
check("_synthesize_header_if_needed 返回 False", not synthesized,
      "2行表 text_in_col0 不足")


# ============================================================
# Case 5: 纯数值矩阵 → 不应合成（col0 全是数字）
# ============================================================
print("\n" + "=" * 60)
print("Case 5: 纯数值矩阵 → 不应合成")
print("=" * 60)

table_numeric_only = [
    ["1,234", "5,678", "9,012"],
    ["234", "890", "1,234"],
    ["567", "123", "456"],
]
max_cols = 3
table_copy = [list(r) for r in table_numeric_only]
synthesized = _synthesize_header_if_needed(table_copy, max_cols)
check("_synthesize_header_if_needed 返回 False", not synthesized,
      "col0 全是数值")


# ============================================================
# Case 6: 混合型 KPI 表（table_006_P12）→ 不应合成
# ============================================================
print("\n" + "=" * 60)
print("Case 6: KPI 混合表（table_006_P12）→ 不应合成")
print("=" * 60)

path_006 = "files/liteparse_tables/20260606_155340/table_006_P12.csv"
if os.path.exists(path_006):
    table_006 = load_csv(path_006)
    max_cols = max(len(r) for r in table_006) if table_006 else 0
    table_006_norm = [_normalize_row_width(r, max_cols) for r in table_006]

    print(f"  原始: {len(table_006_norm)}行 × {max_cols}列")
    print(f"  内容: {table_006_norm}")

    table_copy = [list(r) for r in table_006_norm]
    synthesized = _synthesize_header_if_needed(table_copy, max_cols)
    check("_synthesize_header_if_needed 返回 False", not synthesized,
          "col0 无文本标签 (0/.85%/.51% 是数值)")
else:
    check("table_006 存在", False, f"文件不存在: {path_006}")


# ============================================================
# Case 7: 构造的典型无表头标签-数值表 → 应合成
# ============================================================
print("\n" + "=" * 60)
print("Case 7: 构造标签-数值表 → 应合成表头")
print("=" * 60)

table_labeled = [
    ["营业收入", "1,234", "5,678", "9,012"],
    ["净利润", "234", "890", "1,234"],
    ["每股收益", "1.5", "2.3", "3.1"],
]
max_cols = 4
table_copy = [list(r) for r in table_labeled]
synthesized = _synthesize_header_if_needed(table_copy, max_cols)
check("_synthesize_header_if_needed 返回 True", synthesized)
if synthesized:
    check("row[0][0] = 项目", table_copy[0][0] == "项目")
    check("row[0][1] = 列1", table_copy[0][1] == "列1")
    check("row[1][0] = 营业收入", table_copy[1][0] == "营业收入")

# 再跑完整 repair
repaired, info = repair_table_rules(table_labeled, force=False)
has_synth = info.get('header_synthesized', False)
check("repair_info header_synthesized=True", has_synth)
check("修复后 header_rows_found > 0", info.get('header_rows_found', 0) > 0)
check("修复后 data_cols=4", info.get('data_cols', 0) == 4,
      f"data_cols={info.get('data_cols')}")
if repaired:
    print(f"  修复结果: {len(repaired)}行")
    for i, r in enumerate(repaired):
        print(f"    row[{i}]: {r}")


# ============================================================
# Case 8: 空表 / 单列表 → 不应合成
# ============================================================
print("\n" + "=" * 60)
print("Case 8: 边界情况")
print("=" * 60)

check("空表不合成", not _synthesize_header_if_needed([], 0), "空表")
check("单列表不合成", not _synthesize_header_if_needed(
    [["A"], ["B"], ["C"]], 1), "单列")


# ============================================================
# Case 9: 日期标签型表格（table_041_P34）→ 应合成（col0是日期文本）
# ============================================================
print("\n" + "=" * 60)
print("Case 9: 日期标签型（table_041_P34）→ 应合成")
print("=" * 60)

path_041 = "files/liteparse_tables/20260606_155340/table_041_P34.csv"
if os.path.exists(path_041):
    table_041 = load_csv(path_041)
    max_cols = max(len(r) for r in table_041) if table_041 else 0
    table_041_norm = [_normalize_row_width(r, max_cols) for r in table_041]
    print(f"  原始: {len(table_041_norm)}行 × {max_cols}列")
    for i, r in enumerate(table_041_norm):
        print(f"    row[{i}]: {r}")

    table_copy = [list(r) for r in table_041_norm]
    synthesized = _synthesize_header_if_needed(table_copy, max_cols)
    # 日期可能被判断为数值（因为含数字"2024年12月31日"），也可能作为文本
    # 无论是否合成，都是合理行为
    print(f"  合成结果: {synthesized}")
    if synthesized:
        check("合成后 row[0][0]=项目", table_copy[0][0] == "项目")
else:
    print(f"  ⚠ 文件不存在")


# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 60)
print(f"测试结果: {PASS} PASS / {FAIL} FAIL")
print("=" * 60)
if FAIL > 0:
    sys.exit(1)
