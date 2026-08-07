# -*- coding: utf-8 -*-
import json
from pathlib import Path

from codes.table_engine.split.structure_split import find_structure_break_row
from codes.format_corrector.structure_pre_split import expand_tables_with_structure_split
from codes.reconstruct.grid_nucleus.pipeline import restore_table_grid, apply_grid_to_table

raw = json.loads(Path("data/mid_cache/page_016/data.json").read_text(encoding="utf-8"))

# find tables mentioning 信用成本 or 不良贷款率
hits = []


def walk(obj, path=""):
    if isinstance(obj, dict):
        data = obj.get("data")
        if isinstance(data, list) and data and isinstance(data[0], list):
            flat = " ".join(str(c) for r in data for c in r)
            if "不良贷款率" in flat or "信用成本" in flat or "资产质量" in flat:
                hits.append(obj)
        for k, v in obj.items():
            walk(v, path + "." + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:200]):
            walk(v, path + f"[{i}]")


walk(raw)
print("hit tables", len(hits))
for i, t in enumerate(hits[:6]):
    data = t.get("data") or []
    print(f"\n=== table {i} page={t.get('page')} cols={t.get('cols')} rows={len(data)} cat={t.get('table_category')} ===")
    print("bbox", t.get("x0"), t.get("y0"), t.get("x1"), t.get("y1"))
    print("words", len(t.get("_source_words") or []))
    for r in data[:12]:
        print(" ", r)
    br = find_structure_break_row(data)
    print(" break_row", br)

# check liteparse regions
lp = json.loads(Path("data/mid_cache/page_016/liteparse/pages.json").read_text(encoding="utf-8"))
page = (lp.get("pages") or [lp])[0]
regs = page.get("table_regions") or []
print("\nliteparse regions", len(regs))
for ri, reg in enumerate(regs):
    rt = (reg.get("region_text") or "")[:80].replace("\n", " ")
    print(f"  R{ri} y={reg.get('y0'):.1f}-{reg.get('y1'):.1f} {rt!r}")
