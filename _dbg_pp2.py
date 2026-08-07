# -*- coding: utf-8 -*-
import json
from pathlib import Path

root = Path("data/mid_cache")
for d in sorted(root.iterdir()):
    if not d.is_dir() or not (d / "data.json").exists():
        continue
    try:
        tables = json.loads((d / "data.json").read_text(encoding="utf-8")).get("data", {}).get("tables") or []
    except Exception:
        continue
    for i, t in enumerate(tables):
        data = t.get("data") or []
        flat = " ".join(str(c) for r in data for c in (r or []))
        if "净息差" in flat or "总资产收益率" in flat:
            for r in data:
                j = " ".join(str(c) for c in (r or []))
                if "百分点" in j or "净息差" in j or "总资产收益率" in j or "1.28" in j:
                    print(d.name[:40], "idx", i, "p", t.get("page"), "->", r)
            print("---")
