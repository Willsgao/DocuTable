"""专项测试：_strip_footnote_rows_from_data — 数据列对齐检测"""

import sys
sys.path.insert(0, ".")

from codes.table_validator.hybrid_segmenter import (
    _strip_footnote_rows_from_data,
    _build_numeric_column_profile,
    _is_data_aligned,
)

PASS, FAIL = 0, 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f"  ({detail})" if detail else ""))


# ================================================================
# Test Set 1: _build_numeric_column_profile 数值列画像
# ================================================================
print("\n── 数值列画像 ──")
data1 = [
    ["现金、存款及同业存单", "1,008,220", "60.80", "20,512", "34.60"],
    ["债券", "440,983", "26.60", "5,052", "8.52"],
    ["总额", "1,658,154", "100.00", "59,285", "100.00"],
]
profile = _build_numeric_column_profile(data1)
check("列0(文本列) → False", profile[0] is False, f"got {profile[0]}")
check("列1(数值列) → True", profile[1] is True, f"got {profile[1]}")
check("列2(数值列) → True", profile[2] is True, f"got {profile[2]}")
check("列3(数值列) → True", profile[3] is True, f"got {profile[3]}")

# ── 混合列：50% 数值 → False（<40% 阈值） ──
data_mixed = [
    ["a", "100", "text", "200"],
    ["b", "text", "", "text"],
]
profile_mixed = _build_numeric_column_profile(data_mixed)
check("混合例 25%数值列 → False", profile_mixed[0] is False, f"got {profile_mixed[0]}")
check("混合例 50%数值列 → True", profile_mixed[1] is True, f"got {profile_mixed[1]}")


# ================================================================
# Test Set 2: _is_data_aligned 数据列对齐
# ================================================================
print("\n── 数据列对齐 ──")
num_cols = [False, True, True, True, True, True, True]  # 7列, 列1~6为数值列

# 正常数据行
row_data = ["现金、存款及同业存单", "1,008,220", "60.80", "20,512", "34.60", "1,028,732", "59.90"]
check("数据行 → 对齐", _is_data_aligned(row_data, num_cols) is True)

# 有空和占位符的行
row_dash = ["其他类资产^1", "201,987", "12.18", "－", "－", "201,987", "11.76"]
check("含'－'行 → 对齐", _is_data_aligned(row_dash, num_cols) is True)

# 用户报告的脚注行
row_fn = ["1.", "包括公募基金、代客境外理财投资QDII、金融衍生品、拆放同业及债券买入返售。", "", "", "", "", ""]
check("用户bug脚注行 → 不对齐", _is_data_aligned(row_fn, num_cols) is False)

# 全空行
row_empty = ["", "", "", "", "", "", ""]
check("全空行 → 对齐（空=允许）", _is_data_aligned(row_empty, num_cols) is True)

# 注行
row_note = ["注：", "数据截止2024年12月31日。", "", "", "", "", ""]
check("注行 → 不对齐", _is_data_aligned(row_note, num_cols) is False)


# ================================================================
# Test Set 3: _strip_footnote_rows_from_data 端到端
# ================================================================
print("\n── 端到端剥离 ──")

# Case 1: 用户报告的真实 case（简化版）
data_user = [
    ["现金、存款及同业存单", "1,008,220", "60.80", "20,512", "34.60", "1,028,732", "59.90"],
    ["债券", "440,983", "26.60", "5,052", "8.52", "446,035", "25.97"],
    ["权益类资产", "1,793", "0.11", "25,679", "43.31", "27,472", "1.60"],
    ["非标准化债权类资产", "5,171", "0.31", "8,042", "13.57", "13,213", "0.77"],
    ["其他类资产^1", "201,987", "12.18", "－", "－", "201,987", "11.76"],
    ["总额", "1,658,154", "100.00", "59,285", "100.00", "1,717,439", "100.00"],
    ["1.", "包括公募基金、代客境外理财投资QDII、金融衍生品、拆放同业及债券买入返售。", "", "", "", "", ""],
]
cleaned, notes = _strip_footnote_rows_from_data(data_user, 7)
check("用户case: 脚注行被剥离", len(cleaned) == 6, f"got {len(cleaned)} rows")
check("用户case: notes非空(列表)", len(notes) == 1 and len(notes[0]) > 20, f"notes={notes}")
check("用户case: 其他类资产^1保留", any("其他类资产" in str(c) for r in cleaned for c in r))

