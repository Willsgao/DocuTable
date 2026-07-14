# -*- coding: utf-8 -*-
"""CC2 Page 13 — 五列 a/b/c 表头与坐标分列。"""
import json
import sys
from pathlib import Path

from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _build_table_from_liteparse_fallback,
)
from codes.table_validator.table_content_splitter import split_mixed_table_entries

CACHE = Path(
    "data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024年度资本管理第三支柱信息披露报告"
    "/liteparse/pages.json"
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


def main():
    if not CACHE.exists():
        print(f"skip: {CACHE}")
        return 0

    d = json.loads(CACHE.read_text(encoding="utf-8"))
    lp = {"pages": d["pages"]}
    b = [x for x in detect_table_boundaries_from_liteparse(lp) if x["page"] == 13][0]
    t = _build_table_from_liteparse_fallback(b, lp)
    parts = split_mixed_table_entries([t])
    tbl = next(p for p in parts if p.get("type") == "table")

    check("CC2 layout flag", tbl.get("_cc2_abc_layout"))
    check("5 columns", max(len(r) for r in tbl["data"]) == 5)

    data = tbl["data"]
    check("row0 abc", data[0][2:5] == ["a", "b", "c"])
    check(
        "date in col2",
        any("2024" in str(r[2]) and "12" in str(r[2]) for r in data[:4]),
    )
    check(
        "unit row col0",
        any("人民币" in str(r[0]) for r in data[:5]),
    )
    check(
        "finance header col2",
        any("财务并表" in str(r[2]) and "资产负债表" not in str(r[2]) for r in data[:6]),
    )
    check(
        "regulatory header col3",
        any("监管并表" in str(r[3]) for r in data[:6]),
    )
    check(
        "balance sheet subheader row",
        any(
            str(r[2]).strip() == "资产负债表" and str(r[3]).strip() == "资产负债表"
            for r in data[:8]
        ),
    )
    check(
        "code header col4",
        any(str(r[4]).strip() == "代码" for r in data[:8]),
    )

    row1 = next((r for r in data if str(r[0]).strip() == "1"), None)
    check("data row1 split", row1 and row1[1] and "2,571,361" in str(row1[2]))
    check("data row1 two amounts", row1 and str(row1[2]).strip() and str(row1[3]).strip())

    row17 = next((r for r in data if str(r[0]).strip() == "17"), None)
    row18 = next((r for r in data if str(r[0]).strip() == "18"), None)
    check("row17 code b in col4", row17 and str(row17[4]).strip() == "b")
    check("row18 code a in col4", row18 and str(row18[4]).strip() == "a")

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
