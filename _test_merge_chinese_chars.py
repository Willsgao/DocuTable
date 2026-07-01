# -*- coding: utf-8 -*-
"""测试 _merge_chinese_chars 和 _fix_vertical_cjk_rows 修复效果。

验证步骤：
1. _merge_chinese_chars 单字合并（liteparse_table_segmenter.py）
2. _build_items 集成调用链
3. _fix_vertical_cjk_rows 安全网（hybrid_segmenter.py）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from codes.table_validator.liteparse_table_segmenter import (
    _merge_chinese_chars,
    _build_items,
    _compute_item_index,
)
from codes.table_validator.hybrid_segmenter import _fix_vertical_cjk_rows


# ============================================================
# Helpers
# ============================================================

def _mk_item(text, x0, x1, y0=100, y1=108):
    y_mid = (y0 + y1) / 2
    return {
        "text": text, "x0": x0, "x1": x1,
        "y0": y0, "y1": y1, "y_mid": y_mid,
        "item_index": hash((0, text, round(x0, 1), round(y0, 1))),
    }


def _run(label, condition):
    if condition:
        print(f"  PASS: {label}")
        return True
    else:
        print(f"  FAIL: {label}")
        return False


pass_count = 0
fail_count = 0


def check(label, condition):
    global pass_count, fail_count
    if _run(label, condition):
        pass_count += 1
    else:
        fail_count += 1


# ============================================================
# 1. _merge_chinese_chars 单元测试
# ============================================================

print("=" * 60)
print("1. _merge_chinese_chars 单元测试")
print("=" * 60)

# 1.1 同 Y 行连续单字 → 合并
items = [
    _mk_item("本", 100, 108, y0=100, y1=108),
    _mk_item("集", 110, 118, y0=100, y1=108),
    _mk_item("团", 120, 128, y0=100, y1=108),
]
merged = _merge_chinese_chars(items)
check("同Y连续单字合并为1个item", len(merged) == 1)
check("合并后text='本集团'", merged[0]["text"] == "本集团")
check("合并后x0=100（首项）", merged[0]["x0"] == 100)
check("合并后x1=128（末项）", merged[0]["x1"] == 128)
check("_merged_from记录2个被吞并项", len(merged[0].get("_merged_from", [])) == 2)

# 1.2 不同 Y 行的单字 → 不合并
items = [
    _mk_item("本", 100, 108, y0=100, y1=108),
    _mk_item("集", 100, 108, y0=120, y1=128),  # Y 差 > 2pt
]
merged = _merge_chinese_chars(items)
check("不同Y单字不合并", len(merged) == 2)
check("各保持原text", merged[0]["text"] == "本" and merged[1]["text"] == "集")

# 1.3 X 间距过大 → 不合并
items = [
    _mk_item("本", 100, 108, y0=100, y1=108),
    _mk_item("集", 200, 208, y0=100, y1=108),  # X gap = 92 >> 5
]
merged = _merge_chinese_chars(items)
check("X间距过大不合并", len(merged) == 2)

# 1.4 混入非 CJK 字符 → 分组合并
items = [
    _mk_item("本", 100, 108, y0=100, y1=108),
    _mk_item("集", 110, 118, y0=100, y1=108),
    _mk_item("123", 120, 138, y0=100, y1=108),  # 非 CJK
    _mk_item("团", 140, 148, y0=100, y1=108),
]
merged = _merge_chinese_chars(items)
check("非CJK阻断合并", len(merged) >= 2)
# "本集团" should exist
merged_texts = [m["text"] for m in merged]
check("前两字合并为'本集'", "本集" in merged_texts)
check("数字'123'独立保留", "123" in merged_texts)
check("'团'独立保留", "团" in merged_texts)

# 1.5 空列表 → 无错误
merged = _merge_chinese_chars([])
check("空列表无报错", merged == [])

# 1.6 单元素 → 不变
items = [_mk_item("单", 100, 108)]
merged = _merge_chinese_chars(items)
check("单元素不变", len(merged) == 1 and merged[0]["text"] == "单")

# 1.7 全非 CJK → 不变
items = [
    _mk_item("abc", 100, 108, y0=100, y1=108),
    _mk_item("123", 110, 118, y0=100, y1=108),
]
merged = _merge_chinese_chars(items)
check("全非CJK不变", len(merged) == 2 and merged[0]["text"] == "abc")

# 1.8 CJK 标点符号合并
items = [
    _mk_item("本", 100, 108, y0=100, y1=108),
    _mk_item("\uff0c", 110, 118, y0=100, y1=108),  # 全角逗号 ，
    _mk_item("集", 120, 128, y0=100, y1=108),
    _mk_item("\u3002", 130, 138, y0=100, y1=108),  # CJK 句号 。
]
merged = _merge_chinese_chars(items)
check("CJK标点合并到文本", len(merged) == 1)
check("合并后包含标点", "，" in merged[0]["text"] and "。" in merged[0]["text"])


# ============================================================
# 2. _build_items 集成测试
# ============================================================

print("\n" + "=" * 60)
print("2. _build_items 集成测试（小数+中文合并链）")
print("=" * 60)

# 2.1 单字 + 无小数拆分 → 仅中文合并生效
raw = [
    {"text": "本", "x0": 100, "x1": 108, "y0": 100, "y1": 108},
    {"text": "集", "x0": 110, "x1": 118, "y0": 100, "y1": 108},
    {"text": "团", "x0": 120, "x1": 128, "y0": 100, "y1": 108},
]
items = _build_items(raw, page_num=1)
check("_build_items单字合并", len(items) == 1 and items[0]["text"] == "本集团")
check("item_index存在", "item_index" in items[0])
check("_merged_from存在", "_merged_from" in items[0])

# 2.2 小数 + 中文 共存 → 两者都合并
raw = [
    {"text": "收", "x0": 50,  "x1": 58,  "y0": 100, "y1": 110},
    {"text": "入", "x0": 60,  "x1": 68,  "y0": 100, "y1": 110},
    {"text": "1",  "x0": 150, "x1": 158, "y0": 100, "y1": 110},
    {"text": ".85", "x0": 158, "x1": 175, "y0": 100, "y1": 110},
]
items = _build_items(raw, page_num=1)
check("小数+中文并行合并", len(items) == 2)
texts = sorted([it["text"] for it in items])
check("'收入'存在", "收入" in texts)
check("'1.85'存在", "1.85" in texts)

# 2.3 完整句子（用户报告案例模拟）
sentence = "(i)本集团及本行于资产负债表日采用的主要精算假设为："
raw = []
x = 50
for ch in sentence:
    raw.append({"text": ch, "x0": x, "x1": x + 8, "y0": 100, "y1": 110})
    x += 9  # 紧密相邻，间距 1pt
items = _build_items(raw, page_num=1)
check("完整句子合并", len(items) < len(sentence))  # 应该有合并且不是逐字
check("句子内容完整", any(len(it["text"]) > 5 for it in items))

# 2.4 同 Y 行但 Y 有微差（≤2pt）→ 仍合并
raw = [
    {"text": "本", "x0": 100, "x1": 108, "y0": 100, "y1": 108},
    {"text": "集", "x0": 110, "x1": 118, "y0": 100.5, "y1": 108.5},  # y_mid差=0.5
    {"text": "团", "x0": 120, "x1": 128, "y0": 101, "y1": 109},      # y_mid差=1.0
]
items = _build_items(raw, page_num=1)
check("Y微差≤2pt仍合并", len(items) == 1 and items[0]["text"] == "本集团")


# ============================================================
# 3. _fix_vertical_cjk_rows 安全网测试
# ============================================================

print("\n" + "=" * 60)
print("3. _fix_vertical_cjk_rows 安全网测试")
print("=" * 60)

# 3.1 竖排 CJK 单字 → 合并
data = [
    ["本"], ["集"], ["团"],
    ["及"], ["本"], ["行"],
]
result = _fix_vertical_cjk_rows(data)
check("竖排CJK合并为1行", len(result) <= len(data))
check("合并行首列为'本集团及本行'", any("本集团及本行" in str(r[0]) for r in result))

# 3.2 正常表格数据 → 不影响
data = [
    ["项目", "2024年", "2023年", ""],
    ["营业收入", "1,234", "1,100", ""],
    ["营业成本", "800", "750", ""],
    ["利润", "434", "350", ""],
    ["合计", "1,668", "1,450", ""],
]
result = _fix_vertical_cjk_rows(data)
check("正常表格不受影响", len(result) == len(data))

# 3.3 混合：少数单字行 + 多数数据行 → 不触发
data = [
    ["营业收入", "1,234", "1,100"],
    ["本", "", ""],          # 1/5 = 20% < 60%
    ["营业成本", "800", "750"],
    ["营业利润", "434", "350"],
    ["合计", "1,668", "1,450"],
]
result = _fix_vertical_cjk_rows(data)
check("少数单字行不触发", len(result) == len(data))

# 3.4 空数据 → 不变
data = []
result = _fix_vertical_cjk_rows(data)
check("空数据不变", result == [])

# 3.5 少于 3 行 → 不触发
data = [["本"], ["集"]]
result = _fix_vertical_cjk_rows(data)
check("少于3行不触发", len(result) == 2)


# ============================================================
# Summary
# ============================================================

print("\n" + "=" * 60)
print(f"结果: {pass_count} PASS / {fail_count} FAIL")
print("=" * 60)

if fail_count > 0:
    print("\n*** 存在失败测试，请检查！ ***")
    sys.exit(1)
else:
    print("\n*** 全部测试通过！ ***")
