import json
from pathlib import Path
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
    _build_items,
    _cluster_items_by_y,
)
from codes.table_validator.liteparse_table_segmenter import (
    _detect_column_ranges_from_rows,
    _normalize_rows_to_columns,
)

lp = json.loads(
    Path(
        r"data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024"
        r"年度资本管理第三支柱信息披露报告/liteparse/pages.json"
    ).read_text(encoding="utf-8")
)
p12 = [p for p in lp["pages"] if p["page_number"] == 12][0]
items = _build_items(p12["text_items"], 12)
print("=== items y=70-320 ===")
for it in sorted(items, key=lambda x: (x["y0"], x["x0"])):
    if 70 <= it["y0"] <= 320:
        print(f"y={it['y0']:.0f} x={it['x0']:.0f}-{it['x1']:.0f} | {it['text']!r}")

bounds = detect_table_boundaries_from_liteparse(lp)
enriched, _ = _capture_gap_text_items(bounds, lp)
b12 = [x for x in enriched if x["page"] == 12][0]
scoped = []
by0, by1 = b12["y0"], b12["y1"]
bx0, bx1 = b12["x0"], b12["x1"]
pre = b12.get("_pre_header_items", [])
scope_y0 = by0
if pre:
    scope_y0 = min(by0, min(it.get("y0", by0) for it in pre))
    scoped.extend(pre)
for it in items:
    cx = (it["x0"] + it["x1"]) / 2
    cy = it.get("y_mid", (it["y0"] + it["y1"]) / 2)
    if bx0 - 10 <= cx <= bx1 + 10 and scope_y0 - 10 <= cy <= by1 + 30:
        scoped.append(it)

rows = _cluster_items_by_y(scoped, use_dynamic_threshold=True)
print("\n=== raw rows (first 8) ===")
for i, row in enumerate(rows[:8]):
    texts = row.get("texts", [])
    print(i, texts)

col_ranges = _detect_column_ranges_from_rows(rows)
print("\ncol_ranges:", col_ranges)
norm = _normalize_rows_to_columns(rows, col_ranges)
print("\n=== normalized (first 8) ===")
for i, row in enumerate(norm[:8]):
    print(i, row.get("texts", []))
