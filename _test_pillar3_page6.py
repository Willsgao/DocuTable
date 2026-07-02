"""第三支柱监管指标表（Page 6 类型）— 禁止错误拆分。"""
import copy
import sys

from codes.table_validator.table_content_splitter import (
    is_regulatory_metrics_summary_table,
    split_mixed_table_entry,
    split_mixed_table_entries,
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


P6_DATA = [
    ["", "a", "b", "c", "d"],
    ["（人民币百万元，百分比除外）", "2024 年", "2024 年", "2024 年", "2024 年"],
    ["", "12 月 31 日", "9 月 30 日", "6 月 30 日", "3 月 31 日"],
    ["13", "调整后表内外资产余额", "42,755,544", "42,815,730", "42,314,726", "41,837,451"],
    ["14", "杠杆率（%）", "7.78", "7.76", "7.65", "7.76"],
    ["14a", "杠杆率 a（%）1", "7.78", "7.76", "", "7.76"],
    ["14b", "杠杆率 b（%）2", "7.69", "7.75", "7.65", "7.73"],
    ["14c", "杠杆率 c（%）3", "7.69", "7.75", "7.65", "7.73"],
    ["流动性覆盖率 4", "", "", "", ""],
    ["15", "合格优质流动性资产", "6,237,408", "6,148,940", "6,115,852", "6,059,382"],
    ["16", "现金净流出量", "4,957,733", "5,119,129", "4,877,791", "4,510,003"],
    ["17", "流动性覆盖率（%）", "125.73", "120.29", "125.43", "134.46"],
    ["净稳定资金比例", "", "", "", ""],
    ["18", "可用稳定资金合计", "28,158,322", "28,350,638", "28,236,945", "28,350,972"],
    ["19", "所需稳定资金合计", "21,027,700", "20,928,125", "20,917,739", "22,174,688"],
    ["20", "净稳定资金比例（%）", "133.91", "135.47", "134.99", "127.85"],
    [
        "1．杠杆率a指不考虑临时豁免存款准备金、采用证券融资交易季末余额计算的杠杆率。"
        "详细信息见“13.杠杆率”章节。",
        "", "", "", "",
    ],
    [
        "2．杠杆率b指考虑临时豁免存款准备金、采用最近一个季度内证券融资交易每日余额的简单算数平均值"
        "计算的杠杆率。详细信息见“13.杠杆率”章节。",
        "", "", "", "",
    ],
    [
        "3．杠杆率c指不考虑临时豁免存款准备金、采用最近一个季度内证券融资交易每日余额的简单算数平均"
        "值计算的杠杆率。详细信息见“13.杠杆率”章节。",
        "", "", "", "",
    ],
    [
        "4．流动性覆盖率数据均为最近一个季度内每个自然日数值的简单算数平均值。"
        "详细信息见“14.流动性风险”章节。",
        "", "", "", "",
    ],
]

check("detect pillar III table", is_regulatory_metrics_summary_table(P6_DATA))

table = {
    "type": "table",
    "page": 6,
    "data": copy.deepcopy(P6_DATA),
    "y0": 77,
    "y1": 303,
}

split = split_mixed_table_entry(table)
check("split_mixed yields single table", len(split) == 1, f"n={len(split)}")
if split:
    d = split[0].get("data", [])
    flat = " ".join(str(c) for row in d for c in row)
    check("row 13 kept in table", "42,755,544" in flat)
    check("row 20 kept in table", "133.91" in flat)
    check("section 流动性覆盖率 in table", "流动性覆盖率" in flat)
    check("section 净稳定资金比例 in table", "净稳定资金比例" in flat)
    check("footnotes stripped from body", bool(split[0].get("_footnote_records")) or "杠杆率a指" not in flat)

struct = _split_fused_table_by_structure([copy.deepcopy(table)], None)
check("structure split keeps one table", len(struct) == 1, f"n={len(struct)}")

print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
