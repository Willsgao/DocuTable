from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    _build_items,
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
    _build_table_from_liteparse_fallback,
    hybrid_segment_tables,
)

lp = LiteParseParser().parse("data/input_pdfs/test_subset8.pdf", target_pages=[8]).to_dict()
p8page = [p for p in lp["pages"] if p["page_number"] == 8][0]
items = _build_items(p8page["text_items"])
print("=== year header items ===")
for it in items:
    t = it.get("text", "").strip()
    if t in ("2024年", "2023年") or t.startswith("2024年") and len(t) <= 6:
        print(f"  y={it['y0']:.0f} x={it['x0']:.0f} | {t!r}")

p8 = [b for b in detect_table_boundaries_from_liteparse(lp) if b["page"] == 8]
e, gaps = _capture_gap_text_items(p8, lp)
for i, b in enumerate(e):
    pre = b.get("_pre_header_items", [])
    print(f"B{i} y={b['y0']:.0f}-{b['y1']:.0f} pre={len(pre)}")
    for it in pre:
        if "2024" in it.get("text", "") or "2023" in it.get("text", ""):
            print(f"  pre: y={it['y0']:.0f} | {it['text']!r}")

t0 = _build_table_from_liteparse_fallback(e[0], lp)
print("\nB0 raw first rows:")
for ri, row in enumerate(t0["data"][:5]):
    print(f"  {ri}: {row}")

entries, _ = hybrid_segment_tables(lp, docx_tables=[])
for t in entries:
    if t.get("page") == 8 and t.get("type") == "table":
        d = t["data"]
        flat = " ".join(str(c) for row in d for c in row)
        tag = "2024-ECL" if "23,016" in flat else ("2023-ECL" if "33,304" in flat else "debt")
        print(f"\n=== {tag} rows={len(d)} ===")
        for ri, row in enumerate(d[:5]):
            print(f"  {ri}: {row}")
