from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _build_table_from_liteparse_fallback,
    _split_fused_table_by_structure,
    _capture_gap_text_items,
    hybrid_segment_tables,
)
from codes.table_validator.table_content_splitter import (
    split_mixed_table_entry,
    is_embedded_paragraph_row,
)

parser = LiteParseParser()
lp = parser.parse("data/input_pdfs/test_subset8.pdf", target_pages=[2]).to_dict()
bounds = detect_table_boundaries_from_liteparse(lp)
b = [x for x in bounds if x["page"] == 2][0]
print(f"boundary y={b['y0']}-{b['y1']}")

t = _build_table_from_liteparse_fallback(b, lp)
data = t["data"]
print(f"\nRAW table {len(data)} rows x {t.get('cols')} cols")
for i, row in enumerate(data):
    txt = " | ".join(str(c).strip() for c in row if str(c).strip())[:90]
    emb = " [EMB]" if is_embedded_paragraph_row(row) else ""
    print(f"{i:2d}: {txt}{emb}")

parts = _split_fused_table_by_structure([t], lp)
print(f"\n=== structure split -> {len(parts)} parts ===")
for pi, pt in enumerate(parts):
    d = pt["data"]
    print(f"PART{pi} y0={pt.get('y0'):.0f} rows={len(d)}")
    for i, row in enumerate(d[:5]):
        print(f"  {i}: {' | '.join(str(c).strip() for c in row if str(c).strip())[:80]}")
    if len(d) > 5:
        print("  ...")
        for i, row in enumerate(d[-2:], len(d)-2):
            print(f"  {i}: {' | '.join(str(c).strip() for c in row if str(c).strip())[:80]}")

print("\n=== table_content_split per part ===")
for pi, pt in enumerate(parts):
    segs = split_mixed_table_entry(pt)
    print(f"PART{pi} -> {len(segs)} segs")
    for si, s in enumerate(segs):
        typ = s.get("type", "table")
        if typ == "text":
            txt = str(s.get("context_text", ""))[:120]
            has_tbl = any(k in txt for k in ("建信理财", "1,499,121", "现金、存款", "1,008,220"))
            print(f"  seg{si} TEXT y={s.get('y0'):.0f} tableish={has_tbl} | {txt[:80]}")
        else:
            flat = " ".join(str(c) for row in s.get("data",[]) for c in row)[:60]
            print(f"  seg{si} TABLE y={s.get('y0'):.0f} rows={len(s.get('data',[]))} | {flat}")

bounds2, gaps = _capture_gap_text_items(bounds, lp)
print(f"\n=== gap entries page 2: {len(gaps)} ===")
for g in gaps:
    if g.get("page") != 2:
        continue
    txt = str(g.get("context_text", g.get("data", "")))[:100]
    has_tbl = any(k in txt for k in ("建信理财", "1,499,121", "现金、存款", "期数", "金额"))
    print(f"  {g.get('type')} y={g.get('y0',0):.0f} tableish={has_tbl} | {txt}")

print("\n=== FINAL all page2 ===")
entries, _ = hybrid_segment_tables(lp, docx_tables=[])
for i, e in enumerate(entries):
    if e.get("page") != 2:
        continue
    typ = e.get("type", "table")
    src = e.get("segment_source", "")
    if typ == "text":
        txt = str(e.get("context_text", e.get("data", "")))
        has_tbl = any(k in txt for k in ("建信理财", "1,499,121", "现金、存款", "期数 金额", "676"))
        print(f"{i} TEXT src={src} tableish={has_tbl} y={e.get('y0'):.0f}")
        if has_tbl:
            print(f"   {txt[:150]}")
    else:
        print(f"{i} TABLE src={src} rows={len(e.get('data',[]))} y={e.get('y0'):.0f}")
