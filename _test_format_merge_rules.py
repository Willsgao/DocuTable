# -*- coding: utf-8 -*-
"""格式校正合并规则：同页夹层禁止合并、跨页页眉仅页首、列数策略。"""
from __future__ import annotations

from codes.format_corrector.candidates import (
    _cols_merge_policy,
    _gap_allows_merge,
    _is_independent_new_table,
    build_candidate_tasks,
)
from codes.format_corrector.conservation import (
    table_has_own_column_header,
    table_missing_header,
)
from codes.format_corrector.models import TaskType


def test_same_page_gap_blocks_merge():
    allow, headerish, note = _gap_allows_merge(
        ["上述金融债券期末余额均在国内发行"],
        same_page=True,
    )
    assert allow is False
    assert "同页" in note
    assert headerish is False


def test_cross_page_pre_table_text_blocks():
    allow, _, note = _gap_allows_merge(
        ["单位：千元"],
        same_page=False,
        page_header_lines=["成都银行股份有限公司", "2024 年年度报告"],
        pre_table_lines=["衍生金融工具如下"],
    )
    assert allow is False
    assert "说明" in note or "非页首" in note


def test_cross_page_page_header_only_ok():
    allow, headerish, note = _gap_allows_merge(
        ["成都银行股份有限公司", "2024 年年度报告"],
        same_page=False,
        page_header_lines=["成都银行股份有限公司", "2024 年年度报告"],
        pre_table_lines=[],
    )
    assert allow is True
    assert headerish is True
    assert note == ""


def test_cols_policy():
    ok, mismatch, _ = _cols_merge_policy(5, 5, same_page=True, missing_hdr=True)
    assert ok and not mismatch
    ok, mismatch, note = _cols_merge_policy(5, 4, same_page=True, missing_hdr=True)
    assert not ok
    ok, mismatch, note = _cols_merge_policy(5, 4, same_page=False, missing_hdr=True)
    assert ok and mismatch and "列数不同" in note
    ok, _, _ = _cols_merge_policy(5, 4, same_page=False, missing_hdr=False)
    assert not ok


def test_derivative_table_has_own_header():
    table = {
        "data": [
            ["", "", "", "单位：千元"],
            ["", "", "公允价值", ""],
            ["项目", "合约/名义金额", "", ""],
            ["", "", "资产", "负债"],
            ["外汇掉期", "45,329,032", "209,411", "222,858"],
        ]
    }
    assert table_has_own_column_header(table)
    assert not table_missing_header(table)
    assert _is_independent_new_table(table)


def test_stage_period_multirow_header_not_missing():
    """报告期 + 阶段一/二/三/合计 是完整表头，不能当缺表头续表。"""
    table = {
        "data": [
            ["", "", "2023年12月31日", "", ""],
            ["", "阶段一", "阶段二", "阶段三", "合计"],
            ["以摊余成本计量的发放贷款和垫款总额", "21,602,943", "1", "2", "3"],
        ],
        "_anomaly": {"header_missing": True},
        "table_category": "数据表(缺表头)",
        "quality_decision": "header_missing",
    }
    assert table_has_own_column_header(table)
    assert not table_missing_header(table)
    assert _is_independent_new_table(table)


def test_header_detected_from_rows_above_data():
    """先找数据行，再认上方表头——不依赖首行必须像表头。"""
    from codes.format_corrector.conservation import find_first_data_row_index

    with_header = {
        "data": [
            ["单位：千元"],
            ["项目", "2024 年", "2023 年", "增减"],
            ["总资产", "1,250,116,154", "1,091,243,069", "14.56%"],
        ]
    }
    assert find_first_data_row_index(with_header["data"]) == 2
    assert table_has_own_column_header(with_header)
    assert not table_missing_header(with_header)

    body_only = {
        "data": [
            ["总资产", "1,250,116,154", "1,091,243,069", "14.56%"],
            ["净资产", "85,855,452", "71,235,227", "20.52%"],
        ]
    }
    assert find_first_data_row_index(body_only["data"]) == 0
    assert not table_has_own_column_header(body_only)
    assert table_missing_header(body_only)


def test_p50_bond_derivative_not_merged():
    import json
    from pathlib import Path

    root = next(
        p
        for p in Path(__file__).resolve().parent.joinpath("data/mid_cache").iterdir()
        if "601838" in p.name
    )
    tables = json.loads((root / "data.json").read_text(encoding="utf-8"))["data"][
        "tables"
    ]
    lp = json.loads((root / "liteparse" / "pages.json").read_text(encoding="utf-8"))
    tasks = build_candidate_tasks(tables, lp)
    bad = [
        t
        for t in tasks
        if t.task_type == TaskType.CROSS_PAGE_MERGE
        and t.evidence.get("pair") == [204, 206]
    ]
    assert not bad, f"should not propose merge-204-206: {[t.reason for t in bad]}"


if __name__ == "__main__":
    test_same_page_gap_blocks_merge()
    print("same-page gap OK")
    test_cross_page_pre_table_text_blocks()
    print("cross-page pre-table OK")
    test_cross_page_page_header_only_ok()
    print("page header OK")
    test_cols_policy()
    print("cols policy OK")
    test_derivative_table_has_own_header()
    print("derivative header OK")
    test_stage_period_multirow_header_not_missing()
    print("stage/period header OK")
    test_header_detected_from_rows_above_data()
    print("data-first header OK")
    test_p50_bond_derivative_not_merged()
    print("P50 not merged OK")
    print("ALL PASS")
