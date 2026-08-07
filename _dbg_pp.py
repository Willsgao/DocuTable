# -*- coding: utf-8 -*-
import json
from pathlib import Path
from copy import deepcopy

root = Path("data/mid_cache")
# find tables with 百分点 glued
for d in sorted(root.iterdir()):
    if not d.is_dir():
        continue
    dj = d / "data.json"
    if not dj.exists():
        continue
    try:
        tables = json.loads(dj.read_text(encoding="utf-8")).get("data", {}).get("tables") or []
    except Exception:
        continue
    for i, t in enumerate(tables):
        data = t.get("data") or []
        for r in data:
            joined = " ".join(str(c) for c in (r or []))
            if "个百分点" in joined and ("下降" in joined or "上升" in joined or "增加" in joined):
                # check glue in one cell
                for c in (r or []):
                    s = str(c or "")
                    if "个百分点" in s and any(ch.isdigit() for ch in s):
                        print("====", d.name, "idx", i, "page", t.get("page"))
                        print("row", r)
                        raise SystemExit
print("not found in mid_cache")
