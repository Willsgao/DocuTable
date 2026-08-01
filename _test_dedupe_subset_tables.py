# -*- coding: utf-8 -*-
"""同页子集重叠表去重：短碎片不应与完整表并存。"""
from __future__ import annotations

from codes.table_engine.models import (
    BBox,
    Cell,
    ColumnGrid,
    ColumnRange,
    DocumentEntry,
    StructuredTable,
)
from codes.table_engine.split.structure_split import dedupe_subset_overlapping_tables
from codes.table_engine.table_access import dense_rows


def _table(rows: list[list[str]], *, y0: float, y1: float, page: int = 242) -> StructuredTable:
    n_cols = max(len(r) for r in rows)
    grid = ColumnGrid(
        ranges=[
            ColumnRange(x0=100 + i * 80, x1=180 + i * 80, col_index=i)
            for i in range(n_cols)
        ],
        layout_id="constraint_grid",
    )
    cells = []
    for ri, row in enumerate(rows):
        crow = []
        for ci in range(n_cols):
            text = row[ci] if ci < len(row) else ""
            if str(text).strip():
                crow.append(
                    Cell(
                        text=text,
                        bbox=BBox(
                            100 + ci * 80,
                            y0 + ri * 12,
                            180 + ci * 80,
                            y0 + ri * 12 + 10,
                        ),
                        row=ri,
                        col=ci,
                        source_items=[f"r{ri}c{ci}"],
                    )
                )
            else:
                crow.append(None)
        cells.append(crow)
    return StructuredTable(
        page=page,
        pages=[page],
        x0=100,
        y0=y0,
        x1=100 + n_cols * 80,
        y1=y1,
        grid=grid,
        rows=cells,
        layout_id="constraint_grid",
        metadata={"scope_source_items": [f"i{k}" for k in range(len(rows) * 2)]},
    )


def test_short_prefix_table_dropped():
    short_rows = [
        ["", "", "", "2023年", "", ""],
        ["", "注释", "阶段一", "阶段二", "阶段三", "合计"],
        ["2023年1月1日", "", "17,768", "199", "16,901", "34,868"],
    ]
    long_rows = short_rows + [
        ["转移：", "", "", "", "", ""],
        ["转移至阶段一", "", "1", "2", "3", "4"],
    ]
    short = _table(short_rows, y0=374.5, y1=413.5)
    short.metadata["scope_source_items"] = ["i0", "i1", "i2"]
    long = _table(long_rows, y0=374.5, y1=541.7)
    long.metadata["scope_source_items"] = ["i0", "i1", "i2", "i3", "i4", "i5"]

    entries = [
        DocumentEntry(kind="table", page=242, y0=short.y0, y1=short.y1, table=short, entry_id=1),
        DocumentEntry(kind="table", page=242, y0=long.y0, y1=long.y1, table=long, entry_id=2),
    ]
    out = dedupe_subset_overlapping_tables(entries)
    assert len(out) == 1, len(out)
    assert len(dense_rows(out[0].table)) == len(long_rows)


def test_independent_year_tables_kept():
    """2024 表与 2023 表内容不同，即使同页也不去重。"""
    t2024 = _table(
        [
            ["", "", "", "2024年", "", ""],
            ["", "注释", "阶段一", "阶段二", "阶段三", "合计"],
            ["2024年1月1日", "", "1", "2", "3", "4"],
        ],
        y0=100,
        y1=300,
    )
    t2023 = _table(
        [
            ["", "", "", "2023年", "", ""],
            ["", "注释", "阶段一", "阶段二", "阶段三", "合计"],
            ["2023年1月1日", "", "17,768", "199", "16,901", "34,868"],
        ],
        y0=320,
        y1=520,
    )
    entries = [
        DocumentEntry(kind="table", page=242, y0=t2024.y0, y1=t2024.y1, table=t2024, entry_id=1),
        DocumentEntry(kind="table", page=242, y0=t2023.y0, y1=t2023.y1, table=t2023, entry_id=2),
    ]
    out = dedupe_subset_overlapping_tables(entries)
    assert len(out) == 2


if __name__ == "__main__":
    test_short_prefix_table_dropped()
    print("subset dropped OK")
    test_independent_year_tables_kept()
    print("independent kept OK")
    print("ALL PASS")
