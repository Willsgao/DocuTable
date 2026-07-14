# -*- coding: utf-8 -*-
"""逾期贷款表：日期+占比重复列标不得丢失。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.table_access import dense_rows


def _item(text: str, x0: float, y0: float) -> SourceItem:
    x1 = x0 + max(len(text) * 5.0, 24.0)
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x1, y0 + 10),
        page=1,
        item_index=f"i_{y0}_{x0}",
        y_mid=y0 + 5,
    )


def _make_overdue_loan_page() -> PageSource:
    items: list[SourceItem] = []
    y_unit, y_hdr, y0 = 80.0, 100.0, 120.0
    items.append(_item("单位：千元", 400, y_unit))
    for text, x in [
        ("项目", 60),
        ("2024 年 12 月 31 日", 180),
        ("占比", 280),
        ("2023 年 12 月 31 日", 360),
        ("占比", 460),
    ]:
        items.append(_item(text, x, y_hdr))
    rows = [
        ("逾期 1 天至 90 天", ["1,386,697", "26.58%", "1,452,103", "29.11%"]),
        ("逾期 90 天至 1 年", ["1,255,794", "24.08%", "1,089,542", "21.84%"]),
        ("逾期贷款合计", ["5,216,281", "100.00%", "4,987,896", "100.00%"]),
    ]
    for i, (label, vals) in enumerate(rows):
        y = y0 + i * 20
        items.append(_item(label, 60, y))
        for v, x in zip(vals, [180, 280, 360, 460]):
            items.append(_item(v, x, y))
    return PageSource(
        page_number=1,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[RegionBox(54, 70, 541, 200, 1.0, 0)],
        is_table_page=True,
    )


def test_overdue_loan_ratio_headers_preserved():
    page = _make_overdue_loan_page()
    result = build_page(page)
    table = next(e.table for e in result.entries if e.table)
    rows = dense_rows(table)
    header = next(
        (r for r in rows if any(str(c or "").strip() == "项目" for c in r)),
        None,
    )
    assert header is not None, rows[:3]
    ratio_cols = [str(c or "").strip() for c in header if str(c or "").strip() == "占比"]
    assert len(ratio_cols) == 2, f"expected 2 占比 headers, got {header!r}"
    assert "2024" in " ".join(str(c) for c in header if c), header
    assert "2023" in " ".join(str(c) for c in header if c), header


def test_overdue_loan_body_order_preserved():
    page = _make_overdue_loan_page()
    result = build_page(page)
    table = next(e.table for e in result.entries if e.table)
    labels = [str(r[0] or "").strip() for r in dense_rows(table) if str(r[0] or "").strip()]
    body_labels = [lb for lb in labels if lb != "项目" and "单位" not in lb]
    assert body_labels[0].startswith("逾期 1 天"), body_labels
    assert body_labels[-1] == "逾期贷款合计", body_labels


if __name__ == "__main__":
    test_overdue_loan_ratio_headers_preserved()
    print("ratio headers preserved OK")
    test_overdue_loan_body_order_preserved()
    print("body order preserved OK")
