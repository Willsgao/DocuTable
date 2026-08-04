# -*- coding: utf-8 -*-
"""还原阶段快照：标记表是否达到「可进人审/可考虑入库」的中间态。

注意：accepted_for_ingest 默认 False；入库闸门（C 层）落地前不得据此写库。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

RECONSTRUCT_VERSION = 1

# 阶段名（便于日志/UI）
STAGE_SKIPPED = "skipped_non_data"
STAGE_RULES = "rules_done"
STAGE_LLM = "llm_done"
STAGE_HUMAN = "needs_human"
STAGE_OK = "structure_ok"
STAGE_ERROR = "error"


def build_reconstruct_snapshot(
    table: Dict[str, Any],
    *,
    checklist: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """从 table 当前状态生成 `_reconstruct` 快照（纯数据，无副作用）。"""
    kind_info = table.get("_table_kind") or {}
    kind = str(kind_info.get("kind") or "unknown")
    status = str(table.get("repair_status") or "none")
    cl = checklist or table.get("_repair_checklist") or {}
    summary = cl.get("summary") or {}
    anomaly = table.get("_anomaly") or {}

    if status == "skipped_non_data" or kind in ("toc", "non_data"):
        stage = STAGE_SKIPPED
    elif status in ("human_needed",):
        stage = STAGE_HUMAN
    elif status in ("llm_proposed", "llm_applied"):
        stage = STAGE_LLM
    elif status in ("rule_fixed", "none") and not anomaly.get("needs_review"):
        # 规则修完且质检未再标红 → 结构上可视为 OK（仍未入库）
        failed = int(summary.get("failed") or 0)
        stage = STAGE_OK if failed == 0 else STAGE_RULES
    elif status in ("rule_fixed", "llm_candidate"):
        stage = STAGE_RULES
    else:
        stage = STAGE_RULES

    return {
        "version": RECONSTRUCT_VERSION,
        "stage": stage,
        "table_kind": kind,
        "table_kind_detail": kind_info if isinstance(kind_info, dict) else {},
        "repair_status": status,
        "needs_review": bool(anomaly.get("needs_review")),
        "checklist_failed_ids": list(summary.get("failed_ids") or [])[:20],
        "checklist_phase": cl.get("phase") or cl.get("phase_final"),
        # 入库闸门未启用前恒为 False
        "accepted_for_ingest": False,
        "domain": "generic",  # 后续金融包可改为 finance
        "source": "liteparse_anchored",
    }