# Case 1b: 脚注整体落在列0（单cell）—— liteparse 中文本跨行的场景
data_user_single_col = [
    ["现金、存款及同业存单", "1,008,220", "60.80", "20,512", "34.60", "1,028,732", "59.90"],
    ["债券", "440,983", "26.60", "5,052", "8.52", "446,035", "25.97"],
    ["权益类资产", "1,793", "0.11", "25,679", "43.31", "27,472", "1.60"],
    ["非标准化债权类资产", "5,171", "0.31", "8,042", "13.57", "13,213", "0.77"],
    ["其他类资产^1", "201,987", "12.18", "－", "－", "201,987", "11.76"],
    ["总额", "1,658,154", "100.00", "59,285", "100.00", "1,717,439", "100.00"],
    ["1. 包括公募基金、代客境外理财投资QDII、金融衍生品、拆放同业及债券买入返售。", "", "", "", "", "", ""],
]
cleaned1b, notes1b = _strip_footnote_rows_from_data(data_user_single_col, 7)
check("单列脚注: 脚注行被剥离", len(cleaned1b) == 6, f"got {len(cleaned1b)} rows")
check("单列脚注: notes非空", len(notes1b) == 1 and len(notes1b[0]) > 20, f"notes={notes1b}")
check("单列脚注: 其他类资产^1保留", any("其他类资产" in str(c) for r in cleaned1b for c in r))

# Case 2: 多行连续脚注
data_multi = [
    ["营业收入", "100", "200", "300"],
    ["营业成本", "80", "160", "240"],
    ["净利润", "20", "40", "60"],
    ["1.", "包含子公司数据。", "", ""],
    ["2.", "数据已审计。", "", ""],
]
cleaned2, notes2 = _strip_footnote_rows_from_data(data_multi, 4)
check("多行脚注: 剥离2行", len(cleaned2) == 3, f"got {len(cleaned2)}")
check("多行脚注: notes含两行",
      len(notes2) == 2 and any("1." in n for n in notes2) and any("2." in n for n in notes2),
      f"notes={notes2}")

# Case 3: 排名表不误杀
data_rank = [
    ["排名", "名称", "金额", "占比"],
    ["1.", "工商银行", "500", "25%"],
    ["2.", "建设银行", "400", "20%"],
    ["3.", "农业银行", "300", "15%"],
]
cleaned3, notes3 = _strip_footnote_rows_from_data(data_rank, 4)
check("排名表: 排名行不剥离", len(cleaned3) == 4, f"got {len(cleaned3)}")

# Case 4: 标注行（注：）剥离
data_note = [
    ["科目", "金额", "占比"],
    ["资产", "1000", "100%"],
    ["负债", "600", "60%"],
    ["注：", "数据截止2024年。", "", ""],
]
cleaned4, notes4 = _strip_footnote_rows_from_data(data_note, 4)
check("注行: 被剥离", len(cleaned4) == 3, f"got {len(cleaned4)}")

# Case 5: 小表跳过（col_count < 3 但数值列够多）
data_small = [
    ["A", "100", "200"],
    ["B", "300", "400"],
    ["1.", "备注文本", ""],
]
cleaned5, notes5 = _strip_footnote_rows_from_data(data_small, 3)
check("3列表: 脚注剥离", len(cleaned5) == 2, f"got {len(cleaned5)}")

# Case 6: * 开头的脚注
data_star = [
    ["科目", "2024", "2023"],
    ["收入", "100", "90"],
    ["*", "经审计数据。", ""],
]
cleaned6, notes6 = _strip_footnote_rows_from_data(data_star, 3)
check("星号脚注: 剥离", len(cleaned6) == 2, f"got {len(cleaned6)}")

# Case 7: 来源行
data_src = [
    ["项目", "金额"],
    ["收入", "1000"],
    ["支出", "800"],
    ["来源：", "公司年报", ""],
]
cleaned7, notes7 = _strip_footnote_rows_from_data(data_src, 3)
check("来源行: 剥离", len(cleaned7) == 3, f"got {len(cleaned7)}")

# Case 8: 底行"1."后有数值 → 不剥离（类似排名）
data_num = [
    ["名称", "金额", "增长率"],
    ["1.", "500", "10%"],
    ["2.", "300", "8%"],
]
cleaned8, notes8 = _strip_footnote_rows_from_data(data_num, 3)
check("编号+数值行: 不剥离", len(cleaned8) == 3, f"got {len(cleaned8)}")

# Case 9: 只有2行数据 + 脚注（<3行不扫描）
data_tiny = [
    ["项目", "金额"],
    ["1.", "备注内容"],
]
cleaned9, notes9 = _strip_footnote_rows_from_data(data_tiny, 2)
check("2行数据: 不扫描", len(cleaned9) == 2, f"got {len(cleaned9)}")

# Case 10: 列数不一致的行
data_mismatch = [
    ["A", "100", "200"],
    ["B", "300", "400"],
    ["1.", "备注"],  # 只有2列，但表格是3列
]
cleaned10, notes10 = _strip_footnote_rows_from_data(data_mismatch, 3)
check("列数不一致: 停止扫描, 不剥离", len(cleaned10) == 3, f"got {len(cleaned10)}")


# ================================================================
# Summary
# ================================================================
total = PASS + FAIL
print(f"\n{'='*60}")
print(f"结果: {PASS}/{total} PASS" + (f", {FAIL} FAIL" if FAIL else "  ALL PASS"))
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
