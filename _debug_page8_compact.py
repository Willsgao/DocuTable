from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
    _build_table_from_liteparse_fallback,
)
from codes.table_validator.header_boundary import compact_table_spacer_rows_and_columns

lp = LiteParseParser().parse("data/input_pdfs/test_subset8.pdf").to_dict()
p8 = [b for b in detect_table_boundaries_from_liteparse(lp) if b["page"] == 8]
e, _ = _capture_gap_text_items(p8, lp)
t = _build_table_from_liteparse_fallback(e[0], lp)
data = t["data"]
print("BEFORE compact row0:", data[0])
print("width", max(len(r) for r in data))
c = compact_table_spacer_rows_and_columns(data)
print("AFTER compact row0:", c[0])
print("width", max(len(r) for r in c))
