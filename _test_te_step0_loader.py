# -*- coding: utf-8 -*-
"""Table Engine Step 0 — loader 与 SourceItem 契约验收。"""

import json
import sys
from pathlib import Path

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.source.liteparse_loader import load_liteparse_document, load_page

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    cache = DEFAULT_PILLAR_CACHE
    if not cache.exists():
        print(f"skip: 缓存不存在 {cache}")
        return 0

    print(f"=== Step 0: liteparse loader ===")
    print(f"cache: {cache}\n")

    doc = load_liteparse_document(cache)
    check("total_pages == 43", doc.total_pages == 43, f"got {doc.total_pages}")
    check("pages list length", len(doc.pages) == doc.total_pages)

    raw_data = json.loads(cache.read_text(encoding="utf-8"))
    raw_page11 = next(
        p for p in raw_data["pages"] if p["page_number"] == 11
    )
    page11 = load_page(cache, 11)

    raw_count = len(raw_page11.get("text_items", []))
    norm_count = len(page11.items)
    check(
        "P11 normalized items > 0",
        norm_count > 0,
        f"raw={raw_count} norm={norm_count}",
    )
    check(
        "P11 merge reduces or equals raw count",
        norm_count <= raw_count,
        f"raw={raw_count} norm={norm_count}",
    )

    bad_fields = [
        i
        for i, it in enumerate(page11.items)
        if not it.item_index
        or it.bbox.height <= 0
        or it.y_mid <= 0
    ]
    check(
        "all P11 items have index+bbox+y_mid",
        not bad_fields,
        f"bad indices: {bad_fields[:5]}",
    )

    check(
        "P11 is_table_page",
        page11.is_table_page and len(page11.table_regions) >= 1,
    )
    check(
        "P11 table_region has bbox",
        all(r.x1 > r.x0 and r.y1 > r.y0 for r in page11.table_regions),
    )

    print("\n--- P11 前 20 个 SourceItem（y_mid, x0 排序）---")
    sorted_items = sorted(page11.items, key=lambda it: (it.y_mid, it.x0))
    for it in sorted_items[:20]:
        print(
            f"  y={it.y0:6.1f} x={it.x0:6.1f}  "
            f"idx={it.item_index[:12]:>12}  {it.text[:40]!r}"
        )

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    if FAIL:
        print("Step 0 未通过 — 请修复后再进入 Step 1")
        return 1
    print("Step 0 通过 — 可进入 Step 1（几何建表内核）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
