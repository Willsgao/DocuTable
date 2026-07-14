# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 325)
for si, scope in enumerate(plan_page_scopes(page).scopes):
    print(f"=== scope {si} region={scope.region_index} items={len(scope.items)} ===")
    for it in sorted(scope.items, key=lambda i: (i.bbox.y0, i.bbox.x0)):
        t = it.text.strip()
        if any(k in t for k in ("阶段", "贷款", "2024", "珠江", "82,590", "48,731", "损失")):
            print(f"  y={it.bbox.y0:.0f} x0={it.bbox.x0:.0f} x1={it.bbox.x1:.0f} {t!r}")
