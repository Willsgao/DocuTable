# -*- coding: utf-8 -*-
"""验证表头分组填充修复 — 按标签顺序均匀分配，不依赖列位置"""
import sys
import io
import traceback

sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from codes.table_validator.rule_based_repair import (
    repair_table_rules,
    generate_rules_repair_report,
    _detect_bottom_header_group_size,
    _fill_labels_evenly,
    _is_effectively_empty,
)

PASS = "✅ PASS"
FAIL = "❌ FAIL"


def print_header(title):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")


# ════════════════════════════════════════════════════════════
# 单元测试1: _detect_bottom_header_group_size
# ════════════════════════════════════════════════════════════
print_header("单元测试: _detect_bottom_header_group_size")

test_cases = [
    # (name, bottom_row, expected_size)
    ("金额/占比对重复6次", ["（单位）", "金额", "占比(%)", "金额", "占比(%)", "金额", "占比(%)", "金额", "占比(%)", "金额", "占比(%)", "金额", "占比(%)"], 2),
    ("期数/金额对重复4次", ["", "期数", "金额", "期数", "金额", "期数", "金额", "期数", "金额"], 2),
    ("Q1-Q4重复2次（2年）", ["", "Q1", "Q2", "Q3", "Q4", "Q1", "Q2", "Q3", "Q4"], 4),
    ("无重复·全部不同", ["", "Name", "Age", "City", "Salary"], 1),
    ("无重复·只有两个不同", ["", "2024年", "2023年"], 1),
    ("单列底表头", ["", "金额"], 1),
    ("空的col0+重复", ["", "A", "B", "A", "B"], 2),
]

for name, row, expected in test_cases:
    result = _detect_bottom_header_group_size(row)
    status = PASS if result == expected else FAIL
    print(f"  {status} [{name}] expected={expected}, got={result}")


# ════════════════════════════════════════════════════════════
# 单元测试2: _fill_labels_evenly
# ════════════════════════════════════════════════════════════
print_header("单元测试: _fill_labels_evenly（标签顺序均匀分配）")

# 测试A: 用户的核心案例 — 中层表头（6个标签、13列）
row_a = ["", "建信理财", "", "本行", "", "本集团", "", "建信理财", "", "本行", "", "本集团", ""]
expected_a = ["", "建信理财", "建信理财", "本行", "本行", "本集团", "本集团",
              "建信理财", "建信理财", "本行", "本行", "本集团", "本集团"]
result_a = _fill_labels_evenly(row_a, 13, 2)
status_a = PASS if result_a == expected_a else FAIL
print(f"  {status_a} 中层（6个机构×2列=12个数据列）")
print(f"    期望: {expected_a}")
print(f"    结果: {result_a}")
if status_a == FAIL:
    # 逐列对比
    for i in range(13):
        if result_a[i] != expected_a[i]:
            print(f"    差异 @ col {i}: '{result_a[i]}' ≠ '{expected_a[i]}'")

# 测试B: 顶层表头（2个年份标签、13列）
row_b = ["", "2024年12月31日", "", "", "", "", "", "2023年12月31日", "", "", "", "", ""]
expected_b = ["", "2024年12月31日", "2024年12月31日", "2024年12月31日", "2024年12月31日", "2024年12月31日", "2024年12月31日",
              "2023年12月31日", "2023年12月31日", "2023年12月31日", "2023年12月31日", "2023年12月31日", "2023年12月31日"]
result_b = _fill_labels_evenly(row_b, 13, 2)
status_b = PASS if result_b == expected_b else FAIL
print(f"  {status_b} 顶层（2个年份×6列=12个数据列）")
print(f"    期望: {expected_b}")
print(f"    结果: {result_b}")

# 测试C: 简单2标签2列（标签已各占一位，无需填充）
row_c = ["", "2024年", "2023年"]
expected_c = ["", "2024年", "2023年"]
result_c = _fill_labels_evenly(row_c, 3, 1)
status_c = PASS if result_c == expected_c else FAIL
print(f"  {status_c} 简单（2标签2数据列，已对齐）")
print(f"    期望: {expected_c}")
print(f"    结果: {result_c}")

# 测试D: 标签位置偏移
row_d = ["", "", "", "A", "", "B", "", "", "C", ""]
expected_d = ["", "A", "A", "A", "B", "B", "B", "C", "C", "C"]  # 9 cols, 去掉col0=8数据列, 3标签, span=2
result_d = _fill_labels_evenly(row_d, 10, 1)
status_d = PASS if result_d == expected_d else FAIL
print(f"  {status_d} 偏移标签（A在col3,B在col5,C在col8 → 均匀分配）")
print(f"    期望: {expected_d}")
print(f"    结果: {result_d}")

