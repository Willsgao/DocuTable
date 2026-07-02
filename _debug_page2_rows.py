from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _build_table_from_liteparse_fallback,
    _split_fused_table_by_structure,
)
from codes.table_validator.table_content_splitter import (
    split_mixed_table_entry,
    is_embedded_paragraph_row,
    is_main_table_data_row,
)

parser = LiteParseParser()
# Full doc parse like production
lp = parser.parse("data/input_pdfs/test_subset8.pdf").to_dict()
bounds = detect_table_boundaries_from_liteparse(lp)
b = [x for x in bounds if x["page"] == 2][0]
print(f"P2 boundary y={b['y0']}-{b['y1']}")

t = _build_table_from_liteparse_fallback(b, lp)
data = t["data"]
print(f"RAW {len(data)} rows")
for i, row in enumerate(data):
    txt = " | ".join(str(c).strip() for c in row if str(c).strip())[:95]
    emb = " [EMB]" if is_embedded_paragraph_row(row) else ""
    data_row = " [DATA]" if is_main_table_data_row(row) else ""
    print(f"{i:2d}: {txt}{emb}{data_row}")

parts = _split_fused_table_by_structure([t], lp)
print(f"\nstructure -> {len(parts)} parts")
for pi, pt in enumerate(parts):
    d = pt["data"]
    print(f"\nPART{pi} y0={pt.get('y0'):.0f} rows={len(d)}")
    segs = split_mixed_table_entry(pt)
    for si, s in enumerate(segs):
        typ = s.get("type", "table")
        if typ == "text":
            txt = str(s.get("context_text", ""))
            print(f"  seg{si} TEXT ({len(txt)} chars)")
            print(f"    {txt[:120].replace(chr(10),' | ')}")
            if "建信理财" in txt and "1,499,121" in txt:
                print("    *** ISSUANCE TABLE IN TEXT ***")
            if "现金、存款" in txt or ("建信理财" in txt and "占比" in txt):
                print("    *** INVESTMENT TABLE IN TEXT ***")
        else:
            flat = " ".join(str(c) for row in s.get("data",[]) for c in row)
            print(f"  seg{si} TABLE rows={len(s.get('data',[]))} | {flat[:80]}")
