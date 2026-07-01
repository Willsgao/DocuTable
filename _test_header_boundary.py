# -*- coding: utf-8 -*-
"""表头/表尾边界判定 + 间隙日期表头挂载测试"""
import sys
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from codes.table_validator.header_boundary import (
    is_date_only_header_block,
    is_date_only_header_row_items,
    is_table_tail_annotation_row,
    strip_tail_annotation_rows_from_data,
    row_body_mismatch_with_fingerprint,
    compute_body_fingerprint,
)
from codes.table_validator.hybrid_segmenter import (
    _is_potential_missed_table,
    _classify_gap_text,
    hybrid_segment_tables,
    _cluster_text_items_into_blocks,
)
from codes.liteparse_extractor.parser import LiteParseParser

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


print("\n=== 1. 日期表头块识别 ===")
year_block = {
    "text_items": [
        {"text": "2024年", "x0": 427, "x1": 453, "y0": 460, "y1": 461},
        {"text": "2023年", "x0": 494, "x1": 519, "y0": 460, "y1": 461},
        {"text": "12月31日", "x0": 419, "x1": 453, "y0": 471, "y1": 472},
        {"text": "12月31日", "x0": 486, "x1": 519, "y0": 471, "y1": 472},
    ],
    "full_text": "2024年 2023年\n12月31日 12月31日",
    "num_ratio": 0.1,
    "col_count": 2,
}
check("year+date block is date-only header", is_date_only_header_block(year_block))
check("not missed table", not _is_potential_missed_table(year_block))

print("\n=== 1b. 多级表头：不补数据、不删空白 ===")
from codes.table_validator.table_content_splitter import normalize_table_header_columns
misaligned = [
    ["2024年", "2023年", "", ""],
    ["12月31日", "12月31日", "12月31日", "12月31日"],
    ["折现率", "1.75%", "2.50%", ""],
    ["医疗费用年增长率", "7.00%", "7.00%", ""],
]
fixed = normalize_table_header_columns(misaligned)
check("column width preserved", len(fixed[1]) == 4, str(fixed[1]))
check("year row not shifted", fixed[0] == ["2024年", "2023年", "", ""], str(fixed[0]))
check("spurious month-day cleared only", fixed[1] == ["", "12月31日", "12月31日", ""], str(fixed[1]))
check("data row untouched", fixed[2] == ["折现率", "1.75%", "2.50%", ""], str(fixed[2]))

data_block = {
    "text_items": [
        {"text": "1.75%", "x0": 430, "x1": 453, "y0": 486, "y1": 487},
        {"text": "2.50%", "x0": 499, "x1": 519, "y0": 486, "y1": 487},
        {"text": "7.00%", "x0": 430, "x1": 453, "y0": 498, "y1": 499},
        {"text": "7.00%", "x0": 499, "x1": 519, "y0": 498, "y1": 499},
    ],
    "full_text": "1.75% 2.50%\n7.00% 7.00%",
    "num_ratio": 0.8,
    "col_count": 2,
}
check("numeric block is not date-only", not is_date_only_header_block(data_block))

print("\n=== 2. gap 分类：日期表头 → 挂到 next ===")
next_b = {"y0": 486, "y1": 649, "page": 6}
target, field = _classify_gap_text(year_block, None, next_b, median_row_h=12.0)
check("classify as pre_header", target == "next" and field == "_pre_header", f"got {target},{field}")

print("\n=== 3. 表尾列指纹偏离 ===")
body_items = [
    {"text": "折现率", "x0": 139, "x1": 163, "y0": 486, "y1": 487, "y_mid": 486.5},
    {"text": "1.75%", "x0": 430, "x1": 453, "y0": 486, "y1": 487, "y_mid": 486.5},
    {"text": "2.50%", "x0": 499, "x1": 519, "y0": 486, "y1": 487, "y_mid": 486.5},
    {"text": "医疗费用年增长率", "x0": 139, "x1": 204, "y0": 498, "y1": 499, "y_mid": 498.5},
    {"text": "7.00%", "x0": 430, "x1": 453, "y0": 498, "y1": 499, "y_mid": 498.5},
    {"text": "7.00%", "x0": 499, "x1": 519, "y0": 498, "y1": 499, "y_mid": 498.5},
]
fp = compute_body_fingerprint(body_items)
sensitivity_row = [
    {"text": "(84)", "x0": 200, "x1": 220, "y0": 640, "y1": 641, "y_mid": 640.5},
    {"text": "87", "x0": 280, "x1": 300, "y0": 640, "y1": 641, "y_mid": 640.5},
    {"text": "折现率", "x0": 139, "x1": 163, "y0": 640, "y1": 641, "y_mid": 640.5},
]
footnote_row = [
    {"text": "死亡率的假设是基于中国人寿保险业经验生命表", "x0": 50, "x1": 500, "y0": 534, "y1": 545, "y_mid": 540},
]
check("footnote row mismatch", row_body_mismatch_with_fingerprint(footnote_row, fp))

