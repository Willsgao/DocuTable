# -*- coding: utf-8 -*-
"""统一还原编排：liteparse 锚定 → 数据主体 → 规则 checklist/表头 → 可选 LLM。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def run_table_reconstruct(
    table: Dict[str, Any],
    *,
    redetect_anomaly=None,
    run_llm: bool = False,
    llm_apply: bool = False,
    liteparse_data: Optional[Dict[str, Any]] = None,
    liteparse_page: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对单表跑还原主链。

    顺序（见 policy）：
      锚定 liteparse → 粘连拆+锁数据主体 → table_repair 规则（表头服从主体）
      → 可选 LLM（仅剩余问题）。

    返回 `_reconstruct` 快照。
    """
    from codes.reconstruct.snapshot import (
        STAGE_ERROR,
        RECONSTRUCT_VERSION,
        build_reconstruct_snapshot,
    )
    from codes.reconstruct.policy import (
        POLICY_SUMMARY,
        STAGE_ANCHOR,
        STAGE_DATA_BODY,
        STAGE_GLUE,
        STAGE_LLM,
        STAGE_RULES,
    )

    if not table or not isinstance(table, dict):
        return {
            "version": RECONSTRUCT_VERSION,
            "stage": STAGE_ERROR,
            "accepted_for_ingest": False,
            "error": "invalid_table",
        }

    policy_trace: list = []
    try:
        # 1) liteparse 字框锚定
        from codes.reconstruct.liteparse_anchor import attach_liteparse_words

        anchor = attach_liteparse_words(
            table,
            liteparse_data=liteparse_data,
            liteparse_page=liteparse_page,
        )
        policy_trace.append(f"{STAGE_ANCHOR}:words={anchor.get('word_count', 0)}")

        # 2) 粘连 + 数据主体（同列同型）—— 先于 checklist
        from codes.reconstruct.data_body import prepare_data_body

        body = prepare_data_body(table)
        for n in (body.get("notes") or [])[:6]:
            policy_trace.append(f"{STAGE_DATA_BODY}:{n}" if "glue" not in str(n) else f"{STAGE_GLUE}:{n}")

        # 2.5) 凝结核网格（字框 → 分割线 → data）；失败保留原 data
        try:
            from codes.table_repair.table_kind import get_table_kind
            from codes.reconstruct.grid_nucleus import apply_grid_to_table, GRID_NUCLEUS

            if GRID_NUCLEUS.get("enabled") and get_table_kind(table) == "data":
                grid_res = apply_grid_to_table(table)
                policy_trace.append(
                    f"grid_nucleus:{grid_res.method}:ok={grid_res.ok}:"
                    f"cols={grid_res.n_cols}:rows={grid_res.n_rows}"
                )
                if grid_res.errors:
                    policy_trace.append("grid_err:" + ";".join(grid_res.errors[:3]))
        except Exception as exc:
            policy_trace.append(f"grid_nucleus_skip:{exc}")

        # 3) 规则 checklist / PhaseA（表头等）+ 可选 LLM
        from codes.table_repair.pipeline import run_table_repair_pipeline

        policy_trace.append(STAGE_RULES)
        checklist = run_table_repair_pipeline(
            table,
            redetect_anomaly=redetect_anomaly,
            run_llm=run_llm,
            llm_apply=llm_apply,
        )
        if run_llm:
            policy_trace.append(STAGE_LLM)

        snap = build_reconstruct_snapshot(table, checklist=checklist)
        snap["policy"] = POLICY_SUMMARY
        snap["policy_trace"] = policy_trace
        snap["liteparse_anchor"] = table.get("_liteparse_anchor") or anchor
        snap["data_body"] = (table.get("_data_body") or {}).get("zone")
        table["_reconstruct"] = snap
        return snap
    except Exception as exc:
        snap = {
            "version": RECONSTRUCT_VERSION,
            "stage": STAGE_ERROR,
            "accepted_for_ingest": False,
            "error": str(exc)[:300],
            "repair_status": str(table.get("repair_status") or ""),
            "policy": POLICY_SUMMARY,
            "policy_trace": policy_trace,
        }
        table["_reconstruct"] = snap
        return snap


def run_payload_reconstruct(
    payload: Dict[str, Any],
    *,
    redetect_anomaly=None,
    run_llm: bool = False,
    liteparse_data: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int, int, int]:
    """遍历 payload 内表格跑还原主链。

    返回 (ok_or_rules, llm_like, human, skipped)。
    """
    tables = []
    if isinstance(payload, dict):
        tables = list(payload.get("tables") or [])
    ok = llm = human = skipped = 0
    for t in tables:
        if not isinstance(t, dict) or t.get("type") in ("text", "paragraph"):
            continue
        snap = run_table_reconstruct(
            t,
            redetect_anomaly=redetect_anomaly,
            run_llm=run_llm,
            llm_apply=False,
            liteparse_data=liteparse_data,
        )
        stage = str(snap.get("stage") or "")
        if stage == "skipped_non_data":
            skipped += 1
        elif stage == "needs_human":
            human += 1
        elif stage in ("llm_done",):
            llm += 1
        else:
            ok += 1
    return ok, llm, human, skipped
