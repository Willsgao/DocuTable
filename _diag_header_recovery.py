import json
from pathlib import Path
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
    _build_table_from_liteparse_fallback,
    ensure_table_has_header_band,
    table_data_has_header_band,
)
from codes.table_validator.table_content_splitter import (
    is_pillar_disclosure_table_body,
    _has_pillar_grid_header,
)

lp = json.loads(
    Path(
        r"data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024"
        r"年度资本管理第三支柱信息披露报告/liteparse/pages.json"
    ).read_text(encoding="utf-8")
)
bounds = detect_table_boundaries_from_liteparse(lp)
enriched, gaps = _capture_gap_text_items(bounds, lp)

for pg in [9, 10]:
    b = [x for x in enriched if x["page"] == pg][0]
    t = _build_table_from_liteparse_fallback(b, lp)
    print(f"\n=== P{pg} BEFORE header recovery ===")
    print("has_header_band:", table_data_has_header_band(t["data"]))
    print("pillar_grid:", _has_pillar_grid_header(t["data"]))
    print("pillar:", is_pillar_disclosure_table_body(t["data"]))
    print("first 5 rows:")
    for i, r in enumerate(t["data"][:5]):
        print(f"  {i}: {r}")

    ok = ensure_table_has_header_band(t, lp)
    print(f"header recovery: {ok}")
    print("AFTER has_header_band:", table_data_has_header_band(t["data"]))
    print("AFTER pillar:", is_pillar_disclosure_table_body(t["data"]))
    if ok:
        for i, r in enumerate(t["data"][:8]):
            print(f"  {i}: {r}")
