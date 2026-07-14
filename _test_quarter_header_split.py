# -*- coding: utf-8 -*-
"""季度列表头：须按列拆开，不得与「项目」混在一格。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.table_access import dense_rows


def _item(text: str, x0: float, y0: float, x1: float | None = None) -> SourceItem:
    x1 = x1 if x1 is not None else x0 + len(text) * 4.5
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x1, y0 + 10),
        page=1,
        item_index=f"i_{y0}_{x0}",
        y_mid=y0 + 5,
    )


def _make_quarter_table_page(*, merged_header: bool = False) -> PageSource:
    items: list[SourceItem] = []
    y_header = 100
    if merged_header:
        items.append(
            _item(
                "一季度（1-3 月） 二季度（4-6 月） 三季度（7-9 月） 四季度（10-12 月） 项目",
                60,
                y_header,
                520,
            )
        )
    else:
        items.append(_item("项目", 60, y_header))
        for label, x in [
            ("一季度（1-3 月）", 200),
            ("二季度（4-6 月）", 300),
            ("三季度（7-9 月）", 400),
            ("四季度（10-12 月）", 500),
        ]:
            items.append(_item(label, x, y_header))

    rows = [
        ("营业收入", ["5,638,180", "5,946,877", "5,656,087", "5,740,383"], 120),
        ("归属于母公司股东的净利润", ["2,850,954", "3,316,050", "2,871,191", "3,820,185"], 140),
        ("归属于母公司股东的扣除", ["2,824,925", "3,296,404", "2,843,943", "3,859,380"], 160),
        ("非经常性损益净利润", ["", "", "", ""], 170),
        ("经营活动产生的现金流量", ["-975,242", "-2,553,972", "-16,415,768", "-74,281,027"], 180),
        ("净额", ["", "", "", ""], 190),
    ]
    for label, vals, y in rows:
        items.append(_item(label, 60, y))
        for v, x in zip(vals, [200, 300, 400, 500]):
            if v:
                items.append(_item(v, x, y))

    return PageSource(
        page_number=1,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[RegionBox(54, 90, 541, 210, 1.0, 0)],
        is_table_page=True,
    )


def _header_row(result) -> list[str]:
    for e in result.entries:
        if not e.table:
            continue
        rows = dense_rows(e.table)
        if not rows:
            continue
        if any("营业收入" in str(r[0] or "") for r in rows[1:]):
            return [str(c or "").strip() for c in rows[0]]
    return []


def test_quarter_header_split_separate_items():
    page = _make_quarter_table_page(merged_header=False)
    result = build_page(page)
    header = _header_row(result)
    assert header, "expected table"
    assert header[0] == "项目", header
    assert any("一季度" in c for c in header[1:]), header
    assert sum(1 for c in header if "季度" in c) == 4, header
    assert " ".join(header).count("一季度") == 1, header


def test_quarter_header_split_merged_ocr_item():
    page = _make_quarter_table_page(merged_header=True)
    result = build_page(page)
    header = _header_row(result)
    assert header, "expected table"
    assert header[0] == "项目", header
    assert sum(1 for c in header if "季度" in c) == 4, header
    joined = " ".join(c for c in header if c)
    assert joined.count("一季度") == 1, joined
    assert "二季度" in joined and "四季度" in joined, joined


def test_quarter_label_suffix_rows_merged():
    page = _make_quarter_table_page(merged_header=False)
    result = build_page(page)
    labels = []
    for e in result.entries:
        if e.table:
            for r in dense_rows(e.table):
                labels.append(str(r[0] or ""))
    assert any("扣除" in lb and "非经常性" in lb for lb in labels), labels
    assert any("现金流量" in lb and "净额" in lb for lb in labels), labels
    assert labels.count("净额") == 0 or not any(lb.strip() == "净额" for lb in labels), labels


if __name__ == "__main__":
    test_quarter_header_split_separate_items()
    print("separate items OK")
    test_quarter_header_split_merged_ocr_item()
    print("merged OCR header OK")
    test_quarter_label_suffix_rows_merged()
    print("label suffix merge OK")
