import json
from pathlib import Path
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
    _build_items,
    _cluster_text_items_into_blocks,
    _peel_pillar_grid_header_items_from_block,
    _classify_gap_text,
    _estimate_median_row_height,
)

lp = json.loads(
    Path(
        r"data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024"
        r"年度资本管理第三支柱信息披露报告/liteparse/pages.json"
    ).read_text(encoding="utf-8")
)
bounds = detect_table_boundaries_from_liteparse(lp)
p10 = [p for p in lp["pages"] if p["page_number"] == 10][0]
items = _build_items(p10["text_items"], 10)
b10 = [x for x in bounds if x["page"] == 10][0]
median = _estimate_median_row_height(items)

gap_items = [
    it
    for it in items
    if it.get("y_mid", (it["y0"] + it["y1"]) / 2) < b10["y0"]
]
print("gap items:", len(gap_items), "boundary y0", b10["y0"])
blocks = _cluster_text_items_into_blocks(gap_items)
print("blocks:", len(blocks))
for bi, block in enumerate(blocks):
    print(f"\n--- block {bi} y={block['y0']:.0f}-{block['y1']:.0f} ---")
    print(block["full_text"][-200:])
    peeled_block, peeled = _peel_pillar_grid_header_items_from_block(block)
    print("peeled items:", len(peeled))
    if peeled:
        print("peeled text:", " | ".join(it["text"] for it in peeled))
    target, field = _classify_gap_text(block, None, b10, median)
    print("classify:", target, field)
