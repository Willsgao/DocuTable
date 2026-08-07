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

for ti in (1877, 1884):
    t = deepcopy(raw["data"]["tables"][ti])
    res = restore_table_grid(t)
    print("===", ti, "ok", res.ok, "n_cols", res.n_cols, "method", res.method)
    # show rows that should have serial split
    for r in (res.data or []):
        flat = " | ".join(str(c) for c in r)
        if any(
            str(c).strip() in ("1", "3", "24a", "14a", "29a")
            or str(c).strip().startswith("24")
            for c in r
        ) or "稳定存款" in flat or "杠杆率" in flat[:20]:
            # only print interesting
            cells = [str(c).strip() for c in r]
            if any(c in ("1", "3", "24a", "24", "29a", "14") for c in cells) or "稳定存款" in flat:
                print(cells[:6])
    # glue check
    import re
    glued = [
        str(c)
        for r in (res.data or [])
        for c in r
        if re.match(r"^\d{1,3}[a-zA-Z]?\s+[\u4e00-\u9fff]", str(c or "").strip())
    ]
    print("glued", glued[:8], "n=", len(glued))
    print()