print("\n=== 3b. 表尾注释行剥离 ===")
pension_tail = ["利息成本于其他业务及管理费中确认。", "", "", "", "", "", ""]
pension_data = [
    ["", "2024年", "2023年", "2024年", "2023年", "2024年", "2023年"],
    ["年末余额", "4,271", "4,343", "4,766", "4,917", "(495)", "(574)"],
    pension_tail,
]
check("tail row is annotation", is_table_tail_annotation_row(pension_tail, 7))
check("year row not annotation", not is_table_tail_annotation_row(pension_data[0], 7))
check("balance row not annotation", not is_table_tail_annotation_row(pension_data[1], 7))
section_row = ["计入当期损益的设定受益成本", "", "", "", "", "", ""]
check("section header not annotation", not is_table_tail_annotation_row(section_row, 7))
cleaned, notes = strip_tail_annotation_rows_from_data(pension_data, col_count=7)
check("footnote stripped", "利息成本" not in " ".join(str(c) for r in cleaned for c in r))
check("footnote in notes", any("利息成本" in n for n in notes), str(notes))
check("年末余额 kept", len(cleaned) == 2)

print("\n=== 3c. 全空列删除 + 互补列合并 ===")
from codes.table_validator.header_boundary import compact_table_spacer_rows_and_columns
equity = [
    ["", "", "2024年", "", "2023年"],
    ["（人民币百万元）", "", "12月31日", "", "12月31日"],
    ["股本", "", "250,011", "", "250,011"],
    ["优先股", "", "", "59,977", "59,977"],
    ["其他综合收益", "", "", "57,901", "23,981"],
]
compacted = compact_table_spacer_rows_and_columns(equity)
check("removed spacer col", max(len(r) for r in compacted) == 3, str([len(r) for r in compacted]))
check("2024/2023 headers", compacted[0] == ["", "2024年", "2023年"], str(compacted[0]))
check("month-day headers", compacted[1] == ["（人民币百万元）", "12月31日", "12月31日"], str(compacted[1]))
check("OCI 2024 merged", compacted[4][1] == "57,901", str(compacted[4]))

print("\n=== 3d. 顺序不变：2024 在 2023 左，行序不乱 ===")
from codes.table_validator.header_boundary import _pair_has_distinct_year_headers
check("2024 col index < 2023", compacted[0].index("2024年") < compacted[0].index("2023年"))
labels = [r[0] for r in compacted[2:] if r and r[0]]
check("row order preserved", labels == ["股本", "优先股", "其他综合收益"], str(labels))
check("block 2024|2023 merge", _pair_has_distinct_year_headers(
    [["", "2024年", "2023年"]], 1, 3
))

print("\n=== 4. 端到端 page6 精算假设表 ===")
parser = LiteParseParser()
lp = parser.parse("data/input_pdfs/test_subset8.pdf", target_pages=[6]).to_dict()
tables, _ = hybrid_segment_tables(lp, docx_tables=[])
p6_tables = [t for t in tables if t.get("page") == 6 and t.get("type") != "text"]
p6_texts = [t for t in tables if t.get("page") == 6 and t.get("type") == "text"]

actuarial = None
sensitivity = None
for t in p6_tables:
    flat = " ".join(str(c) for row in t.get("data", []) for c in row)
    if "折现率" in flat and "1.75%" in flat and "敏感性" not in flat:
        actuarial = t
    if "(84)" in flat or ("提高0.25%" in flat and "精算假设" in flat):
        sensitivity = t

check("found actuarial table", actuarial is not None)
if actuarial:
    data = actuarial.get("data", [])
    flat = "\n".join(" | ".join(str(c) for c in row) for row in data)
    check("header 2024年 in main table", "2024" in flat)
    check("year 2024 appears once", flat.count("2024年") == 1, f"count={flat.count('2024年')}")
    check("year 2023 appears once", flat.count("2023年") == 1, f"count={flat.count('2023年')}")
    check("year header not duplicated 4x", flat.count("2024年") <= 2, f"count={flat.count('2024年')}")
    check("no footnote in main table", "死亡率" not in flat, flat[:80])
    check("no sensitivity in main table", "敏感性" not in flat and "(84)" not in flat)
    check("main table rows <= 7", len(data) <= 7, f"rows={len(data)}")

