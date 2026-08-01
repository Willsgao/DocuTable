# -*- coding: utf-8 -*-
"""P14：利润表指标区与资产负债表指标区不得误并为巨表（否则丢 2022 列）。"""

import json
from pathlib import Path

from codes.liteparse_extractor import cache_manager as cm
from codes.table_engine.pipeline import build_page
from codes.table_engine.scope.gap_capture import (
    _expand_regions_split_by_subsection,
    _merge_continuation_regions,
    plan_page_scopes,
)
from codes.table_engine.source.liteparse_loader import load_liteparse_document
from codes.table_engine.table_access import dense_rows


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print(f"  OK  {name}")


def _page():
    f = next(Path("data/mid_cache").glob("*601838*/data.json"))
    obj = json.loads(f.read_text(encoding="utf-8"))
    pdf = obj["pdf_info"]["path"]
    pages_json = cm._get_cache_dir(pdf) / "pages.json"
    if not pages_json.exists():
        raise SystemExit(f"skip: {pages_json}")
    return load_liteparse_document(pages_json).get_page(14)


if __name__ == "__main__":
    print("=== P14 no false region merge ===")
    page = _page()
    check("至少 3 个 liteparse 表区", len(page.table_regions) >= 3)

    exp = _expand_regions_split_by_subsection(
        page, sorted(enumerate(page.table_regions), key=lambda p: p[1].y0)
    )
    # 利润表区(1000) 不得吞并 资产负债表区(2000)
    mi, mr, ni, mc, _ = _merge_continuation_regions(page, exp, 1)
    check("不把 region1+2 合并", mc == 1 and ni == 2)
    check("合并后高度未跨到 700+", mr.y1 < 620)

    plan = plan_page_scopes(page)
    check("scope>=3", len(plan.scopes) >= 3)

    res = build_page(page)
    found = False
    for t in res.tables:
        d = dense_rows(t)
        for row in d:
            cells = [str(c).strip() for c in row]
            if "总资产" in cells and "917,650,305" in cells:
                found = True
                check("总资产行满 5 列有效值", len([c for c in cells if c]) >= 5)
                check("含增减%", any("%" in c for c in cells))
                break
        if found:
            break
    # entries 路径（写回 mid_cache 用）
    if not found:
        for e in res.entries or []:
            t = getattr(e, "table", None)
            if t is None:
                continue
            d = dense_rows(t)
            for row in d:
                cells = [str(c).strip() for c in row]
                if "总资产" in cells and "917,650,305" in cells:
                    found = True
                    break
            if found:
                break
    check("保留 2022 年金额 917,650,305", found)
    print("ALL PASS")
