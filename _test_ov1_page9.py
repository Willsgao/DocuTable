"""OV1 风险加权资产表 Page 9 — 通用结构，无关键词白名单。"""
import copy
import sys

from codes.table_validator.table_content_splitter import (
    find_pillar_table_body_start_row,
    is_pillar_disclosure_table_body,
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
    ["2.2.2 风险加权资产概览"],
    ["下表列示本集团风险加权资产和资本要求。"],
    ["表 2 (OV1)：风险加权资产概况"],
]

OV1_TABLE = [
    ["（人民币百万元）", "风险加权资产", "", "最低资本要求"],
    ["", "2024 年", "2024 年", "2024 年"],
    ["", "12 月 31 日", "9 月 30 日", "12 月 31 日"],
    ["1", "信用风险", "19,814,943", "20,185,885", "1,585,195"],
    ["2", "信用风险（不包括交易对手信用风险…）", "19,433,391", "19,818,263", "1,554,670"],
    ["3", "其中：权重法", "5,820,738", "5,596,995", "465,658"],
    ["22", "市场风险", "", "", ""],
    ["23", "其中：标准法", "250,577", "194,481", "20,046"],
    ["27", "操作风险", "1,744,419", "1,770,189", "139,554"],
    ["29", "合计", "21,854,590", "22,150,555", "1,748,367"],
    [
        "1．除项目19、20、21外，本集团银行账簿资产证券化信用风险加权资产还包括"
        "“适用1250%风险权重”的681.92亿元。",
        "", "", "",
    ],
]

DATA = NARRATIVE + OV1_TABLE
body_start = find_pillar_table_body_start_row(DATA)
check("body starts at unit row", body_start == len(NARRATIVE), f"got {body_start}")
check("OV1 body detected structurally", is_pillar_disclosure_table_body(DATA[body_start:]))

table = {"type": "table", "page": 9, "data": copy.deepcopy(DATA), "y0": 172, "y1": 650}
parts = split_mixed_table_entry(table)
texts = [p for p in parts if p.get("type") == "text"]
tables = [p for p in parts if p.get("type") == "table"]
check("one narrative block", len(texts) == 1, f"n={len(texts)}")
check("one table", len(tables) == 1, f"n={len(tables)}")
if texts:
    t = texts[0].get("data", "")
    check("narrative has OV1 caption", "OV1" in t)
    check("narrative has 2.2.2", "2.2.2" in t)
if tables:
    flat = " ".join(str(c) for row in tables[0]["data"] for c in row)
    check("row 1 credit risk", "19,814,943" in flat)
    check("row 29 total", "21,854,590" in flat)
    check("section 市场风险 in table", "市场风险" in flat)
    check("no narrative in table body", "下表列示" not in flat)

print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
