"""Debug page 8 ECL table split."""
from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _build_table_from_liteparse_fallback,
    _split_fused_table_by_structure,
    hybrid_segment_tables,
)
from codes.table_validator.table_content_splitter import (
    split_mixed_table_entry,
    is_embedded_paragraph_row,
)

parser = LiteParseParser()
lp = parser.parse("data/input_pdfs/test_subset8.pdf", target_pages=[8]).to_dict()

bounds = detect_table_boundaries_from_liteparse(lp)
p8_bounds = [b for b in bounds if b["page"] == 8]
print(f"Page 8 boundaries: {len(p8_bounds)}")
for i, b in enumerate(p8_bounds):
    print(f"  B{i}: y={b['y0']:.0f}-{b['y1']:.0f} x={b.get('x0')}-{b.get('x1')}")

for bi, b in enumerate(p8_bounds):
    t = _build_table_from_liteparse_fallback(b, lp)
    if not t:
        print(f"\nB{bi}: build failed")
        continue
    data = t["data"]
    print(f"\n=== B{bi} raw {len(data)} rows x {t.get('cols')} cols ===")
    for ri, row in enumerate(data):
        txt = " | ".join(str(c).strip() for c in row if str(c).strip())[:100]
        emb = " [EMB]" if is_embedded_paragraph_row(row) else ""
        print(f"  {ri:2d}: {txt}{emb}")

print("\n\n=== FULL hybrid output page 8 ===")
entries, _ = hybrid_segment_tables(lp, docx_tables=[])
p8 = [e for e in entries if e.get("page") == 8]
for i, e in enumerate(p8):
    typ = e.get("type", "table")
    src = e.get("segment_source", e.get("extractor", ""))
    y0 = e.get("y0", 0)
    if typ == "text":
        txt = str(e.get("context_text") or e.get("data") or "")[:80].replace("\n", " ")
        print(f"[{i}] TEXT y={y0:.0f} src={src} | {txt}")
    else:
        d = e.get("data", [])
        print(f"[{i}] TABLE y={y0:.0f} rows={len(d)} cols={max(len(r) for r in d) if d else 0} src={src}")
        for ri, row in enumerate(d[:4]):
            print(f"      {ri}: {row}")
        if len(d) > 4:
            print(f"      ... ({len(d)} rows total)")
            for ri, row in enumerate(d[-2:], len(d)-2):
                print(f"      {ri}: {row}")
