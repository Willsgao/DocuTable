from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import _build_items

parser = LiteParseParser()
lp = parser.parse("data/input_pdfs/test_subset8.pdf", target_pages=[8]).to_dict()
p8 = [p for p in lp["pages"] if p["page_number"] == 8][0]
items = _build_items(p8["text_items"], 8)
gap_items = [it for it in items if 310 <= it.get("y_mid", it["y0"]) <= 405]
gap_items.sort(key=lambda x: x["y0"])
print(f"Gap items y=310-405: {len(gap_items)}")
for it in gap_items:
    print(f"  y={it['y0']:.0f} x={it['x0']:.0f} | {it['text']!r}")
