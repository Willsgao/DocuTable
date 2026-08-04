# -*- coding: utf-8 -*-
"""人工队列 collect / accept / reject 单元测试。"""

from codes.table_repair.human_queue import (
    DECISION_ACCEPTED,
    DECISION_REJECTED,
    HumanQueueItem,
    apply_queue_decisions,
    collect_human_queue,
    store_llm_proposal,
)


def test_collect_and_apply_proposal():
    tables = [
        {
            "type": "table",
            "page": 3,
            "caption": "demo",
            "repair_status": "llm_candidate",
            "data": [["a", "1"], ["b", "2"]],
            "_problem_report": {
                "problem_tags": ["wrap_split"],
                "rule_ids": ["R03"],
            },
        },
        {
            "type": "text",
            "data": "skip me",
            "repair_status": "human_needed",
        },
    ]
    store_llm_proposal(
        tables[0],
        before_data=tables[0]["data"],
        repaired_table=[["a", "1"], ["b", "2"], ["c", "3"]],
        success=True,
        confidence=0.9,
        problem_tags=["wrap_split"],
        reasoning_summary="merged wrap",
    )
    assert tables[0]["repair_status"] == "llm_proposed"

    items = collect_human_queue(tables)
    assert len(items) == 1
    assert items[0].has_proposal
    assert items[0].page == 3
    assert "wrap_split" in items[0].problem_tags

    items[0].decision = DECISION_ACCEPTED
    a, r, s = apply_queue_decisions(tables, items)
    assert (a, r, s) == (1, 0, 0)
    assert tables[0]["repair_status"] == "llm_applied"
    assert len(tables[0]["data"]) == 3


def test_reject_keeps_original():
    table = {
        "type": "table",
        "page": 1,
        "data": [["x"]],
        "repair_status": "llm_proposed",
    }
    store_llm_proposal(
        table,
        before_data=[["x"]],
        repaired_table=[["y"]],
        success=True,
        confidence=0.5,
    )
    item = collect_human_queue([table])[0]
    item.decision = DECISION_REJECTED
    apply_queue_decisions([table], [item])
    assert table["data"] == [["x"]]
    assert table["repair_status"] == "human_needed"


def test_human_needed_without_proposal():
    tables = [
        {
            "type": "table",
            "page": 9,
            "data": [["only"]],
            "repair_status": "human_needed",
            "_problem_report": {"problem_tags": ["data_loss"]},
        }
    ]
    items = collect_human_queue(tables)
    assert len(items) == 1
    assert not items[0].has_proposal
    items[0].decision = DECISION_ACCEPTED
    apply_queue_decisions(tables, items)
    assert tables[0]["repair_status"] == "human_done"


def test_llm_candidate_not_in_queue():
    """候选表不应堆进人工队列——否则等于把 AI 检查丢给人。"""
    tables = [
        {
            "type": "table",
            "page": 2,
            "data": [["a"]],
            "repair_status": "llm_candidate",
            "_problem_report": {"problem_tags": ["misalignment"]},
        },
        {
            "type": "table",
            "page": 3,
            "data": [["b"]],
            "repair_status": "none",
        },
    ]
    items = collect_human_queue(tables)
    assert items == []


if __name__ == "__main__":
    test_collect_and_apply_proposal()
    test_reject_keeps_original()
    test_human_needed_without_proposal()
    test_llm_candidate_not_in_queue()
    print("OK")
