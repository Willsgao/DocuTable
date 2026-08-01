# -*- coding: utf-8 -*-
"""用成都银行 2024 年报 liteparse 缓存验收结构关键洞（P14/P28 等）。

数据：data/mid_cache/2025-04-29-601838_*/liteparse/pages.json
对照：同目录 pages/page_XXX.txt
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _find_pages_json() -> Path:
    mid = ROOT / "data" / "mid_cache"
    for p in mid.iterdir():
        if p.is_dir() and "601838" in p.name:
            js = p / "liteparse" / "pages.json"
            if js.exists():
                return js
    raise FileNotFoundError("未找到 601838 liteparse/pages.json")


def _header_row(rows):
    for r in rows:
        cells = [str(c or "").strip() for c in r]
        if any(c in ("项目", "品种", "类别", "类型") for c in cells):
            return cells
        # 双层表头：报告期独占一行
        if any("年" in c and ("月" in c or "日" in c) for c in cells if c):
            if sum(1 for c in cells if c and "年" in c) >= 1:
                return cells
    return None


def _table_has_header_tokens(rows, tokens) -> bool:
    """表头带（前几行）是否含所需片段。"""
    band = rows[:6]
    blob = " ".join(str(c) for r in band for c in r if c)
    return all(t in blob for t in tokens)


def _any_cell_has(rows, *needles: str) -> bool:
    blob = " ".join(str(c) for r in rows for c in r if c)
    return all(n in blob for n in needles)


def validate_page(page_num: int, checks: dict) -> list[str]:
    from codes.table_engine.source.liteparse_loader import load_page
    from codes.table_engine.pipeline import build_page
    from codes.table_engine.table_access import dense_rows
    from codes.table_engine.export.legacy_adapter import (
        to_legacy_table,
        verify_legacy_table_matches_structured,
        audit_legacy_source_coverage,
    )

    path = _find_pages_json()
    page = load_page(path, page_num)
    result = build_page(page)
    errors: list[str] = []
    tables = [e.table for e in result.entries if e.table]
    texts = [
        e.text_block.text
        for e in result.entries
        if e.kind == "text" and e.text_block and e.text_block.text.strip()
    ]

    if checks.get("min_tables") is not None and len(tables) < checks["min_tables"]:
        errors.append(f"P{page_num}: tables={len(tables)} < {checks['min_tables']}")

    for needle in checks.get("forbid_orphan_texts") or []:
        for t in texts:
            if t.strip() == needle:
                errors.append(f"P{page_num}: orphan TEXT {needle!r}")

    for spec in checks.get("table_must") or []:
        matched = False
        for t in tables:
            rows = dense_rows(t)
            if not _any_cell_has(rows, *spec.get("contains", ())):
                continue
            matched = True
            header = _header_row(rows)
            for h in spec.get("header_has") or []:
                if not _table_has_header_tokens(rows, (h,)):
                    errors.append(
                        f"P{page_num}: header missing {h!r} in table with {spec.get('contains')}"
                    )
            for h in spec.get("header_not_glued") or []:
                # 禁止多列表头糊进同一格
                if header and any(
                    all(part in (c or "") for part in h)
                    for c in header
                ):
                    errors.append(f"P{page_num}: glued header {h} still in one cell: {header}")
            legacy = to_legacy_table(t)
            if not verify_legacy_table_matches_structured(t, legacy):
                errors.append(f"P{page_num}: legacy verify failed")
            cov = audit_legacy_source_coverage(legacy)
            min_cov = spec.get("min_source_coverage", 0.5)
            if cov["coverage"] < min_cov:
                errors.append(
                    f"P{page_num}: source coverage {cov['coverage']:.0%} < {min_cov:.0%} "
                    f"missing={cov['missing_samples'][:3]}"
                )
            if "_cell_source_items" not in legacy:
                errors.append(f"P{page_num}: missing _cell_source_items")
            for row_spec in spec.get("row_must") or []:
                label = row_spec.get("label", "")
                expect = row_spec.get("cells")
                found = False
                for row in rows:
                    cells = [str(c or "").strip() for c in row]
                    if label and not any(label in c for c in cells):
                        continue
                    found = True
                    if expect is not None:
                        # 对齐期望列（允许首列标签不完全等于）
                        for ci, want in enumerate(expect):
                            if want is None:
                                continue
                            got = cells[ci] if ci < len(cells) else ""
                            if want not in got and got != want:
                                errors.append(
                                    f"P{page_num}: row {label!r} col{ci} "
                                    f"want {want!r} got {got!r} full={cells}"
                                )
                    for forbid in row_spec.get("forbid_in_cells") or []:
                        if any(forbid in c for c in cells):
                            errors.append(
                                f"P{page_num}: row {label!r} still has {forbid!r}: {cells}"
                            )
                    break
                if not found:
                    errors.append(f"P{page_num}: row not found label={label!r}")
        if not matched:
            errors.append(f"P{page_num}: no table matched contains={spec.get('contains')}")

    return errors


def main() -> int:
    suites = [
        (
            14,
            {
                "min_tables": 2,
                "forbid_orphan_texts": ["增减", "末增减"],
                "table_must": [
                    {
                        "contains": ("营业收入",),
                        "header_has": ("项目", "2024", "2023"),
                        "min_source_coverage": 0.4,
                    },
                    {
                        "contains": ("总资产",),
                        "header_has": ("项目",),
                        "min_source_coverage": 0.4,
                    },
                ],
            },
        ),
        (
            15,
            {
                "min_tables": 1,
                "table_must": [
                    {
                        "contains": ("加权平均净资产收益率", "近三年主要财务指标"),
                        "header_has": ("项目", "2024", "2023", "2022"),
                        "min_source_coverage": 0.35,
                        "row_must": [
                            {
                                "label": "加权平均净资产收益率",
                                "cells": (
                                    None,
                                    "17.81%",
                                    "18.78%",
                                    "下降 0.97 个百分点",
                                    "19.48%",
                                ),
                                "forbid_in_cells": ("下降 0.97 个百分点 19.48%",),
                            },
                            {
                                "label": "扣除非经常性损益后的加权",
                                "cells": (
                                    None,
                                    "17.76%",
                                    "18.44%",
                                    "下降 0.68 个百分点",
                                    "19.34%",
                                ),
                                "forbid_in_cells": ("下降 0.68 个百分点 19.34%",),
                            },
                        ],
                    },
                ],
            },
        ),
        (
            33,
            {
                "min_tables": 2,
                "forbid_orphan_texts": [],
                "table_must": [
                    {
                        "contains": ("存放同业及其他金融机构款项", "拆出资金"),
                        "header_has": ("项目", "2024", "2023", "增减幅度", "主要原因"),
                        "header_not_glued": (
                            ("2024", "2023", "增减幅度"),
                            ("2024", "2023"),
                        ),
                        "min_source_coverage": 0.35,
                        "row_must": [
                            {
                                "label": "存放同业及其他金融机构款项",
                                "cells": (
                                    None,
                                    "4,135,772",
                                    "1,661,178",
                                    "148.97%",
                                    "存放同业清算款项增加",
                                ),
                                "forbid_in_cells": (
                                    "股东权益合计",
                                    "20.45%",
                                    "14.56%",
                                ),
                            },
                        ],
                    },
                ],
            },
        ),
        (
            36,
            {
                "min_tables": 1,
                "table_must": [
                    {
                        "contains": ("债券", "389,777", "570,629"),
                        "header_has": ("2024", "2023"),
                        "min_source_coverage": 0.3,
                        "row_must": [
                            {
                                "label": "品种",
                                "cells": (
                                    "品种",
                                    "2024 年 12 月 31 日",
                                    None,
                                    "2023 年 12 月 31 日",
                                    None,
                                ),
                                "forbid_in_cells": (),
                            },
                            {
                                "label": "账面余额",
                                "cells": (
                                    None,
                                    "账面余额",
                                    "占比",
                                    "账面余额",
                                    "占比",
                                ),
                                "forbid_in_cells": (),
                            },
                            {
                                "label": "债券",
                                "cells": (None, "389,777", "100%", "570,629", "100%"),
                                "forbid_in_cells": ("债券合计",),
                            },
                            {
                                "label": "合计",
                                "cells": (None, "389,777", "100%", "570,629", "100%"),
                                "forbid_in_cells": ("债券合计",),
                            },
                        ],
                    },
                ],
            },
        ),
        (
            28,
            {
                "min_tables": 2,
                "forbid_orphan_texts": ["增减"],
                "table_must": [
                    {
                        "contains": ("一、营业收入",),
                        "header_has": ("增减幅度",),
                        "min_source_coverage": 0.4,
                    },
                    {
                        "contains": ("手续费及佣金支出", "代理业务"),
                        "header_has": ("变化原因", "增减幅度"),
                        "header_not_glued": (("2023", "增减幅度", "变化原因"),),
                        "min_source_coverage": 0.35,
                    },
                ],
            },
        ),
    ]
    path = _find_pages_json()
    print(f"pages.json = {path}")
    all_err: list[str] = []
    for page_num, checks in suites:
        errs = validate_page(page_num, checks)
        if errs:
            print(f"FAIL P{page_num}")
            for e in errs:
                print(" ", e)
            all_err.extend(errs)
        else:
            print(f"PASS P{page_num}")
    if all_err:
        print(f"\n{len(all_err)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
