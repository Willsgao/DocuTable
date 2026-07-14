# -*- coding: utf-8 -*-
"""SEC1 Page 27 — 坐标驱动行/表头精修。"""
import json
import sys
from pathlib import Path

from codes.table_validator.hybrid_segmenter import _build_table_from_liteparse_fallback

PASS = FAIL = 0
CACHE = Path(
    "data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024年度资本管理第三支柱信息披露报告"
    "/liteparse/pages.json"
)


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
        print(f"skip: cache not found {CACHE}")
        return 0

    d = json.loads(CACHE.read_text(encoding="utf-8"))
    lp = {"pages": d["pages"]}
    page = next(x for x in d["pages"] if x["page_number"] == 27)
    reg = page["table_regions"][0]
    boundary = {
        "page": 27,
        "y0": reg["y0"],
        "y1": reg["y1"],
        "x0": reg["x0"],
        "x1": reg["x1"],
        "_pre_header_items": [],
    }
    t = _build_table_from_liteparse_fallback(boundary, lp)
    check("table built", t is not None)
    if not t:
        return 1

    data = t.get("data", [])
    flat = " ".join(str(c) for row in data for c in row)

    check("header 足STC and 准的 on separate rows", any(
        str(c).strip() == "足STC标" for row in data for c in row
    ) and any(
        str(c).strip() == "准的" for row in data for c in row
    ) and not any("足STC标准" in str(c) for row in data for c in row))
    check("no orphan 准的 data row", not any(
        sum(1 for c in row if str(c).strip() == "准的") >= 2
        and not any(str(c).strip().isdigit() for c in row[:1])
        for row in data
    ))

    # row 1 零售类合计
    row1 = next((r for r in data if str(r[0]).strip() == "1"), None)
    check("row1 has label col", row1 and "零售" in str(row1[1]))
    check("row1 has value", row1 and "7,195" in flat)

    row2 = next((r for r in data if str(r[0]).strip() == "2"), None)
    check("row2 wrapped label", row2 and "住房抵押" in str(row2[1]) and "款" in str(row2[1]))
    check("row2 no separate 款 col", row2 and str(row2[1]).count("款") >= 1 and (
        len(row2) < 3 or str(row2[2]) != "款"
    ))

    row6 = next((r for r in data if str(r[0]).strip() == "6"), None)
    check("row6 num/label split", row6 and "公司类" in str(row6[1]))

    row8 = next((r for r in data if str(r[0]).strip() == "8"), None)
    check("row8 mortgage loan wrapped", row8 and "商用房地产" in str(row8[1]) and "贷款" in str(row8[1]))

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
