# -*- coding: utf-8 -*-
import json
from pathlib import Path

from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _build_table_from_liteparse_fallback,
    _build_items,
    _ensure_y_mid,
)
from codes.table_validator.cell_differ import _cluster_items_by_y
from codes.table_validator.table_content_splitter import (
    split_mixed_table_entries,
    split_clustered_date_code_header_rows,
)
from codes.table_validator.coord_row_refiner import refine_clustered_rows_by_coords
from codes.table_validator.liteparse_table_segmenter import (
    infer_cc1_ab_four_column_ranges,
    _normalize_rows_to_columns,
)

CACHE = Path(
    "data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024年度资本管理第三支柱信息披露报告"
    "/liteparse/pages.json"
)
d = json.loads(CACHE.read_text(encoding="utf-8"))
page = next(x for x in d["pages"] if x["page_number"] == 13)
reg = page["table_regions"][0]
items = _build_items(page["text_items"], 13)
scoped = [
    it for it in items
    if reg["x0"] <= (it["x0"] + it["x1"]) / 2 <= reg["x1"]
    and reg["y0"] <= (it["y0"] + it["y1"]) / 2 <= reg["y1"] + 30
]

print("=== Header band items (y 255-340) ===")
for it in sorted(scoped, key=lambda x: (x["y0"], x["x0"])):
    cy = (it["y0"] + it["y1"]) / 2
    if cy < 340:
        print("  y=%.0f x0=%.0f %r" % (cy, it["x0"], it["text"]))

cc1 = infer_cc1_ab_four_column_ranges(page)
print("\ncc1_ab ranges:", cc1)

rows = refine_clustered_rows_by_coords(
    split_clustered_date_code_header_rows(
        _cluster_items_by_y(_ensure_y_mid(scoped), use_dynamic_threshold=True)
    )
)
rows = _normalize_rows_to_columns(rows, col_ranges=cc1)
print("\n=== First 8 normalized rows ===")
for i, r in enumerate(rows[:8]):
    print(i, r.get("texts", []))

lp = {"pages": d["pages"]}
bounds = [b for b in detect_table_boundaries_from_liteparse(lp) if b["page"] == 13]
t = _build_table_from_liteparse_fallback(bounds[0], lp)
full = split_mixed_table_entries([t])[0]
print("\n=== Full pipeline first 8 rows ===")
for i, row in enumerate(full["data"][:8]):
    print(i, row)
