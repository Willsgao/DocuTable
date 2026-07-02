"""KM1 监管表 Page 5 类型 — 说明 + 单表，禁止表体碎裂。"""
import copy
import sys

from codes.table_validator.table_content_splitter import (
    find_regulatory_table_body_start_row,
    is_regulatory_metrics_summary_table,
    split_mixed_table_entry,
)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


NARRATIVE = [
    ["2 风险管理、关键审慎监管指标和风险加权资产概览"],
    ["2.1 关键审慎监管指标概览"],
    ["根据监管要求，本行须按照《商业银行资本管理办法》计量和披露资本充足率。"],
    ["表 1 (KM1)：监管并表关键审慎监管指标"],
]

KM1_TABLE = [
    ["", "a", "b", "c", "d"],
    ["（人民币百万元，百分比除外）", "2024 年", "2024 年", "2024 年", "2024 年"],
    ["", "12 月 31 日", "9 月 30 日", "6 月 30 日", "3 月 31 日"],
    ["可用资本（数额）", "", "", "", ""],
    ["1", "核心一级资本净额", "3,165,549", "3,124,043", "3,038,387", "3,045,754"],
    ["2", "一级资本净额", "3,324,424", "3,322,954", "3,237,254", "3,245,824"],
    ["3", "资本净额", "4,303,263", "4,285,564", "4,175,087", "4,175,290"],
    ["风险加权资产（数额）", "", "", "", ""],
    ["4", "风险加权资产合计", "21,854,590", "22,150,555", "21,690,492", "21,586,165"],
    ["4a", "风险加权资产合计（应用资本", "21,854,590", "22,150,555", "21,690,492", "21,586,165"],
    ["", "底线前）", "", "", ""],
    ["资本充足率", "", "", "", ""],
    ["5", "核心一级资本充足率（%）", "14.48", "14.10", "14.01", "14.11"],
    ["8", "储备资本要求（%）", "2.50", "2.50", "2.50", "2.50"],
    ["12", "满足最低资本要求后的可用核", "9.21", "9.00", "8.92", "9.04"],
    ["", "心一级资本净额占风险加权资", "", "", ""],
    ["", "产的比例（%）", "", "", ""],
    ["杠杆率", "", "", "", ""],
]

DATA = NARRATIVE + KM1_TABLE
body_start = find_regulatory_table_body_start_row(DATA)
check("find body at abcd header", body_start == len(NARRATIVE), f"got {body_start}")
check("table body is KM1", is_regulatory_metrics_summary_table(DATA[body_start:]))

table = {"type": "table", "page": 5, "data": copy.deepcopy(DATA), "y0": 297, "y1": 730}
parts = split_mixed_table_entry(table)
texts = [p for p in parts if p.get("type") == "text"]
tables = [p for p in parts if p.get("type") == "table"]
check("one narrative text", len(texts) == 1, f"n={len(texts)}")
check("one KM1 table", len(tables) == 1, f"n={len(tables)}")
if texts:
    check("narrative has 2.1", "2.1" in texts[0].get("data", ""))
    check("narrative has KM1 caption", "KM1" in texts[0].get("data", ""))
if tables:
    flat = " ".join(str(c) for row in tables[0]["data"] for c in row)
    check("table has row 1 capital", "3,165,549" in flat)
    check("table has row 4 rwa", "21,854,590" in flat)
    check("table has section 可用资本", "可用资本" in flat)
    check("table has section 资本充足率", "资本充足率" in flat)
    check("no narrative in table", "商业银行资本管理办法" not in flat)
    check("text seq before table", texts[0].get("_segment_seq", 0) < tables[0].get("_segment_seq", 1))

print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
