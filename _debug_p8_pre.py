from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
)

lp = LiteParseParser().parse("data/input_pdfs/test_subset8.pdf").to_dict()
bounds = detect_table_boundaries_from_liteparse(lp)
e, gaps = _capture_gap_text_items(bounds, lp)
for b in e:
    if b["page"] != 8:
        continue
    pre = b.get("_pre_header_items", [])
    print(f"P8 y={b['y0']:.0f}-{b['y1']:.0f} pre={len(pre)}")
    for it in pre:
        print(f"  y={it['y0']:.0f} | {it['text']!r}")
