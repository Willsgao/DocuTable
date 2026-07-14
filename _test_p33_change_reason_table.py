# -*- coding: utf-8 -*-
"""P33：变化原因表表头拆列、百分比与原因分列、折行不误并标签。"""
from __future__ import annotations

import re

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.table_access import dense_rows


def _item(text: str, x0: float, y0: float, x1: float | None = None, idx: str = "") -> SourceItem:
    x1 = x1 if x1 is not None else x0 + len(text) * 4.8
    y1 = y0 + 10
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        page=33,
        item_index=idx or f"i_{y0}_{x0}",
        y_mid=(y0 + y1) / 2,
    )


def _make_p33_like_page() -> PageSource:
    items: list[SourceItem] = []
    y = 120.0
    # 上半：资产负债表（4列）
    for label, v1, v2, pct in [
        ("吸收存款", "885,859,340", "780,421,289", "13.51%"),
        ("-财政性存款", "130,259", "135,404", "-3.80%"),
        ("负债总计", "1,164,211,707", "1,019,923,459", "14.15%"),
    ]:
        items.append(_item(label, 51, y))
        items.append(_item(v1, 200, y, 258))
        items.append(_item(v2, 300, y, 358))
        items.append(_item(pct, 400, y, 430))
        y += 18

    y = 400.0
    items.append(_item("（二）资产负债表中变化幅度超过30%的项目及变化原因", 51, y))
    y += 20
    items.append(_item("单位：千元", 400, y))
    y += 16
    # 表头：模拟 OCR 将日期+增减粘成一格
    items.append(_item("项目", 60, y))
    items.append(_item("主要原因", 480, y, 530))
    items.append(_item("2024 年 12 月 31 日 2023 年 12 月 31 日 增减幅度", 170, y, 420))
    y += 16

    rows = [
        ("存放同业及其他金融机构款项", "4,135,772", "1,661,178", "148.97%存放同业清算款项增加"),
        ("拆出资金", "89,945,153", "68,823,341", "30.69%拆放同业款项增加"),
        ("其他资产", "4,245,630", "2,285,652", "85.75%清算款项增加及预付购房"),
    ]
    other_y = None
    for label, v1, v2, glued in rows:
        items.append(_item(label, 60, y))
        items.append(_item(v1, 200, y, 258))
        items.append(_item(v2, 300, y, 358))
        items.append(_item(glued, 400, y, 520))
        if label == "其他资产":
            other_y = y
        y += 16
    if other_y is not None:
        items.append(_item("款", 400, other_y + 12, 410))

    y += 4
    items.append(_item("预计负债", 60, y))
    items.append(_item("221,082", 200, y, 258))
    items.append(_item("352,667", 300, y, 358))
    items.append(_item("-37.31%表外业务预期损失计提减", 400, y, 520))
    items.append(_item("少", 400, y + 12, 410))

    region = RegionBox(29, 100, 566, 744, 1.0, 0)
    return PageSource(
        page_number=33,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[region],
        is_table_page=True,
    )


def test_change_table_header_split_into_columns():
    page = _make_p33_like_page()
    result = build_page(page)
    change = None
    for e in result.entries:
        if e.table and any(
            "存放同业" in str(c or "")
            for r in dense_rows(e.table)
            for c in r
        ):
            change = e.table
            break
    assert change is not None, "change table not found"
    rows = dense_rows(change)
    header = next(
        (
            r for r in rows
            if str(r[0] or "").strip() == "项目"
            and any("2024" in str(c or "") for c in r)
        ),
        None,
    )
    assert header is not None, rows[:4]
    cells = [str(c or "").strip() for c in header if str(c or "").strip()]
    assert any("2024" in c and "2023" not in c for c in cells), cells
    assert any("2023" in c for c in cells), cells
    assert any("增减" in c for c in cells), cells
    assert any("主要原因" in c for c in cells), cells
    for c in cells:
        if "2024" in c and "2023" in c:
            raise AssertionError(f"merged header cell: {c!r}")


def test_other_asset_row_percent_reason_split():
    page = _make_p33_like_page()
    result = build_page(page)
    change = next(
        e.table for e in result.entries
        if e.table and any("其他资产" in str(c or "") for r in dense_rows(e.table) for c in r)
    )
    row = next(
        r for r in dense_rows(change)
        if any("其他资产" in str(c or "") for c in r)
    )
    label = str(row[0] or "")
    assert label == "其他资产", label
    assert "款" not in label, row
    flat = [str(c or "") for c in row]
    assert any(c == "85.75%" or c.endswith("85.75%") for c in flat), flat
    assert any("清算款项增加" in c for c in flat), flat
    assert not any("85.75%清算" in c for c in flat if c), flat


def test_expected_liability_row():
    page = _make_p33_like_page()
    result = build_page(page)
    change = next(
        e.table for e in result.entries
        if e.table and any("预计负债" in str(c or "") for r in dense_rows(e.table) for c in r)
    )
    row = next(
        r for r in dense_rows(change)
        if any("预计负债" in str(c or "") for c in r)
    )
    label = str(row[0] or "")
    assert label == "预计负债", label
    assert "少" not in label, row
    flat = [str(c or "") for c in row]
    assert any("37.31%" in c for c in flat), flat
    assert any("表外业务" in c and "少" in c for c in flat), flat


