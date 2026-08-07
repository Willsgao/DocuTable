# -*- coding: utf-8 -*-
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

from codes.reconstruct.grid_nucleus.pipeline import restore_table_grid

raw = json.loads(
    Path(
        r"data/mid_cache/2025A-招商银行股份有限公司-报告-2026-04-01/data.json"
    ).read_text(encoding="utf-8")
)

for i in [1877, 1884, 1889, 1894, 1897]:
    t = raw["data"]["tables"][i]
    gn = t.get("_grid_nucleus") or {}
    print("===", i, "page", t.get("page"), "method", gn.get("method"), "ok", gn.get("ok"))
    for ri, r in enumerate(t.get("data") or []):
        print(ri, r)
    words = t.get("_source_words") or []
    print("nwords", len(words))
    # print leftish words
    for w in words[:40]:
        print(f"  x0={float(w['x0']):.1f} {w['text']!r}")
    print()

# search any glued serial+text in all tables
import re
pat = re.compile(r"^\d{1,3}[a-zA-Z]?\s+[\u4e00-\u9fff]")
n = 0
for i, t in enumerate(raw["data"]["tables"]):
    for r in t.get("data") or []:
        for c in r:
            s = str(c or "").strip()
            if pat.match(s):
                print("GLUE", i, "page", t.get("page"), repr(s)[:60])
                n += 1
                if n >= 20:
                    break
        if n >= 20:
            break
    if n >= 20:
        break
print("glue_count_sample_done", n)
