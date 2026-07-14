# -*- coding: utf-8 -*-
"""Table Engine Step 7 — 物理删除旧引擎验收。"""

import subprocess
import sys
from pathlib import Path

PASS = FAIL = 0

FORBIDDEN_PATTERNS = [
    "hybrid_segment_tables",
    "rule_based_repair",
    "table_content_splitter",
    "liteparse_table_segmenter",
    "coord_row_refiner",
    "hybrid_segmenter",
]

DELETED_FILES = [
    "codes/table_validator/hybrid_segmenter.py",
    "codes/table_validator/liteparse_table_segmenter.py",
    "codes/table_validator/table_content_splitter.py",
    "codes/table_validator/coord_row_refiner.py",
    "codes/table_validator/rule_based_repair.py",
]

KEPT_FILES = [
    "codes/table_validator/table_structure_repair.py",
    "codes/table_engine/integration/processor_bridge.py",
]


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def test_codes_no_legacy_strings() -> None:
    print("--- codes/ 无旧引擎字符串 ---")
    for pat in FORBIDDEN_PATTERNS:
        result = subprocess.run(
            ["rg", "-l", pat, "codes/"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent,
        )
        hits = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        check(f"no '{pat}' in codes/", not hits, ", ".join(hits[:3]))


def test_deleted_files_gone() -> None:
    print("--- 旧文件已删除 ---")
    root = Path(__file__).resolve().parent
    for rel in DELETED_FILES:
        check(f"deleted {rel}", not (root / rel).exists())
    for rel in KEPT_FILES:
        check(f"kept {rel}", (root / rel).exists())


def test_structure_repair_importable() -> None:
    print("--- table_structure_repair 可导入 ---")
    try:
        from codes.table_validator.table_structure_repair import (
            repair_table_rules,
            deduplicate_adjacent_tables,
            _has_complete_table_structure,
        )
        check("repair_table_rules callable", callable(repair_table_rules))
        check("deduplicate_adjacent_tables callable", callable(deduplicate_adjacent_tables))
        check("_has_complete_table_structure callable", callable(_has_complete_table_structure))
    except Exception as e:
        check("import table_structure_repair", False, str(e))


def test_format_segmentation_report() -> None:
    print("--- format_segmentation_report ---")
    from codes.table_engine.integration.processor_bridge import format_segmentation_report
    text = format_segmentation_report(
        [{"type": "table", "is_real_table": True}],
        {"method": "table_engine", "total_tables": 1, "total_text": 0, "total_entries": 1},
    )
    check("report contains Table Engine", "Table Engine" in text)
    check("report contains method", "table_engine" in text)


def test_regression_step5_step6() -> None:
    print("--- Step 5/6 回归 ---")
    root = Path(__file__).resolve().parent
    for script in ("_test_te_step5_document.py", "_test_te_step6_processor.py"):
        proc = subprocess.run(
            [sys.executable, str(root / script)],
            capture_output=True,
            text=True,
            cwd=root,
        )
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-1] if proc.stdout or proc.stderr else ""
        check(f"{script} exit 0", proc.returncode == 0, tail)


def main() -> None:
    test_codes_no_legacy_strings()
    test_deleted_files_gone()
    test_structure_repair_importable()
    test_format_segmentation_report()
    test_regression_step5_step6()
    print(f"\n=== Step 7: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
