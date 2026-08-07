# -*- coding: utf-8 -*-
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

from codes.reconstruct.grid_nucleus.column_infer import (
    assign_nuclei_to_slots,
    infer_column_slots,
    is_serial_nucleus,
)
from codes.reconstruct.grid_nucleus.preprocess import preprocess_words
from codes.reconstruct.grid_nucleus.row_cluster import cluster_rows
from codes.reconstruct.grid_nucleus.types import Nucleus

raw = json.loads(
    Path(
        r"data/mid_cache/2025A-招商银行股份有限公司-报告-2026-04-01/data.json"
    ).read_text(encoding="utf-8")
)

for ti in (1877, 1884):
    t = deepcopy(raw["data"]["tables"][ti])
    words = t["_source_words"]
    nuclei = preprocess_words(words)
    rows = cluster_rows(nuclei)
    n_cols, centers = infer_column_slots(rows)
    print("===", ti, "n_cols", n_cols, "centers", [round(c, 1) for c in centers])
    assign_nuclei_to_slots(rows, centers)
    # find rows with serial-like
    for r in rows:
        ser = [n for n in r.nuclei if is_serial_nucleus(n) or __import__("re").match(r"^\d{1,3}[a-zA-Z]?$", n.text or "")]
        if not ser:
            # also check digit texts
            ser = [n for n in r.nuclei if __import__("re").match(r"^\d{1,3}[a-zA-Z]?$", str(n.text or "").strip())]
        if not ser:
            continue
        items = [(n.col_id, n.text, round(n.x0, 1)) for n in sorted(r.nuclei, key=lambda x: x.x0)]
        # only print if serial shares col with chinese
        for n in ser:
            peers = [m for m in r.nuclei if m.col_id == n.col_id and m is not n]
            if any(__import__("re").search(r"[\u4e00-\u9fff]", m.text or "") for m in peers):
                print(" SAME_COL", items)
                break
        else:
            # print a few ok ones
            if any(n.text in ("1", "3", "24a") for n in ser):
                print(" OK", items[:6])