def test_triple_merged_cells_split():
    """金额+百分比+原因粘在同一格（如 2,150,203 31.90%待清算款项增加）须拆列。"""
    from codes.table_engine.geometry.numeric import split_amount_percent_reason_text

    assert split_amount_percent_reason_text("2,150,203 31.90%待清算款项增加") == (
        "2,150,203", "31.90%", "待清算款项增加",
    )
    assert split_amount_percent_reason_text("196,946 564.46%债券估值变动") == (
        "196,946", "564.46%", "债券估值变动",
    )

    page = _make_p33_like_page()
    page.items = [it for it in page.items if it.bbox.y0 < 600]
    for label, v1, v2, pct_reason, y in [
        ("其他负债", "2,836,188", "2,150,203 31.90%待清算款项增加", None, 620),
        ("资本公积", "13,172,237", "8,791,988", "49.82%可转债转股", 636),
        ("其他综合收益", "1,308,622", "196,946 564.46%债券估值变动", None, 652),
    ]:
        page.items.append(_item(label, 60, y))
        page.items.append(_item(v1, 200, y, 258))
        if pct_reason:
            page.items.append(_item(v2, 300, y, 358))
            page.items.append(_item(pct_reason, 400, y, 520))
        else:
            page.items.append(_item(v2, 300, y, 520))

    result = build_page(page)
    change = next(
        e.table for e in result.entries
        if e.table and any("其他负债" in str(c or "") for r in dense_rows(e.table) for c in r)
    )
    for label, v2, pct, reason in [
        ("其他负债", "2,150,203", "31.90%", "待清算款项增加"),
        ("资本公积", "8,791,988", "49.82%", "可转债转股"),
        ("其他综合收益", "196,946", "564.46%", "债券估值变动"),
    ]:
        row = next(r for r in dense_rows(change) if str(r[0] or "").strip() == label)
        flat = [str(c or "") for c in row]
        assert v2 in flat, (label, flat)
        assert pct in flat, (label, flat)
        assert reason in flat, (label, flat)
        for c in flat:
            if not c:
                continue
            assert not (
                re.search(r"[\d,，]{3,}", c)
                and "%" in c
                and re.search(r"[\u4e00-\u9fff]", c)
            ), (label, c)


def test_four_col_grid_mixed_cells_repair():
    """4 列网格时第 3 列三合一粘连须扩成 5 列并拆开（与用户截图一致）。"""
    from codes.table_engine.models import BBox, Cell, ColumnGrid, ColumnRange, StructuredTable
    from codes.table_engine.split.structure_split import repair_glued_percent_reason_cells
    from codes.table_engine.table_access import dense_rows

    def _row(label, v1, glued, reason_dup, y):
        return [
            Cell(label, BBox(60, y, 120, y + 10), 0, 0, []),
            Cell(v1, BBox(200, y, 258, y + 10), 0, 1, []),
            Cell(glued, BBox(300, y, 520, y + 10), 0, 2, []),
            Cell(reason_dup, BBox(480, y, 530, y + 10), 0, 3, []),
        ]

    rows = [
        _row("项目", "2024年12月31日", "2023年12月31日", "增减幅度", 400),
        _row("其他负债", "2,836,188", "2,150,203 31.90%待清算款项增加", "待清算款项增加", 420),
        _row("资本公积", "13,172,237", "8,791,988 49.82%可转债转股", "可转债转股", 436),
        _row("其他综合收益", "1,308,622", "196,946 564.46%债券估值变动", "债券估值变动", 452),
    ]
    grid = ColumnGrid(
        ranges=[
            ColumnRange(50, 150, 0),
            ColumnRange(170, 270, 1),
            ColumnRange(280, 450, 2),
            ColumnRange(460, 560, 3),
        ],
        layout_id="constraint_grid",
    )
    table = StructuredTable(
        page=33,
        pages=[33],
        y0=400,
        y1=460,
        x0=50,
        x1=560,
        rows=rows,
        grid=grid,
    )
    fixed = repair_glued_percent_reason_cells(table)
    assert fixed.grid.col_count >= 5, fixed.grid.col_count
    body = dense_rows(fixed)[1:]
    for label, v2, pct, reason in [
        ("其他负债", "2,150,203", "31.90%", "待清算款项增加"),
        ("资本公积", "8,791,988", "49.82%", "可转债转股"),
        ("其他综合收益", "196,946", "564.46%", "债券估值变动"),
    ]:
        row = next(r for r in body if str(r[0] or "").strip() == label)
        flat = [str(c or "") for c in row]
        assert v2 in flat, (label, flat)
        assert pct in flat, (label, flat)
        assert reason in flat, (label, flat)
        for c in flat:
            if not c:
                continue
            assert not (
                re.search(r"[\d,，]{3,}", c)
                and "%" in c
                and re.search(r"[\u4e00-\u9fff]", c)
            ), (label, c)


if __name__ == "__main__":
    test_change_table_header_split_into_columns()
    print("header split OK")
    test_other_asset_row_percent_reason_split()
    print("other asset row OK")
    test_expected_liability_row()
    print("expected liability row OK")
    test_triple_merged_cells_split()
    print("triple merged cells OK")
    test_four_col_grid_mixed_cells_repair()
    print("four col grid repair OK")
