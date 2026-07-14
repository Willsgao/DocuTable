# -*- coding: utf-8 -*-
from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.pipeline import build_page, primary_table
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.table_access import dense_rows

page = load_page(DEFAULT_PILLAR_CACHE, 13)
r = build_page(page)
t = primary_table(r)
rows = dense_rows(t)
print("cols", t.grid.col_count, "rows", len(rows))
print("col_ranges", t.grid.col_ranges if hasattr(t.grid, "col_ranges") else t.metadata)
for i in range(min(6, len(rows))):
    print("hdr", i, rows[i][:5])
print("--- body ---")
for ri in range(6, min(35, len(rows))):
    r0 = rows[ri]
    c0 = (r0[0] or "")[:25]
    c1 = (r0[1] or "")[:35] if len(r0) > 1 else ""
    merged = c0 and c1 == "" and any(ch.isdigit() for ch in c0[:3])
    flag = " MERGED?" if merged and " " in c0.strip()[:5] else ""
    print(f"row{ri}: [{c0}] | [{c1}]{flag}")

# raw items for rows 3,4,8
print("--- raw items x for row labels ---")
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.item_bridge import source_items_to_dicts

region = page.table_regions[0]
items = [it for it in page.items if region.x0 - 10 <= it.bbox.cx <= region.x1 + 10
         and region.y0 - 10 <= it.bbox.cy <= region.y1 + 30]
items.sort(key=lambda x: (x.y_mid, x.x0))
dicts = source_items_to_dicts(items)
rows_d = cluster_items_by_y(dicts, use_dynamic_threshold=True)
for target in ("3", "1", "9", "14"):
    for row in rows_d:
        texts = [d.get("text", "") for d in row.get("items", [])]
        if any(str(t).strip() == target for t in texts):
            parts = [(d.get("text"), round(d.get("x0", 0), 1), round(d.get("x1", 0), 1)) for d in row.get("items", [])]
            print(f"  serial {target}:", parts)
            break
