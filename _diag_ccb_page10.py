"""诊断单页为何未走披露表保护。"""
import json
from pathlib import Path

from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _build_table_from_liteparse_fallback,
)
from codes.table_validator.table_content_splitter import (
    is_pillar_disclosure_table_body,
    find_pillar_table_body_start_row,
    split_mixed_table_entry,
)

CACHE = Path(
    r"data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024"
    r"年度资本管理第三支柱信息披露报告/liteparse/pages.json"
)
lp = json.loads(CACHE.read_text(encoding="utf-8"))

for pn in (5, 6, 9, 10, 11, 16, 41):
    bounds = [b for b in detect_table_boundaries_from_liteparse(lp) if b["page"] == pn]
    if not bounds:
        print(f"P{pn}: no boundary")
        continue
    b = bounds[0]
    t = _build_table_from_liteparse_fallback(b, lp)
    if not t:
        print(f"P{pn}: no table built")
        continue
    data = t.get("data", [])
    bs = find_pillar_table_body_start_row(data)
    body = data[bs:]
    ok = is_pillar_disclosure_table_body(body)
    ok_full = is_pillar_disclosure_table_body(data)
    parts = split_mixed_table_entry(t)
    nt = sum(1 for p in parts if p.get("type") == "table")
    nx = sum(1 for p in parts if p.get("type") in ("text", "paragraph"))
    print(f"P{pn}: rows={len(data)} body_start={bs} pillar_body={ok} pillar_full={ok_full} "
          f"split→{nt}tbl+{nx}txt")
    if pn == 10 and not ok:
        print("  first 8 rows:")
        for i, r in enumerate(data[:8]):
            print(f"    {i}: {[str(c)[:30] for c in r]}")
        print("  body first 5:")
        for i, r in enumerate(body[:5]):
            print(f"    {bs+i}: {[str(c)[:30] for c in r]}")
