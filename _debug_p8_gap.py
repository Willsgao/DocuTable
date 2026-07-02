from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _build_items,
    _cluster_text_items_into_blocks,
    _classify_gap_text,
    _estimate_median_row_height,
)

lp = LiteParseParser().parse("data/input_pdfs/test_subset8.pdf").to_dict()
bounds = detect_table_boundaries_from_liteparse(lp)
p8 = [b for b in bounds if b["page"] == 8]
p8.sort(key=lambda x: x["y0"])
b0 = p8[0]
page = [p for p in lp["pages"] if p["page_number"] == 8][0]
items = _build_items(page["text_items"], 8)
mh = _estimate_median_row_height(items)
gap_y0, gap_y1 = 0, b0["y0"]
gap_items = [
    it
    for it in items
    if gap_y0 - 2 <= it.get("y_mid", (it["y0"] + it["y1"]) / 2) <= gap_y1 + 2
]
blocks = _cluster_text_items_into_blocks(gap_items)
print(f"B0 y0={b0['y0']}, gap blocks={len(blocks)}")
for bl in blocks:
    t, f = _classify_gap_text(bl, None, b0, mh)
    ft = bl["full_text"].replace("\n", " | ")
    print(f"  y={bl['y0']:.0f}-{bl['y1']:.0f} -> {t},{f} | {ft[:80]}")
