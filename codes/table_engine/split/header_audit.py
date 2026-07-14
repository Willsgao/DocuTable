# -*- coding: utf-8
"""表头带完整性校验。"""

from __future__ import annotations

import re
from typing import List

from codes.table_engine.models import DocumentEntry, PageSource
from codes.table_engine.scope.header_supplement import table_header_band_present
from codes.table_engine.table_access import dense_rows

_PAGE_CHROME_MAX_Y = 65.0
_NARRATIVE_MIN_CN = 10


def _prior_text_is_page_chrome_only(entries: List[DocumentEntry], table_index: int) -> bool:
    for entry in entries[:table_index]:
        if entry.kind != "text" or entry.text_block is None:
            continue
        text = entry.text_block.text.strip()
        if not text:
            continue
        cn = len(re.findall(r"[\u4e00-\u9fff]", text))
        if cn >= _NARRATIVE_MIN_CN and "。" in text:
            return False
        if entry.text_block.y0 > _PAGE_CHROME_MAX_Y + 40:
            return False
    return True


def apply_header_audit(
    entries: List[DocumentEntry],
    page: PageSource,
    warnings: List[str],
) -> List[DocumentEntry]:
    """数据列上方缺表头带 → 告警（首页仅页眉时豁免）。"""
    table_indices = [i for i, e in enumerate(entries) if e.kind == "table" and e.table]

    for ti, entry_idx in enumerate(table_indices):
        entry = entries[entry_idx]
        assert entry.table is not None
        rows = dense_rows(entry.table)
        if len(rows) < 2:
            continue

        scan = min(10, len(rows))
        if table_header_band_present(rows[:scan]):
            continue

        is_first_table = ti == 0
        if is_first_table and _prior_text_is_page_chrome_only(entries, entry_idx):
            continue

        ry0 = float(entry.table.metadata.get("region_y0", entry.y0))
        if ry0 < 200.0 and entry.table.metadata.get("headerless_gap_table"):
            continue

        warnings.append(
            f"P{page.page_number}: table missing header band "
            f"y={entry.y0:.0f} rows={len(rows)}"
        )

    return entries
