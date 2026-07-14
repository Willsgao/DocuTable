# -*- coding: utf-8
"""StructuredTable 访问辅助。"""

from __future__ import annotations

from typing import List, Optional

from codes.table_engine.models import StructuredTable


def dense_rows(table: StructuredTable) -> List[List[str]]:
    return table.iter_rows_dense()


def cell_text(table: StructuredTable, row: int, col: int) -> str:
    if row < 0 or row >= len(table.rows):
        return ""
    r = table.rows[row]
    if col < 0 or col >= len(r) or r[col] is None:
        return ""
    return str(r[col].text).strip()


def find_row_index(table: StructuredTable, col0_value: str) -> Optional[int]:
    target = str(col0_value).strip()
    for i, row in enumerate(table.rows):
        if cell_text(table, i, 0) == target:
            return i
        if cell_text(table, i, 0).startswith(target + " "):
            return i
    return None


def col0_values(table: StructuredTable) -> List[str]:
    return [cell_text(table, i, 0) for i in range(len(table.rows))]
