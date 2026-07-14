import json
from pathlib import Path
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
    _build_items,
    _cluster_items_by_y,
    _build_table_from_liteparse_fallback,
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
items = _build_items(
    [p for p in lp["pages"] if p["page_number"] == 25][0]["text_items"], 25
)
bounds = detect_table_boundaries_from_liteparse(lp)
enriched, _ = _capture_gap_text_items(bounds, lp)
b = [x for x in enriched if x["page"] == 25][0]
print("boundary y0-y1:", b["y0"], b["y1"])

for it in sorted(items, key=lambda x: (x["y0"], x["x0"])):
    if 450 <= it["y0"] <= 640:
        print(f"y={it['y0']:.0f} x={it['x0']:.0f} | {it['text']!r}")

t = _build_table_from_liteparse_fallback(b, lp)
print("\n=== BEFORE pillar split ===")
for i, r in enumerate(t["data"]):
    print(i, r)
