# -*- coding: utf-8 -*-
"""P19 季报指标表：OCR 粘连四季列表头时仍须 5 列对齐。"""
from __future__ import annotations

import json
from pathlib import Path

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.table_access import dense_rows


def _load_p19() -> PageSource:
    root = next(
        p
        for p in Path(__file__).resolve().parent.joinpath("data/mid_cache").iterdir()
        if "601838" in p.name
    )
    page = json.loads((root / "liteparse" / "pages.json").read_text(encoding="utf-8"))[
        "pages"
    ][18]
    items = [
        SourceItem(
            text=str(it.get("text") or ""),
            bbox=BBox(
                float(it["x0"]),
                float(it["y0"]),
                float(it["x1"]),
                float(it["y1"]),
            ),
            page=19,
            item_index=f"i{i}",
            y_mid=(float(it["y0"]) + float(it["y1"])) / 2,
        )
        for i, it in enumerate(page.get("text_items") or [])
    ]
    regions = [
        RegionBox(
            float(r["x0"]),
            float(r["y0"]),
            float(r["x1"]),
            float(r["y1"]),
            float(r.get("confidence") or 1),
            i,
        )
        for i, r in enumerate(page.get("table_regions") or [])
    ]
    return PageSource(
        page_number=19,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def _quarter_table_rows(result) -> list[list[str]]:
    found: list[list[str]] = []
    for e in result.entries:
        if not e.table:
            continue
        rows = [[str(c or "").strip() for c in r] for r in dense_rows(e.table)]
        blob = " ".join(c for r in rows for c in r)
        if "一季度" in blob and "营业收入" in blob:
            found.append(rows)
    return found


def test_p19_quarter_five_columns():
    result = build_page(_load_p19())
    tables = _quarter_table_rows(result)
    assert len(tables) == 1, f"expected 1 quarter table, got {len(tables)}"
    rows = tables[0]
    header = rows[0]
    assert sum(1 for c in header if "季度" in c) == 4, header
    assert not any("三季度" in c and "四季度" in c for c in header), header
    assert header[0] == "项目" or "项目" in header[0], header

    rev = next(r for r in rows if r and "营业收入" in r[0])
    assert rev[0] == "营业收入", rev
    assert rev[1:] == [
        "5,638,180",
        "5,946,877",
        "5,656,087",
        "5,740,383",
    ], rev

    cash = next(r for r in rows if any("现金流量" in (c or "") for c in r))
    assert "-975,242" in cash
    assert not any("-975,242" in (c or "") and "现金流量" in (c or "") for c in cash), cash


if __name__ == "__main__":
    test_p19_quarter_five_columns()
    print("ALL PASS")
