"""Debug page 2 duplicate text vs table entries."""
from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import hybrid_segment_tables

parser = LiteParseParser()
lp = parser.parse("data/input_pdfs/test_subset8.pdf", target_pages=[2]).to_dict()
entries, _ = hybrid_segment_tables(lp, docx_tables=[])

p2 = [e for e in entries if e.get("page") == 2]
print(f"Page 2 entries: {len(p2)}\n")
for i, e in enumerate(p2):
    typ = e.get("type", "table")
    y0 = e.get("y0", 0)
    src = e.get("segment_source", e.get("extractor", ""))
    if typ == "text":
        txt = str(e.get("context_text") or e.get("data") or "")
        print(f"--- [{i}] TEXT y={y0:.0f} src={src} len={len(txt)} ---")
        print(txt[:200].replace("\n", " | "))
        if "建信理财" in txt and "1,499,121" in txt:
            print("  >>> DUPLICATE: issuance table data in text")
        if "现金、存款" in txt or "1,008,220" in txt:
            print("  >>> DUPLICATE: investment table data in text")
    else:
        data = e.get("data", [])
        flat = " ".join(str(c) for row in data for c in row)
        print(f"--- [{i}] TABLE y={y0:.0f} rows={len(data)} src={src} ---")
        print(f"  first row: {data[0] if data else []}")
        if "建信理财" in flat and "1,499,121" in flat:
            print("  >>> issuance table")
        if "现金、存款" in flat:
            print("  >>> investment table")
