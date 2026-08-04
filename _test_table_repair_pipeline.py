# -*- coding: utf-8 -*-
"""表级全量检查 + Phase A 流水线单测。"""

from codes.table_repair.check_catalog import CHECK_CATALOG, catalog_ids
from codes.table_repair.checklist import run_full_checklist
from codes.table_repair.column_roles import infer_column_roles
from codes.table_repair.pipeline import run_table_repair_pipeline


def test_catalog_complete():
    ids = catalog_ids()
    assert len(ids) == len(CHECK_CATALOG)
    assert "H02" in ids and "L01" in ids and "N02" in ids


def test_column_roles_skips_serial_col():
    data = [
        ["序号", "项目", "2024", "2023"],
        ["1", "现金", "100", "90"],
        ["2", "存放中央银行款项", "200", "180"],
        ["3", "－法定存款准备金", "50", "40"],
    ]
    roles = infer_column_roles(data)
    assert 0 in roles.serial_cols or roles.primary_label_col == 1
    assert roles.primary_label_col != 0 or 0 not in roles.serial_cols
    # 有序号列时主标签不应是序号列
    if roles.serial_cols:
        assert roles.primary_label_col not in roles.serial_cols


def test_checklist_covers_all_ids():
    table = {
        "type": "table",
        "page": 1,
        "data": [
            ["项目", "2024年", "2023年"],
            ["现金及存放中央银行款项", "1,000", "900"],
            ["存放中央银行款项", "", ""],
            ["－法定存款准备金", "100", "90"],
        ],
        "_anomaly": {"rule_ids": [], "needs_review": False},
    }
    cl = run_full_checklist(table)
    got = {f["check_id"] for f in cl["findings"]}
    assert got == set(catalog_ids())
    assert "summary" in cl


def test_pipeline_marks_unresolved_and_fixes_wrap():
    table = {
        "type": "table",
        "page": 2,
        "data": [
            ["项目", "2024"],
            ["以公允价值计量且其变动计入当期损益的金融", "10"],
            ["资产", ""],
            ["－债券", "5"],
        ],
        "_anomaly": {"rule_ids": ["R03_stacked_long_text"], "needs_review": True},
    }
    cl = run_table_repair_pipeline(table, run_llm=False)
    assert table.get("_repair_checklist")
    assert "summary" in cl
    # 每项都有 fix_status
    for f in cl["findings"]:
        assert f.get("fix_status")
    # 不应在未开 LLM 时变成 llm_applied
    assert table.get("repair_status") in (
        "none", "rule_fixed", "llm_candidate", "human_needed",
    )


def test_data_loss_goes_human():
    table = {
        "type": "table",
        "page": 3,
        "data": [["a", "1"], ["b", "2"], ["c", "3"], ["d", "4"],
                 ["e", "5"], ["f", "6"], ["g", "7"], ["h", "8"]],
        "_conservation_failed": True,
        "_anomaly": {"rule_ids": [], "needs_review": True},
    }
    run_table_repair_pipeline(table, run_llm=False)
    assert table["repair_status"] == "human_needed"
    findings = table["_repair_checklist"]["findings"]
    n02 = next(f for f in findings if f["check_id"] == "N02")
    assert n02["fix_status"] == "needs_human"


if __name__ == "__main__":
    test_catalog_complete()
    test_column_roles_skips_serial_col()
    test_checklist_covers_all_ids()
    test_pipeline_marks_unresolved_and_fixes_wrap()
    test_data_loss_goes_human()
    print("OK")
