"""CC1 资本构成表 Page 10 — 验证通用结构检测。"""
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
    ["3 资本和总损失吸收能力的构成"],
    ["3.1 资本工具和合格外部总损失吸收能力非资本债务工具的主要特征"],
    ["遵照国家金融监督管理总局…在本行官网单独披露…"],
    ["3.2 资本构成"],
    ["下表列示本集团资本构成及与监管并表下的资产负债表的对应关系等。"],
    ["表 3 (CC1)：资本构成"],
]

CC1_TABLE = [
    ["", "a", "", "b"],
    ["（人民币百万元，百分比除外）", "数额", "", "代码"],
    ["", "2024年12月31日", "", ""],
    ["核心一级资本", "", "", ""],
    ["1", "实收资本和资本公积可计入部分", "385,621", "e+g"],
    ["2", "留存收益", "2,718,849", ""],
    ["2a", "盈余公积", "402,196", "h"],
    ["2b", "一般风险准备", "534,151", "i"],
    ["2c", "未分配利润", "1,782,502", "j"],
    ["3", "累计其他综合收益", "65,136", ""],
    ["4", "少数股东资本可计入部分", "3,703", ""],
    ["5", "扣除前的核心一级资本", "3,173,309", ""],
    ["核心一级资本：扣除项", "", "", ""],
    ["6", "审慎估值调整", "-", ""],
    ["7", "商誉（扣除递延税负债）", "2,170", "a-c"],
    ["8", "其他无形资产（土地使用权除外）（扣除递延税负债）", "5,009", "b-d"],
    ["13", "自身信用风险变化导致其负债公允价值变化带来的未实现", "-", ""],
    ["", "损益", "", ""],
    ["20", "其他依赖于银行未来盈利的净递延税资产的未扣除部分超过核心一级资本 15%的应扣除金额", "-", ""],
    ["21", "其中：对金融机构大额少数资本投资中应扣除的金额", "-", ""],
]

DATA = NARRATIVE + CC1_TABLE
body_start = find_pillar_table_body_start_row(DATA)
print(f"  body_start={body_start}")
check("body start before table rows", body_start == len(NARRATIVE), f"got {body_start}")
body = DATA[body_start:]
check("CC1 body detected", is_pillar_disclosure_table_body(body), f"body rows={len(body)}")

table = {"type": "table", "page": 10, "data": copy.deepcopy(DATA), "y0": 331, "y1": 738}
parts = split_mixed_table_entry(table)
texts = [p for p in parts if p.get("type") == "text"]
tables = [p for p in parts if p.get("type") == "table"]
check("one narrative", len(texts) == 1, f"n={len(texts)}")
check("one table", len(tables) == 1, f"n={len(tables)}")
if texts:
    t = texts[0].get("data", "")
    check("has CC1 caption", "CC1" in t)
    check("has 3.2", "3.2" in t)
if tables:
    flat = " ".join(str(c) for row in tables[0]["data"] for c in row)
    check("row 1 amount", "385,621" in flat)
    check("row 5 total", "3,173,309" in flat)
    check("section 核心一级资本", "核心一级资本" in flat)
    check("no 3.1 narrative in table", "国家金融监督管理总局" not in flat)
    row13 = next(
        (r for r in tables[0]["data"] if str(r[0]).strip() == "13"),
        None,
    )
    check(
        "wrapped row 13 label merged",
        row13 is not None and "损益" in str(row13[1]),
        f"row13={row13}",
    )

print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
