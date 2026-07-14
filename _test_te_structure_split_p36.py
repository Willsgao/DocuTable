# -*- coding: utf-8
"""P36 结构分裂：LR1+脚注+LR2 巨表应拆成两表 + 中间文本。"""

from __future__ import annotations

import copy
import sys

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.models import DocumentEntry, StructuredTable
from codes.table_engine.pipeline import build_page_by_number
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.source.liteparse_loader import load_liteparse_document
from codes.table_engine.split.structure_split import (
    apply_structure_split,
    find_structure_break_row,
)
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.table_access import dense_rows as dr

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _mega_lr1_lr2_table() -> StructuredTable:
    page = load_liteparse_document(DEFAULT_PILLAR_CACHE).get_page(36)
    scopes = plan_page_scopes(page).scopes
    t1 = build_table_from_scope(scopes[0])
    t2 = build_table_from_scope(scopes[1])
    merged = copy.deepcopy(t1)
    merged.rows = list(t1.rows) + list(t2.rows)
    merged.y1 = max(t1.y1, t2.y1)
    merged.x1 = max(t1.x1, t2.x1)
    return merged


def test_mega_table_break_signals() -> None:
    print("--- P36 巨表断裂信号 ---")
    rows = dr(_mega_lr1_lr2_table())
    br = find_structure_break_row(rows)
    check("break row found", br > 0, f"got {br}")
    check("break before LR2 header", br >= 14, f"got {br}")


def test_mega_table_split_entries() -> None:
    print("--- P36 巨表结构分裂 ---")
    mega = _mega_lr1_lr2_table()
    raw = [
        DocumentEntry(kind="table", page=36, y0=mega.y0, y1=mega.y1, table=mega),
    ]
    entries = apply_structure_split(raw)
    tables = [e for e in entries if e.kind == "table"]
    texts = [e for e in entries if e.kind == "text"]
    check(">=2 tables", len(tables) >= 2, f"got {len(tables)}")
    check(">=1 text", len(texts) >= 1, f"got {len(texts)}")
    if tables:
        flat0 = " ".join(" ".join(r) for r in dr(tables[0].table))
        check("first table LR1 only", "并表总资产" in flat0 and "LR2" not in flat0)
        check("no footnote in table1", "1．并表总资产指" not in flat0)


def test_p36_pipeline() -> None:
    print("--- P36 pipeline ---")
    result = build_page_by_number(DEFAULT_PILLAR_CACHE, 36)
    tables = [e for e in result.entries if e.kind == "table"]
    check("two tables", len(tables) == 2, f"got {len(tables)}")
    for t in tables:
        flat = " ".join(" ".join(r) for r in dr(t.table))
        if "并表总资产" in flat:
            check("LR1 no LR2 caption", "表 15" not in flat and "LR2" not in flat)
            check("LR1 no footnote", "1．并表总资产指" not in flat)
        if "40,382,455" in flat:
            check("LR2 has derivative", "172,714" in flat)


def main() -> int:
    test_mega_table_break_signals()
    test_mega_table_split_entries()
    test_p36_pipeline()
    print(f"\n=== structure split P36: {passed} passed, {failed} failed ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
