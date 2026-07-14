# -*- coding: utf-8 -*-
"""CC1 Page 11 — 禁止多行行号粘连、折行标签合并。"""
import json
import sys
from pathlib import Path

from codes.table_validator.hybrid_segmenter import _build_table_from_liteparse_fallback

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
    page = next(x for x in d["pages"] if x["page_number"] == 11)
    reg1 = page["table_regions"][1]
    boundary = {
        "page": 11,
        "y0": reg1["y0"],
        "y1": reg1["y1"],
        "x0": reg1["x0"],
        "x1": reg1["x1"],
        "_pre_header_items": [],
    }
    t = _build_table_from_liteparse_fallback(boundary, lp)
    check("table built", t is not None)
    if not t:
        return 1

    data = t["data"]
    col0 = [str(r[0]).strip() for r in data if r]

    check("no glued row numbers 23242526", not any("232425" in c for c in col0))
    check("no glued row numbers 272829", not any("272829" in c for c in col0))
    check("row 22 present", "22" in col0)
    check("row 23 present", "23" in col0)
    check("row 26 present", "26" in col0)
    check("row 53 present", "53" in col0)

    row22 = next((r for r in data if str(r[0]).strip() == "22"), None)
    check("row22 wrapped label", row22 and "递延税" in str(row22[1]) and "扣除" in str(row22[1]))
    check(
        "row22 dash in code column",
        row22
        and (
            str(row22[3]).strip() == "-"
            if len(row22) >= 4
            else str(row22[2]).strip() == "-"
        ),
    )

    row25 = next((r for r in data if str(r[0]).strip() == "25"), None)
    check("row25 amount", row25 and "7,760" in str(row25[2]))

    row26 = next((r for r in data if str(r[0]).strip() == "26"), None)
    check("row26 amount", row26 and "3,165,549" in str(row26[2]))

    # 全流水线（含 split_mixed）：45/47 第三列不得塞入标签文本
    bounds = [b for b in __import__(
        "codes.table_validator.hybrid_segmenter",
        fromlist=["detect_table_boundaries_from_liteparse"],
    ).detect_table_boundaries_from_liteparse(lp) if b["page"] == 11]
    from codes.table_validator.table_content_splitter import split_mixed_table_entries
    ft = _build_table_from_liteparse_fallback(bounds[0], lp)
    full = split_mixed_table_entries([ft])[0]["data"]
    r45 = next((r for r in full if str(r[0]).strip() == "45"), None)
    r47 = next((r for r in full if str(r[0]).strip() == "47"), None)
    r25 = next((r for r in full if str(r[0]).strip() == "25"), None)
    check("full pipeline row45 cols=4", r45 and len(r45) == 4)
    check(
        "full pipeline row45 no label in col3",
        r45 and len(r45) >= 4 and "直接" not in str(r45[3]) and str(r45[3]).strip() in ("-", ""),
    )
    check("full pipeline row47 label in col2", r47 and "未并表" in str(r47[1]))
    check(
        "full pipeline row47 col3 is dash only",
        r47 and len(r47) >= 4 and str(r47[3]).strip() in ("-", ""),
    )
    check(
        "full pipeline row25 amount in col2 not col3",
        r25 and len(r25) >= 4 and "7,760" in str(r25[2]) and "7,760" not in str(r25[3]),
    )
    check(
        "full pipeline 4 columns preserved",
        full and max(len(r) for r in full) >= 4,
    )
    hdr = full[1] if full and len(full) > 1 else []
    check(
        "header has 数额 and 代码 separate",
        len(hdr) >= 4 and str(hdr[2]).strip() == "数额" and str(hdr[3]).strip() == "代码",
    )

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
