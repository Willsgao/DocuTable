# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.pipeline import build_page
from codes.table_engine.source.liteparse_loader import load_page

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
result = build_page(page)
for e in result.entries:
    if e.table and any("21,674" in (c.text or "") for row in e.table.rows for c in row if c):
        t = e.table
        print("layout_id", t.layout_id)
        print("ncols", len(t.grid.ranges))
        print("ranges", [(round(r.x0), round(r.x1), r.role) for r in t.grid.ranges])
        print("meta grid", t.metadata.get("grid_inference"))
        for ri, row in enumerate(t.rows):
            if ri > 12:
                break
            print(ri, [c.text if c else "" for c in row])
        break
