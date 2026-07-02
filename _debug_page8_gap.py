from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
    _build_table_from_liteparse_fallback,
    hybrid_segment_tables,
)
from codes.table_validator.table_content_splitter import split_mixed_table_entry

parser = LiteParseParser()
lp = parser.parse("data/input_pdfs/test_subset8.pdf").to_dict()
bounds = detect_table_boundaries_from_liteparse(lp)
p8 = [b for b in bounds if b["page"] == 8]
print("before gap:", [(b["y0"], b["y1"]) for b in p8])

enriched, gaps = _capture_gap_text_items(p8, lp)
print("after gap boundaries:", [(b["y0"], b["y1"]) for b in enriched])
for i, b in enumerate(enriched):
    pre = b.get("_pre_header_items", [])
    print(f"  B{i} y={b['y0']}-{b['y1']} pre_header={len(pre)} desc={b.get('_gap_description', '')[:40]!r}")
    if pre:
        for it in pre[:8]:
            print(f"    pre y={it['y0']:.0f} | {it['text']}")
for g in gaps:
    if g.get("page") == 8:
        t = str(g.get("context_text", g.get("data", "")))[:60]
        print(f"GAP {g.get('type')} y={g.get('y0',0):.0f} | {t}")

t0 = _build_table_from_liteparse_fallback(enriched[0], lp)
print(f"\nB0 built: {len(t0['data'])} rows")
for ri, row in enumerate(t0["data"]):
    print(f"  {ri}: {' | '.join(str(c).strip() for c in row if str(c).strip())[:90]}")

segs = split_mixed_table_entry(t0)
print(f"\nsplit_mixed -> {len(segs)} segments")
for si, s in enumerate(segs):
    typ = s.get("type", "table")
    if typ == "text":
        print(f"  {si} TEXT: {str(s.get('context_text',''))[:60]}")
    else:
        d = s["data"]
        print(f"  {si} TABLE rows={len(d)}")
        for ri, row in enumerate(d[:5]):
            print(f"    {ri}: {row}")

# second boundary
if len(enriched) > 1:
    t1 = _build_table_from_liteparse_fallback(enriched[1], lp)
    print(f"\nB1 built: {len(t1['data'])} rows")
    segs1 = split_mixed_table_entry(t1)
    print(f"B1 split -> {len(segs1)}")
    for si, s in enumerate(segs1):
        typ = s.get("type", "table")
        if typ == "text":
            print(f"  {si} TEXT: {str(s.get('context_text',''))[:60]}")
        else:
            print(f"  {si} TABLE rows={len(s['data'])} cols={max(len(r) for r in s['data'])}")
