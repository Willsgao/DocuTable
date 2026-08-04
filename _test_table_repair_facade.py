# -*- coding: utf-8 -*-
"""table_repair Facade / validator（不调用真实 LLM）。"""
from __future__ import annotations

from codes.table_repair.llm_facade import build_llm_context, repair_with_facade
from codes.table_repair.validator import (
    amounts_invented,
    extract_amount_tokens,
    validate_repair,
)


def test_validator_blocks_invented_amounts():
    before = [["项目", "金额"], ["现金", "1,000"]]
    after = [["项目", "金额"], ["现金", "1,000"], ["存款", "9,999"]]
    ok, reasons = validate_repair(before, after, confidence=0.9)
    assert not ok
    assert any("补造" in r for r in reasons)
    assert "9999" in amounts_invented(before, after) or any(
        "9999" in x for x in amounts_invented(before, after)
    )


def test_validator_allows_reorder_same_amounts():
    before = [["a", "1,000"], ["b", "2,000"]]
    after = [["b", "2,000"], ["a", "1,000"]]
    ok, reasons = validate_repair(before, after, confidence=0.9)
    assert ok, reasons


def test_context_includes_hierarchy_hint():
    ctx = build_llm_context(problem_tags=["hierarchy_lost", "wrap_split"])
    assert "层级" in ctx or "分项" in ctx
    assert "禁止" in ctx or "补造" in ctx


def test_facade_data_loss_no_llm():
    result = repair_with_facade(
        [["x", "1"]],
        problem_tags=["data_loss"],
    )
    assert not result.success
    assert result.escalate_human
    assert "data_loss" in result.llm_error or "人工" in result.llm_error


def test_amount_extract():
    data = [["－债券", "3,495", "(1,200)"]]
    tokens = extract_amount_tokens(data)
    assert any("3495" in k for k in tokens)


if __name__ == "__main__":
    test_validator_blocks_invented_amounts()
    test_validator_allows_reorder_same_amounts()
    test_context_includes_hierarchy_hint()
    test_facade_data_loss_no_llm()
    test_amount_extract()
    print("OK")
