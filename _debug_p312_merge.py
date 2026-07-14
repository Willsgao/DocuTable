# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
for si, scope in enumerate(plan_page_scopes(page).scopes):
    dicts = source_items_to_dicts(scope.items)
    rows = refine_clustered_rows(cluster_items_by_y(dicts, use_dynamic_threshold=True))
    print(f"=== scope {si} items={len(scope.items)} rows={len(rows)} ===")
    for row in rows:
        y0 = min(float(it.get("y0", 0)) for it in row.get("items", []))
        if 280 < y0 < 360 or y0 > 300:
            texts = [it["text"] for it in sorted(row.get("items", []), key=lambda d: d["x0"])]
            if any("业务及管理费" in t or "资产负债表" in t or "存放同业" in t or "37,494" in t for t in texts):
                print(f"y={y0:.0f}", texts[:8], "..." if len(texts)>8 else "")
