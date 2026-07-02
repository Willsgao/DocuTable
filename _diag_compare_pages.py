import json
from pathlib import Path
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
    _build_table_from_liteparse_fallback,
)
from codes.table_validator.table_content_splitter import is_pillar_disclosure_table_body

lp = json.loads(
    Path(
        r"data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024"
        r"年度资本管理第三支柱信息披露报告/liteparse/pages.json"
    ).read_text(encoding="utf-8")
)
bounds = detect_table_boundaries_from_liteparse(lp)
enriched, _ = _capture_gap_text_items(bounds, lp)

for pg in [5, 6, 9, 10, 16, 41]:
    bs = [x for x in enriched if x["page"] == pg]
    print(f"=== P{pg} regions={len(bs)} ===")
    for i, b in enumerate(bs):
        t = _build_table_from_liteparse_fallback(b, lp)
        d = t["data"] if t else []
        pillar = is_pillar_disclosure_table_body(d) if d else False
        gd = (b.get("_gap_description") or "").replace("\n", "|")[:100]
        print(
            f"  r{i} y0={b['y0']:.0f} rows={len(d)} pillar={pillar} "
            f"pre={len(b.get('_pre_header_items', []))}"
        )
        if d:
            print(f"    row0: {d[0][:4]}")
        if gd:
            print(f"    gap: {gd}...")
