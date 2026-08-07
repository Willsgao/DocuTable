# -*- coding: utf-8 -*-
"""结构任务只来自主链快照，而非「凡有错就 AI」。"""

from codes.format_corrector.candidates import (
    _main_chain_needs_structure_review,
    _structure_ai_repair_tasks,
    _table_has_any_error,
    build_candidate_tasks,
)
from codes.format_corrector.models import TaskType
from codes.format_corrector.structure_ai_repair import hydrate_structure_task_from_main_chain
from codes.reconstruct import run_table_reconstruct


def test_main_chain_status_enters_structure_ai():
    tables = [
        {
            "type": "table",
            "page": 5,
            "data": [
                ["项目", "金额"],
                ["资产", "1,000,000"],
                ["负债", "800,000"],
            ],
            "_anomaly": {
                "needs_review": True,
                "rule_ids": ["R05_text_in_numeric"],
            },
            "repair_status": "llm_candidate",
            "_reconstruct": {"stage": "rules_done", "table_kind": "data"},
            "_table_kind": {"kind": "data"},
        },
        {
            "type": "table",
            "page": 6,
            "data": [
                ["项目", "金额"],
                ["现金", "1,000,000"],
            ],
            "_anomaly": {"needs_review": False, "rule_ids": []},
            "repair_status": "none",
            "_reconstruct": {"stage": "structure_ok", "table_kind": "data"},
            "_table_kind": {"kind": "data"},
        },
    ]
    ok, reasons, _ = _main_chain_needs_structure_review(tables[0])
    assert ok and reasons
    ok2, _, _ = _main_chain_needs_structure_review(tables[1])
    assert not ok2

    tasks = _structure_ai_repair_tasks(tables)
    assert len(tasks) == 1
    assert tasks[0].task_type == TaskType.STRUCTURE_AI_REPAIR
    assert tasks[0].table_index == 0
    assert tasks[0].evidence.get("source") == "reconstruct_main_chain"


def test_needs_review_alone_does_not_queue_without_main_chain():
    """仅有红点、无主链状态 → 不进结构 AI（避免第二套决策）。"""
    tables = [
        {
            "type": "table",
            "page": 1,
            "data": [
                ["项目", "金额"],
                ["现金", "1,000,000"],
                ["存款", "2,000,000"],
            ],
            "_anomaly": {"needs_review": True, "rule_ids": ["R03_stacked_long_text"]},
            "repair_status": "none",
        }
    ]
    ok_err, _, _ = _table_has_any_error(tables[0])
    assert ok_err  # 质检仍认为有错
    ok_mc, _, _ = _main_chain_needs_structure_review(tables[0])
    assert not ok_mc
    tasks = _structure_ai_repair_tasks(tables)
    assert tasks == []


def test_engine_path_queues_after_reconstruct():
    """扫描路径：先主链再候选 → 可出现结构任务。"""
    tables = [
        {
            "type": "table",
            "page": 1,
            "data": [
                ["项目", "期末", "上年末"],
                ["核心一级资本", "1000", "900"],
                ["一级资本", "1100", "1000"],
                ["总资本", "1200", "1100"],
            ],
            "_anomaly": {
                "needs_review": True,
                "rule_ids": ["C01_no_header_band"],
                "header_missing": True,
            },
        }
    ]
    run_table_reconstruct(tables[0], run_llm=False)
    tasks = build_candidate_tasks(tables, None)
    # 主链后若仍 llm_candidate/human_needed 才会有结构任务；rule_fixed/ok 则无
    struct = [t for t in tasks if t.task_type == TaskType.STRUCTURE_AI_REPAIR]
    status = tables[0].get("repair_status")
    if status in ("llm_candidate", "human_needed", "llm_proposed"):
        assert struct
        hydrated = hydrate_structure_task_from_main_chain(struct[0], tables[0])
        assert hydrated.proposal.get("from_main_chain") is True
    else:
        # 规则已清或跳过：允许无结构任务
        assert status in ("rule_fixed", "none", "skipped_non_data") or not struct


def test_hydrate_exposes_grid_nucleus_summary():
    from codes.format_corrector.grid_nucleus_view import summarize_grid_nucleus
    from codes.format_corrector.models import FormatTask, TaskType

    table = {
        "type": "table",
        "page": 13,
        "data": [["a", "b c"], ["1", "100"]],
        "repair_status": "llm_candidate",
        "_reconstruct": {"stage": "rules_done", "policy_trace": ["grid_nucleus:fallback_keep:ok=False:cols=0:rows=0"]},
        "_grid_nucleus": {
            "ok": False,
            "method": "fallback_keep",
            "n_rows": 0,
            "n_cols": 0,
            "errors": ["no_source_words"],
            "metrics": {},
        },
    }
    summ = summarize_grid_nucleus(table)
    assert summ["present"] is True
    assert summ["verdict"] == "kept"
    assert "凝核·保留原表" in summ["short_label"]

    task = FormatTask(
        task_id="t1",
        task_type=TaskType.STRUCTURE_AI_REPAIR,
        table_index=0,
        page=13,
        reason="test",
    )
    hydrated = hydrate_structure_task_from_main_chain(task, table)
    assert hydrated.evidence.get("grid_nucleus", {}).get("verdict") == "kept"
    assert hydrated.evidence.get("grid_nucleus_trace")


if __name__ == "__main__":
    test_main_chain_status_enters_structure_ai()
    test_needs_review_alone_does_not_queue_without_main_chain()
    test_engine_path_queues_after_reconstruct()
    test_hydrate_exposes_grid_nucleus_summary()
    print("OK")
