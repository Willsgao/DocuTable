# -*- coding: utf-8 -*-
"""统一人工队列：收集待审表 → 接受/拒绝提案 → 写回 tables。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from codes.table_repair.validator import normalize_grid

# 进入人工队列的状态（刻意不含 llm_candidate）
# - llm_proposed：AI 已给出提案，等人接受/拒绝
# - human_needed：规则/校验认定必须人收口（如疑似丢数、LLM 失败）
# llm_candidate 只表示「还可让 AI 试」，不应堆进人工队列
QUEUE_STATUSES = frozenset({
    "human_needed",
    "llm_proposed",
})

DECISION_PENDING = "pending"
DECISION_ACCEPTED = "accepted"
DECISION_REJECTED = "rejected"


@dataclass
class HumanQueueItem:
    table_index: int
    page: int = 0
    caption: str = ""
    table_category: str = ""
    repair_status: str = ""
    problem_tags: List[str] = field(default_factory=list)
    rule_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    llm_error: str = ""
    reasoning_summary: str = ""
    report_text: str = ""
    has_proposal: bool = False
    before_data: List[List[str]] = field(default_factory=list)
    proposed_data: List[List[str]] = field(default_factory=list)
    decision: str = DECISION_PENDING  # pending | accepted | rejected

    @property
    def item_id(self) -> str:
        return f"t{self.table_index}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table_index": self.table_index,
            "page": self.page,
            "caption": self.caption,
            "table_category": self.table_category,
            "repair_status": self.repair_status,
            "problem_tags": list(self.problem_tags),
            "rule_ids": list(self.rule_ids),
            "confidence": self.confidence,
            "llm_error": self.llm_error,
            "reasoning_summary": self.reasoning_summary,
            "report_text": self.report_text,
            "has_proposal": self.has_proposal,
            "before_data": self.before_data,
            "proposed_data": self.proposed_data,
            "decision": self.decision,
        }


def _page_of(table: Dict[str, Any]) -> int:
    for key in ("page", "page_num", "page_index"):
        v = table.get(key)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return 0


def _caption_of(table: Dict[str, Any]) -> str:
    for key in ("caption", "title", "name", "ai_name"):
        v = table.get(key)
        if v:
            return str(v)[:80]
    return ""


def store_llm_proposal(
    table: Dict[str, Any],
    *,
    before_data: Sequence[Sequence[Any]],
    repaired_table: Sequence[Sequence[Any]],
    success: bool,
    confidence: float = 0.0,
    validation_errors: Optional[Sequence[str]] = None,
    llm_error: str = "",
    problem_tags: Optional[Sequence[str]] = None,
    reasoning_summary: str = "",
    report_text: str = "",
    set_status: bool = True,
) -> Dict[str, Any]:
    """把完整前后表写入 table['_llm_proposal']，供人工队列审阅。"""
    before = normalize_grid(before_data)
    after = normalize_grid(repaired_table)
    proposal = {
        "success": bool(success),
        "confidence": float(confidence or 0.0),
        "validation_errors": list(validation_errors or []),
        "llm_error": str(llm_error or ""),
        "problem_tags": list(problem_tags or []),
        "reasoning_summary": str(reasoning_summary or ""),
        "report_text": str(report_text or ""),
        "before_data": before,
        "repaired_table": after,
    }
    table["_llm_proposal"] = proposal
    if set_status:
        if success and after:
            table["repair_status"] = "llm_proposed"
        else:
            table["repair_status"] = "human_needed"
        pr = dict(table.get("_problem_report") or {})
        pr["repair_status"] = table["repair_status"]
        if proposal["problem_tags"] and not pr.get("problem_tags"):
            pr["problem_tags"] = list(proposal["problem_tags"])
        table["_problem_report"] = pr
    return proposal


def collect_human_queue(
    tables: Sequence[Dict[str, Any]],
    *,
    include_statuses: Optional[Sequence[str]] = None,
) -> List[HumanQueueItem]:
    """从 tables 收集需人工处理的条目。"""
    wanted = frozenset(include_statuses or QUEUE_STATUSES)
    items: List[HumanQueueItem] = []
    for idx, table in enumerate(tables or []):
        if not isinstance(table, dict):
            continue
        if table.get("type") in ("text", "paragraph"):
            continue
        status = str(table.get("repair_status") or "")
        if status == "skipped_non_data":
            continue
        kind = (table.get("_table_kind") or {}).get("kind")
        if kind in ("toc", "non_data") and status not in (
            "llm_proposed", "human_needed",
        ):
            continue
        proposal = table.get("_llm_proposal") or {}
        has_prop = bool(
            isinstance(proposal, dict)
            and proposal.get("repaired_table")
            and proposal.get("success", True)
        )
        has_unapplied = has_prop and not proposal.get("applied")
        if status not in wanted and not has_unapplied:
            continue

        pr = table.get("_problem_report") or {}
        tags = list(pr.get("problem_tags") or proposal.get("problem_tags") or [])
        rules = list(
            pr.get("rule_ids")
            or ((table.get("_anomaly") or {}).get("rule_ids") or [])
        )
        before = proposal.get("before_data")
        if not before:
            before = normalize_grid(table.get("data") or [])
        else:
            before = normalize_grid(before)
        proposed = normalize_grid(proposal.get("repaired_table") or []) if has_prop else []

        items.append(
            HumanQueueItem(
                table_index=idx,
                page=_page_of(table),
                caption=_caption_of(table),
                table_category=str(table.get("table_category") or ""),
                repair_status=status or ("llm_proposed" if has_prop else "human_needed"),
                problem_tags=tags,
                rule_ids=[str(r) for r in rules],
                confidence=float(proposal.get("confidence") or 0.0),
                llm_error=str(proposal.get("llm_error") or ""),
                reasoning_summary=str(proposal.get("reasoning_summary") or ""),
                report_text=str(proposal.get("report_text") or ""),
                has_proposal=has_prop,
                before_data=before,
                proposed_data=proposed,
            )
        )
    return items


def apply_proposal_to_table(
    table: Dict[str, Any],
    proposed_data: Sequence[Sequence[Any]],
    *,
    status: str = "llm_applied",
) -> None:
    """把提案网格写回表，并更新状态。"""
    grid = normalize_grid(proposed_data)
    table["data"] = grid
    table["rows"] = len(grid)
    table["cols"] = max((len(r) for r in grid), default=0)
    table.pop("_cell_source_items", None)
    table["repair_status"] = status
    if status == "llm_applied":
        table["_llm_repaired"] = True
    pr = dict(table.get("_problem_report") or {})
    pr["repair_status"] = status
    table["_problem_report"] = pr
    prop = dict(table.get("_llm_proposal") or {})
    prop["applied"] = True
    prop["decision"] = DECISION_ACCEPTED
    table["_llm_proposal"] = prop


def reject_proposal_on_table(
    table: Dict[str, Any],
    *,
    status: str = "human_needed",
) -> None:
    """拒绝提案：保留当前 data，清掉可应用提案，标回需人工/已处理。"""
    prop = dict(table.get("_llm_proposal") or {})
    prop["decision"] = DECISION_REJECTED
    prop["applied"] = False
    # 保留 before/repaired 供事后查看，但不视为可应用
    prop["success"] = False
    table["_llm_proposal"] = prop
    table["repair_status"] = status
    pr = dict(table.get("_problem_report") or {})
    pr["repair_status"] = status
    table["_problem_report"] = pr


def mark_human_done(table: Dict[str, Any]) -> None:
    """标记人工已处理完毕（通常手改后）。"""
    table["repair_status"] = "human_done"
    pr = dict(table.get("_problem_report") or {})
    pr["repair_status"] = "human_done"
    table["_problem_report"] = pr
    prop = dict(table.get("_llm_proposal") or {})
    prop["decision"] = DECISION_ACCEPTED
    prop["applied"] = True
    table["_llm_proposal"] = prop


def apply_queue_decisions(
    tables: List[Dict[str, Any]],
    items: Sequence[HumanQueueItem],
) -> Tuple[int, int, int]:
    """按条目 decision 批量写回。

    返回 (accepted_count, rejected_count, skipped_count)。
    """
    accepted = rejected = skipped = 0
    for item in items:
        idx = item.table_index
        if idx < 0 or idx >= len(tables):
            skipped += 1
            continue
        table = tables[idx]
        if not isinstance(table, dict):
            skipped += 1
            continue
        if item.decision == DECISION_ACCEPTED:
            if item.has_proposal and item.proposed_data:
                apply_proposal_to_table(table, item.proposed_data, status="llm_applied")
            else:
                # 无提案：视为确认当前表已手改完成
                mark_human_done(table)
            accepted += 1
        elif item.decision == DECISION_REJECTED:
            reject_proposal_on_table(table, status="human_needed")
            rejected += 1
        else:
            skipped += 1
    return accepted, rejected, skipped


def snapshot_tables(tables: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return deepcopy(list(tables or []))
