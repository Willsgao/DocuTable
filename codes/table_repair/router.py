# -*- coding: utf-8 -*-
"""修复路由：委托全量检查流水线（Phase A 规则 → 标注 → 可选单次 LLM）。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def run_repair_router_on_table(
    table: Dict[str, Any],
    *,
    redetect_anomaly=None,
    run_llm: bool = False,
    llm_apply: bool = False,
) -> Dict[str, Any]:
    """对单表跑全量修复流水线。

    返回 `_problem_report` dict（兼容旧调用方）。
    详细检查见 table['_repair_checklist']。
    """
    from codes.table_repair.pipeline import run_table_repair_pipeline

    run_table_repair_pipeline(
        table,
        redetect_anomaly=redetect_anomaly,
        run_llm=run_llm,
        llm_apply=llm_apply,
    )
    return table.get("_problem_report") or {}


def run_repair_router_on_payload(
    payload: Dict[str, Any],
    *,
    redetect_anomaly=None,
    run_llm: bool = False,
) -> Tuple[int, int, int]:
    """遍历 payload 内表格跑路由。

    返回 (rule_fixed_count, llm_candidate_count, human_needed_count)。
    """
    fixed = llm_n = human_n = 0

    def _buckets():
        data = payload.get("data")
        if isinstance(data, dict):
            yield data.get("tables") or []
            yield data.get("tables_before_segmentation") or []
        elif isinstance(payload.get("tables"), list):
            yield payload["tables"]

    for tables in _buckets():
        if not isinstance(tables, list):
            continue
        for table in tables:
            if not isinstance(table, dict):
                continue
            if table.get("type") in ("text", "paragraph"):
                continue
            if not isinstance(table.get("data"), list):
                continue
            rep = run_repair_router_on_table(
                table,
                redetect_anomaly=redetect_anomaly,
                run_llm=run_llm,
            )
            st = rep.get("repair_status") or table.get("repair_status")
            if st == "rule_fixed":
                fixed += 1
            elif st in ("llm_candidate", "llm_proposed"):
                llm_n += 1
            elif st == "human_needed":
                human_n += 1
    return fixed, llm_n, human_n
