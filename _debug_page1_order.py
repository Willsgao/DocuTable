from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    _build_table_from_liteparse_fallback,
    detect_table_boundaries_from_liteparse,
    _split_fused_table_by_structure,
    hybrid_segment_tables,
)
from codes.table_validator.table_content_splitter import (
    split_mixed_table_entry,
    is_embedded_paragraph_row,
)

parser = LiteParseParser()
lp = parser.parse("data/input_pdfs/test_subset8.pdf", target_pages=[1]).to_dict()
bounds = detect_table_boundaries_from_liteparse(lp)
b = [x for x in bounds if x["page"] == 1][0]
t = _build_table_from_liteparse_fallback(b, lp)
data = t["data"]
print("=== RAW liteparse table (28 rows) ===")
for i, row in enumerate(data):
    txt = " | ".join(str(c).strip() for c in row if str(c).strip())[:100]
    emb = " [EMB]" if is_embedded_paragraph_row(row) else ""
    print(f"{i:2d}: {txt}{emb}")

print("\n=== After structure split ===")
parts = _split_fused_table_by_structure([t], lp)
for pi, pt in enumerate(parts):
    print(f"\nPART {pi} y0={pt.get('y0')} y1={pt.get('y1')} rows={len(pt.get('data', []))}")
    for i, row in enumerate(pt["data"]):
        txt = " | ".join(str(c).strip() for c in row if str(c).strip())[:90]
        emb = " [EMB]" if is_embedded_paragraph_row(row) else ""
        print(f"  {i:2d}: {txt}{emb}")

print("\n=== After table_content_split on each part ===")
for pi, pt in enumerate(parts):
    segs = split_mixed_table_entry(pt)
    print(f"\nPART {pi} -> {len(segs)} segments")
    for si, s in enumerate(segs):
        typ = s.get("type", "table")
        y0, y1 = s.get("y0"), s.get("y1")
        if typ == "text":
            txt = (s.get("context_text") or "")[:70]
            print(f"  seg{si} text y={y0:.1f}-{y1:.1f}: {txt}")
        else:
            d = s.get("data", [])
            r0 = " | ".join(str(c).strip() for c in d[0] if str(c).strip())[:60] if d else ""
            print(f"  seg{si} table y={y0:.1f}-{y1:.1f} rows={len(d)} | {r0}")

print("\n=== Final hybrid output page 1 ===")
entries, _ = hybrid_segment_tables(lp, docx_tables=[])
for i, e in enumerate(entries):
    if e.get("page") != 1:
        continue
    typ = e.get("type", "table")
    y0, y1 = e.get("y0", 0), e.get("y1", 0)
    src = e.get("segment_source", "")
    if typ == "text":
        txt = (e.get("context_text") or e.get("data") or "")[:70].replace("\n", " ")
        print(f"{i} text  y={y0:.0f}-{y1:.0f} {src} | {txt}")
    else:
        d = e.get("data", [])
        r0 = " | ".join(str(c).strip() for c in d[0] if str(c).strip())[:50] if d else ""
        print(f"{i} table y={y0:.0f}-{y1:.0f} rows={len(d)} {src} | {r0}")
