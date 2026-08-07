# -*- coding: utf-8 -*-
"""table_repair 第0/1期：问题报告 + 折行规则 + 路由。"""
from __future__ import annotations

from codes.table_repair.problem_model import build_problem_report
from codes.table_repair.router import run_repair_router_on_table
from codes.table_repair.wrap_repair import merge_wrapped_label_rows, repair_table_wrap_split


def test_wrap_merge():
    data = [
        ["项目", "金额"],
        ["指定为以公允价值计量且其变动计入其他", "1,000"],
        ["的权益工具", ""],
        ["现金", "2"],
    ]
    new_data, notes = merge_wrapped_label_rows(data)
    assert notes, "应合并真折行续写"
    assert len(new_data) == 3
    assert "的权益工具" in str(new_data[1][0])
    assert new_data[2][0] == "现金"


def test_section_title_not_merged_as_wrap():
    """表内分标题（资本充足率）不得并入上一数据行；「底线前）」可续并。"""
    data = [
        ["4a", "风险加权资产合计（应用资本", "21,854,590", "22,150,555"],
        ["", "底线前）", "", ""],
        ["", "资本充足率", "", ""],
        ["5", "核心一级资本充足率（%）", "14.48", "14.10"],
        ["", "杠杆率相关信息", "", ""],
        ["13", "调整后表内外资产余额", "42,755,544", "41,837,451"],
    ]
    table = {"type": "table", "page": 5, "data": [list(r) for r in data]}
    notes = repair_table_wrap_split(table, label_col=1)
    assert any("底线前）" in n for n in notes), notes
    assert not any("资本充足率" in n for n in notes), notes
    assert not any("杠杆率相关信息" in n for n in notes), notes
    labels = [str(r[1]) for r in table["data"]]
    assert "资本充足率" in labels
    assert "杠杆率相关信息" in labels
    assert any("应用资本底线前）" in x for x in labels)


def test_hierarchy_not_merged_and_split():
    from codes.table_repair.wrap_repair import (
        merge_wrapped_label_rows,
        split_hierarchy_glued_rows,
    )

    # 不应把父级与 －债券 当折行合并
    data = [
        ["以公允价值计量且其变动计入当期损益的金融资产", "", "", "", ""],
        ["持有作交易用途的金融资产", "3,495", "127,185", "", "130,680"],
        ["－权益工具和基金", "302", "2,747", "", "3,049"],
    ]
    merged, notes = merge_wrapped_label_rows(data)
    assert not notes
    assert len(merged) == 3

    # 已粘连：父+中+叶子 → 拆开，数值留在叶子
    glued = [
        ["项目", "一", "二", "三", "合"],
        [
            "以公允价值计量且其变动计入当期损益的金融资产持有作交易用途的金融资产 - 债券",
            "3,495",
            "127,185",
            "",
            "130,680",
        ],
        ["－权益工具和基金", "302", "2,747", "", "3,049"],
    ]
    split, snotes = split_hierarchy_glued_rows(glued)
    assert snotes
    assert any("债券" in str(r[0]) for r in split)
    leaf = next(r for r in split if "债券" in str(r[0]) and "权益" not in str(r[0]))
    assert leaf[1] == "3,495"
    parents = [r for r in split if r[0] and not str(r[0]).startswith("－") and "债券" not in str(r[0])]
    assert any("当期损益的金融资产" in str(r[0]) for r in parents)


def test_problem_report_glue_and_human():
    table = {
        "type": "table",
        "page": 1,
        "table_id": 1,
        "data": [["a", "b"], ["1", "2"], ["3", "4"]],
        "_anomaly": {
            "rule_ids": ["R10_numeric_text_glue", "C04_incomplete_data_row"],
            "needs_review": True,
            "anomaly_score": 0.5,
        },
    }
    rep = build_problem_report(table)
    assert "cell_glue" in rep.problem_tags
    assert "data_loss" in rep.problem_tags
    assert rep.severity == "human_only"


def test_router_wrap_fixes():
    table = {
        "type": "table",
        "page": 9,
        "table_id": 9,
        "table_category": "财务数据表",
        "is_real_table": True,
        "data": [
            ["项目", "金额"],
            ["指定为以公允价值计量且其变动计入其他", "1,000"],
            ["的权益工具", ""],
        ],
        "_anomaly": {
            "rule_ids": ["R03_stacked_long_text"],
            "needs_review": True,
            "anomaly_score": 0.3,
        },
    }

    def _redetect(t):
        t["_anomaly"] = {
            "rule_ids": [],
            "needs_review": False,
            "anomaly_score": 0.0,
            "header_missing": False,
        }

    rep = run_repair_router_on_table(table, redetect_anomaly=_redetect)
    actions = str((rep.get("evidence") or {}).get("router_actions") or [])
    assert table.get("_wrap_repaired") or "折行合并" in actions
    assert len(table["data"]) == 2
    assert "的权益工具" in str(table["data"][1][0])
    assert rep.get("repair_status") in ("rule_fixed", "llm_candidate", "none")


def test_router_hierarchy_split():
    table = {
        "type": "table",
        "page": 10,
        "table_category": "财务数据表",
        "is_real_table": True,
        "data": [
            ["项目", "一", "二", "合"],
            [
                "以公允价值计量且其变动计入当期损益的金融资产持有作交易用途的金融资产 - 债券",
                "3,495",
                "127,185",
                "130,680",
            ],
            ["－权益工具和基金", "302", "2,747", "3,049"],
        ],
        "_anomaly": {
            "rule_ids": ["R01_orphan_extension"],
            "needs_review": True,
            "anomaly_score": 0.4,
        },
    }

    def _redetect(t):
        t["_anomaly"] = {
            "rule_ids": [],
            "needs_review": False,
            "anomaly_score": 0.0,
        }

    rep = run_repair_router_on_table(table, redetect_anomaly=_redetect)
    assert table.get("_hierarchy_split")
    assert len(table["data"]) >= 4
    assert any(str(r[0]).startswith("－") and "债券" in str(r[0]) for r in table["data"])


def test_data_loss_not_auto_invent():
    table = {
        "type": "table",
        "page": 2,
        "data": [["x", "y"]] + [["1", "2"]] * 5,
        "_conservation_failed": True,
        "_anomaly": {"rule_ids": [], "needs_review": False},
    }
    rep = run_repair_router_on_table(table)
    assert "data_loss" in (rep.get("problem_tags") or [])
    assert rep.get("repair_status") == "human_needed"


if __name__ == "__main__":
    test_wrap_merge()
    test_section_title_not_merged_as_wrap()
    test_hierarchy_not_merged_and_split()
    test_problem_report_glue_and_human()
    test_router_wrap_fixes()
    test_router_hierarchy_split()
    test_data_loss_not_auto_invent()
    print("OK")
    test_data_loss_not_auto_invent()
    print("OK")