# 测试E: 标签数和列数不能整除
row_e = ["", "X", "", "Y", ""]
expected_e = ["", "X", "X", "X", "Y"]  # 4 data cols, 2 labels, span=2 → even
# 换成5个数据列、2个标签的版本
row_e2 = ["", "X", "", "", "Y", ""]   # 5 data cols, 2 labels, span=2 remainder=1
expected_e2 = ["", "X", "X", "X", "Y", "Y"]  # X gets 3 cols (2+1), Y gets 2 cols
result_e2 = _fill_labels_evenly(row_e2, 6, 1)
status_e2 = PASS if result_e2 == expected_e2 else FAIL
print(f"  {status_e2} 不能整除（2标签、5数据列 → X跨3列、Y跨2列）")
print(f"    期望: {expected_e2}")
print(f"    结果: {result_e2}")

# 测试F: 没有标签的情况
row_f = ["", "", "", ""]
expected_f = ["", "", "", ""]
result_f = _fill_labels_evenly(row_f, 4, 1)
status_f = PASS if result_f == expected_f else FAIL
print(f"  {status_f} 无标签（保持原样）")
print(f"    期望: {expected_f}")
print(f"    结果: {result_f}")


# ════════════════════════════════════════════════════════════
# 集成测试: 完整修复流程
# ════════════════════════════════════════════════════════════
print_header("集成测试: 完整修复流程")

def run_full_test(name, table, check_fn=None):
    """运行完整修复并打印结果"""
    print(f"\n  --- {name} ---")
    print(f"  原始 ({len(table)} rows):")
    for i, row in enumerate(table):
        print(f"    [{i:2d}] {row}")

    try:
        repaired, info = repair_table_rules(table)
        print(f"\n  修复后 ({len(repaired)} rows × {info['data_cols']} cols):")
        for i, row in enumerate(repaired):
            print(f"    [{i:2d}] {row}")

        if check_fn:
            check_fn(repaired, info)

        print(f"  needed={info['needed']}")
        print(f"  anomalies={len(info.get('anomalies', []))}")
    except Exception as e:
        print(f"  {FAIL} 异常: {e}")
        traceback.print_exc()
        return

# ---- 测试1: 用户原始案例（理财表格） ----
table1_raw = [
    # 顶层: 年份行（标签位置可能在中间）
    ["", "", "2024年12月31日", "", "", "", "", "2023年12月31日", "", "", "", "", ""],
    # 中层: 机构行
    ["", "建信理财", "", "本行", "", "本集团", "", "建信理财", "", "本行", "", "本集团", ""],
    # 底层: 指标行
    ["（人民币百万元，百分比除外）", "金额", "占比(%)", "金额", "占比(%)", "金额", "占比(%)",
     "金额", "占比(%)", "金额", "占比(%)", "金额", "占比(%)"],
    # 数据行
    ["现金、存款及同业存单", "1,008,220", "60.80", "20,512", "34.60", "1,028,732", "59.90",
     "907,809", "58.73", "31,462", "32.01", "939,271", "57.14"],
    ["债券", "440,983", "26.60", "5,052", "8.52", "446,035", "25.97",
     "478,169", "30.94", "7,942", "8.08", "486,111", "29.57"],
    ["总额", "1,658,154", "100.00", "59,285", "100.00", "1,717,439", "100.00",
     "1,545,582", "100.00", "98,281", "100.00", "1,643,863", "100.00"],
]


def check_case1(repaired, info):
    """验证修复结果"""
    if len(repaired) < 3:
        print(f"  {FAIL} 表头不足3行")
        return
    # 顶层表头（从上到下第一行）
    top_row = repaired[0]
    # 检查 '2024年12月31日' 是否均匀分布在 col 1-6
    has_2024 = [c for c in top_row[1:7] if '2024' in c]
    has_2023 = [c for c in top_row[7:] if '2023' in c]

    ok1 = len(has_2024) == 6 and all('2024' in c for c in has_2024)
    ok2 = len(has_2023) == 6 and all('2023' in c for c in has_2023)

    print(f"  {'✅' if ok1 else '❌'} 2024年跨6列: {has_2024}")
    print(f"  {'✅' if ok2 else '❌'} 2023年跨6列: {has_2023}")

    # 中层表头
    mid_row = repaired[1]
    # 检查机构标签是否均匀
    expected_mid = [
        '', '建信理财', '建信理财', '本行', '本行', '本集团', '本集团',
        '建信理财', '建信理财', '本行', '本行', '本集团', '本集团'
    ]
    mid_ok = mid_row == expected_mid
    print(f"  {'✅' if mid_ok else '❌'} 机构各跨2列")
    if not mid_ok:
        print(f"    期望: {expected_mid}")
        print(f"    结果: {mid_row}")

    # 底层表头
    bot_row = repaired[2]
    bot_ok = bot_row[0] != "" and bot_row[1] == "金额" and bot_row[2] == "占比(%)"
    print(f"  {'✅' if bot_ok else '❌'} 底层表头保持原样")


