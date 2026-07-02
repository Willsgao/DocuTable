from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    hybrid_segment_tables,
)

pdf = "data/input_pdfs/test_subset8.pdf"
lp = LiteParseParser().parse(pdf, target_pages=[6]).to_dict()
p6 = lp["pages"][0]
print("=== liteparse ===")
print("is_table_page:", p6.get("is_table_page"))
print("table_regions:", len(p6.get("table_regions", [])))
for i, r in enumerate(p6.get("table_regions", [])):
    print(
        f"  R{i}: y={r.get('y0'):.0f}-{r.get('y1'):.0f} "
        f"conf={r.get('confidence')}"
    )

bounds = detect_table_boundaries_from_liteparse(lp)
p6b = [b for b in bounds if b["page"] == 6]
print("boundaries:", len(p6b))
for b in p6b:
    cap = b.get("caption", "")[:40]
    print(f"  y={b['y0']:.0f}-{b['y1']:.0f} caption={cap!r}")

docx = [{"page": 6, "data": [["x"] * 5] * 3}]
seg, _ = hybrid_segment_tables(lp, docx_tables=docx)
tables = [e for e in seg if e.get("page") == 6 and e.get("type") == "table"]
texts = [e for e in seg if e.get("page") == 6 and e.get("type") != "table"]
print("=== hybrid page6 ===")
print("table entries:", len(tables))
for i, t in enumerate(tables):
    d = t.get("data", [])
    row0 = " | ".join(str(c) for c in d[0]) if d else ""
    print(f"  T{i}: rows={len(d)} cols={max(len(r) for r in d) if d else 0}")
    print(f"       y0={t.get('y0', 0):.0f} row0={row0[:70]!r}")
    # show section-like rows
    for ri, row in enumerate(d):
        joined = " ".join(str(c).strip() for c in row if str(c).strip())
        if any(k in joined for k in ("流动性", "净稳定", "杠杆率", "调整后")):
            print(f"       row{ri}: {joined[:60]}")

print("non-table entries:", len(texts))
for e in texts:
    blob = str(e.get("context_text") or e.get("data") or "")[:60]
    print(f"  {e.get('type')} y0={e.get('y0', 0):.0f} {blob!r}")