footnote_text = " ".join(
    t.get("context_text", t.get("data", "")) for t in p6_texts
)
check("footnote as text", "死亡率" in footnote_text, footnote_text[:60])

check("sensitivity as separate table", sensitivity is not None)
if sensitivity:
    sflat = " ".join(str(c) for row in sensitivity.get("data", []) for c in row)
    check("sensitivity has (84)", "(84)" in sflat or "84" in sflat)

pension = None
for t in p6_tables:
    flat = " ".join(str(c) for row in t.get("data", []) for c in row)
    if "年初余额" in flat and "设定受益计划义务现值" in flat:
        pension = t
check("found pension table", pension is not None)
if pension:
    pdata = pension.get("data", [])
    pflat = " ".join(str(c) for row in pdata for c in row)
    check("no interest footnote in pension table", "利息成本于" not in pflat, pflat[-40:])
    check("年末余额 in pension table", "年末余额" in pflat)
    interest_text = " ".join(
        t.get("context_text", t.get("data", "")) for t in p6_texts
    )
    check("interest footnote as text", "利息成本于" in interest_text, interest_text[-60:])

print("\n=== 5. page2 脚注原位置唯一（不重复复制） ===")
lp2 = parser.parse("data/input_pdfs/test_subset8.pdf", target_pages=[2]).to_dict()
entries2, _ = hybrid_segment_tables(lp2, docx_tables=[])
needle2 = "包括公募基金"
fn_hits = []
for e in entries2:
    if e.get("page") != 2:
        continue
    if e.get("type") == "text" and needle2 in str(e.get("context_text", "")):
        fn_hits.append(e)
    elif e.get("type") == "table":
        if needle2 in str(e.get("notes", "")):
            fn_hits.append(("notes", e))
        for row in e.get("data", []):
            for c in row:
                if needle2 in str(c):
                    fn_hits.append(("cell", e))
check("page2 footnote appears once", len(fn_hits) == 1, f"hits={len(fn_hits)}")
if fn_hits and isinstance(fn_hits[0], dict):
    check("page2 footnote is _is_footnote text", fn_hits[0].get("_is_footnote") is True)
    check("page2 footnote y near table bottom", fn_hits[0].get("y0", 0) > 500)

print("\n=== 6. page4 表内标签行 — 数据列网格延续则不拆表 ===")
lp4 = parser.parse("data/input_pdfs/test_subset8.pdf", target_pages=[4]).to_dict()
entries4, _ = hybrid_segment_tables(lp4, docx_tables=[])

main_cash_table = None
for e in entries4:
    if e.get("page") != 4 or e.get("type") != "table":
        continue
    flat = " ".join(str(c) for row in e.get("data", []) for c in row)
    if "46,691" in flat and "法定存款准备金" in flat and "2,206,678" in flat:
        main_cash_table = e
        break

check("page4 cash + reserve in one table", main_cash_table is not None)
if main_cash_table:
    data4 = main_cash_table.get("data", [])
    labels = [str(r[0]).strip() for r in data4 if r and str(r[0]).strip()]
    check(
        "page4 section header in table rows",
        any("存放中央银行款项" in " ".join(str(c) for c in row) for row in data4),
    )
    check(
        "page4 cash and reserve rows together",
        any("现金" in l for l in labels) and any("法定存款准备金" in l for l in labels),
        str(labels[:8]),
    )
    check("page4 合计 in same table", any("合计" in l for l in labels))

# 「存放中央银行款项」不应单独成 text 段
orphan_section = [
    e for e in entries4
    if e.get("page") == 4 and e.get("type") == "text"
    and str(e.get("context_text", e.get("data", ""))).strip() == "存放中央银行款项"
]
check("page4 section title not standalone text", len(orphan_section) == 0, f"n={len(orphan_section)}")

# 注释续行仍应抽出（不全焊回表内）
footnote_cont = [
    e for e in entries4
    if e.get("page") == 4 and e.get("type") == "text"
    and "用于本集团的日常业务运作" in str(e.get("context_text", e.get("data", "")))
]
check("page4 footnote continuation still text", len(footnote_cont) >= 1)

print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