run_full_test("案例1: 建信理财表格（3层表头）", table1_raw, check_case1)


# ---- 测试2: 原有场景——确保不改坏已有正常案例 ----
print(f"\n  --- 场景2: 已有正常案例（第一列表头+数据错位） ---")
table2 = [
    ["下表列出所示日期本集团股东权益总额及构成情况。", "", "", "", ""],
    ["", "2024年", "2023年", "", ""],
    ["（人民币百万元）", "", "12月31日", "", "12月31日"],
    ["股本", "", "250,011", "", "250,011"],
    ["资本公积", "", "135,736", "", "135,619"],
]
try:
    repaired2, info2 = repair_table_rules(table2)
    print(f"  repaired: {len(repaired2)} rows × {info2['data_cols']} cols")
    for i, row in enumerate(repaired2):
        print(f"    [{i:2d}] {row}")
    # 验证顶层表头
    top2 = repaired2[0]
    top2_ok = top2[1] == "2024年" and top2[2] == "2023年"
    print(f"  {'✅' if top2_ok else '❌'} 顶层表头正确: {top2}")
except Exception as e:
    print(f"  {FAIL} {e}")
    traceback.print_exc()


# ---- 测试3: 第一列表头为空 ----
print(f"\n  --- 场景3: 第一列表头为空+数据有行标签 ---")
table3 = [
    ["", "2024年", "2023年"],
    ["", "", "12月31日", "12月31日"],
    ["资产总计", "3,343,965", "3,172,074"],
    ["流动资产", "1,500,000", "1,400,000"],
]
try:
    repaired3, info3 = repair_table_rules(table3)
    for i, row in enumerate(repaired3):
        print(f"    [{i:2d}] {row}")
    top3 = repaired3[0]
    top3_ok = top3[1] == "2024年" and top3[2] == "2023年"
    print(f"  {'✅' if top3_ok else '❌'} 顶层正确: {top3}")
except Exception as e:
    print(f"  {FAIL} {e}")
    traceback.print_exc()


# ---- 测试4: 4组季度重复 ---------
print(f"\n  --- 场景4: 多个子列重复 ---")
# 模拟：2024年 vs 2023年，每年有 Q1期数/Q1金额/Q2期数/Q2金额/Q3期数/Q3金额/Q4期数/Q4金额
# 底层表头: 期数/金额 重复8次
bottom4 = [""]
for _ in range(8):
    bottom4.extend(["期数", "金额"])
# 中层: Q1/Q2/Q3/Q4 重复2次
mid4 = [""]
for _ in range(2):
    for q in ["Q1", "Q2", "Q3", "Q4"]:
        mid4.append(q)
        mid4.append("")
# 顶层: 年份重复
top4 = ["", "2024年"]
top4 += [""] * 8
top4 += ["2023年"]
top4 += [""] * 7
# 数据：4个季度×2指标×2年份 = 16列 + 1行标签 = 17列
data4 = ["总额"]
data4 += [str(i) for i in range(16)]

table4 = [top4, mid4, bottom4, data4]
try:
    repaired4, info4 = repair_table_rules(table4)
    print(f"  repaired: {len(repaired4)} rows × {info4['data_cols']} cols")
    for i, row in enumerate(repaired4):
        print(f"    [{i:2d}] {row}")
    top4_result = repaired4[0]
    has_2024 = [c for c in top4_result[1:9] if '2024' in c]
    has_2023 = [c for c in top4_result[9:] if '2023' in c]
    ok_4a = len(has_2024) == 8 and all('2024' in c for c in has_2024)
    ok_4b = len(has_2023) == 8 and all('2023' in c for c in has_2023)
    print(f"  {'✅' if ok_4a else '❌'} 2024年跨8列: {len(has_2024)}")
    print(f"  {'✅' if ok_4b else '❌'} 2023年跨8列: {len(has_2023)}")
except Exception as e:
    print(f"  {FAIL} {e}")
    traceback.print_exc()


print(f"\n{'='*80}")
print("  测试完成")
print(f"{'='*80}")
