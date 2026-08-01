# -*- coding: utf-8 -*-
"""P29（五）利息净收入不得因 region 索引撞号被丢弃。"""

import json
from pathlib import Path

from codes.liteparse_extractor import cache_manager as cm
from codes.table_engine.pipeline import build_page
from codes.table_engine.scope.gap_capture import (
    _expand_regions_split_by_subsection,
    plan_page_scopes,
)
from codes.table_engine.source.liteparse_loader import load_liteparse_document
from codes.table_engine.table_access import dense_rows


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print(f"  OK  {name}")


def _load_p29():
    f = next(Path("data/mid_cache").glob("*601838*/data.json"))
    obj = json.loads(f.read_text(encoding="utf-8"))
    pdf = obj["pdf_info"]["path"]
    pages_json = cm._get_cache_dir(pdf) / "pages.json"
    if not pages_json.exists():
        raise SystemExit(f"skip: no pages.json at {pages_json}")
    return load_liteparse_document(pages_json).get_page(29)


if __name__ == "__main__":
    print("=== P29 interest income scope ===")
    page = _load_p29()
    check("liteparse 有 2 个表区", len(page.table_regions) >= 2)

    regions = sorted(enumerate(page.table_regions), key=lambda p: p[1].y0)
    expanded = _expand_regions_split_by_subsection(page, regions)
    ids = [i for i, _ in expanded]
    check("拆分后 region_index 不重复", len(ids) == len(set(ids)))

    plan = plan_page_scopes(page)
    check("scope 数 >= 3", len(plan.scopes) >= 3)
    bottom = page.table_regions[-1]
    covered = False
    for s in plan.scopes:
        if not s.region:
            continue
        if abs(s.region.y0 - bottom.y0) < 8 and abs(s.region.y1 - bottom.y1) < 30:
            covered = True
            break
        if s.region.y0 <= bottom.y0 + 20 and s.region.y1 >= bottom.y1 - 20:
            covered = True
            break
    check("底部利息净收入 region 有 scope", covered)

    result = build_page(page)
    check("建表数 >= 3", len(result.tables) >= 3)
    blobs = []
    for t in result.tables:
        d = dense_rows(t)
        blobs.append("|".join(str(c) for r in d[:6] for c in (r or [])))
    joined = "\n".join(blobs)
    check("含存放中央银行款项", "存放中央银行" in joined or "899,467" in joined)
    check("含利息收入小节", "利息收入" in joined)
    print("ALL PASS")
