# -*- coding: utf-8 -*-
"""表格问题统一模型：rule_ids → problem_tags → severity。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

# 与产品六类问题对齐的稳定标签
PROBLEM_TAGS = (
    "data_loss",
    "cell_glue",
    "misalignment",
    "wrap_split",
    "hierarchy_lost",
    "merge_lost",
)

SEVERITY_AUTO = "auto_fixable"
SEVERITY_LLM = "llm_candidate"
SEVERITY_HUMAN = "human_only"

# rule_id → tags
_RULE_TO_TAGS: Dict[str, tuple[str, ...]] = {
    "R10_numeric_text_glue": ("cell_glue",),
    "R05_text_in_numeric": ("cell_glue", "misalignment"),
    "R04_merged_numeric": ("cell_glue", "misalignment"),
    "R02_merged_in_short_col": ("merge_lost", "misalignment"),
    "R03_stacked_long_text": ("wrap_split", "hierarchy_lost"),
    "R08_header_data_misalign": ("misalignment",),
    "R07_word_crosses_columns": ("misalignment", "cell_glue"),
    "R06_ghost_column": ("data_loss", "misalignment"),
    "R01_orphan_extension": ("hierarchy_lost", "wrap_split"),
    "R09_interior_singleton": ("misalignment",),
    "C02_unrecognized_data_row": ("misalignment", "hierarchy_lost"),
    "C03_column_type_violation": ("misalignment",),
    "C04_incomplete_data_row": ("data_loss", "misalignment"),
    "C01_missing_header": ("merge_lost",),
    "C01_no_header_band": ("merge_lost",),
}

# tag → 默认处置（可被路由器降级）
_TAG_DEFAULT_SEVERITY: Dict[str, str] = {
    "data_loss": SEVERITY_HUMAN,
    "cell_glue": SEVERITY_AUTO,
    "misalignment": SEVERITY_LLM,
    "wrap_split": SEVERITY_AUTO,
    "hierarchy_lost": SEVERITY_LLM,
    "merge_lost": SEVERITY_LLM,
}


@dataclass
class TableProblemReport:
    table_id: str = ""
    page: int = 0
    rule_ids: List[str] = field(default_factory=list)
    problem_tags: List[str] = field(default_factory=list)
    severity: str = SEVERITY_AUTO  # 表级：取最严重
    evidence: Dict[str, Any] = field(default_factory=dict)
    repair_status: str = "none"
    # none | rule_fixed | llm_proposed | llm_applied | human_needed | human_done

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _severity_rank(sev: str) -> int:
    return {
        SEVERITY_AUTO: 0,
        SEVERITY_LLM: 1,
        SEVERITY_HUMAN: 2,
    }.get(sev, 0)


def _merge_severity(tags: Sequence[str]) -> str:
    best = SEVERITY_AUTO
    for tag in tags:
        sev = _TAG_DEFAULT_SEVERITY.get(tag, SEVERITY_LLM)
        if _severity_rank(sev) > _severity_rank(best):
            best = sev
    return best


def _heuristic_wrap_split(data: Sequence[Sequence[Any]]) -> bool:
    """相邻行真折行，或单行内父级与「- 分项」粘连。"""
    import re

    from codes.table_repair.wrap_repair import split_glued_hierarchy_label

    amount_re = re.compile(r"[\d,]{3,}|\d+\.\d+")
    for row in data:
        if not row:
            continue
        if split_glued_hierarchy_label(str(row[0] or "")):
            return True
    for i in range(len(data) - 1):
        a = [str(c or "").strip() for c in (data[i] or [])]
        b = [str(c or "").strip() for c in (data[i + 1] or [])]
        if not a or not b:
            continue
        a_amt = sum(1 for c in a if amount_re.search(c))
        b_amt = sum(1 for c in b if amount_re.search(c))
        b_label = b[0] if b else ""
        # 下行是 －分项：不是折行续写
        if re.match(r"^[\-－—–]\s*", b_label):
            continue
        if a_amt >= 1 and b_amt == 0 and 2 <= len(b_label) <= 40:
            if re.match(r"^[(（]?\d+[)）.．、]", b_label):
                continue
            if re.search(r"\d{4}\s*年", b_label):
                continue
            a_label = a[0] if a else ""
            if b_label.startswith(("的", "及", "与", "和")) or len(b_label) <= 12:
                if len(a_label) >= 8 or len(b_label) <= 16:
                    return True
    return False


def _heuristic_hierarchy_lost(data: Sequence[Sequence[Any]]) -> bool:
    """左列出现编号层级但整表无 level 元数据时标候选。"""
    import re

    if not data or len(data) < 4:
        return False
    numbered = 0
    for row in data[1:]:
        if not row:
            continue
        lab = str(row[0] or "").strip()
        if re.match(r"^[(（]?\d+[)）.．、]", lab) or re.match(
            r"^[一二三四五六七八九十]+[、.．]", lab
        ):
            numbered += 1
    return numbered >= 3


def _heuristic_data_loss(table: Dict[str, Any], data: Sequence[Sequence[Any]]) -> bool:
    """源映射大面积空、或守恒告警 → 疑似丢失（只转人工，不自动补数）。"""
    if table.get("_conservation_failed") or table.get("_item_conservation_failed"):
        return True
    sources = table.get("_cell_source_items")
    if not isinstance(sources, list) or not data:
        return False
    cells = 0
    empty_src = 0
    for ri, row in enumerate(data):
        if ri >= len(sources):
            break
        src_row = sources[ri] if isinstance(sources[ri], list) else []
        for ci, cell in enumerate(row or []):
            text = str(cell or "").strip()
            if not text:
                continue
            cells += 1
            src = src_row[ci] if ci < len(src_row) else None
            if not src:
                empty_src += 1
    if cells >= 8 and empty_src / max(cells, 1) >= 0.55:
        return True
    return False


def tags_from_rule_ids(rule_ids: Sequence[str]) -> List[str]:
    tags: Set[str] = set()
    for rid in rule_ids or []:
        for t in _RULE_TO_TAGS.get(str(rid), ()):
            tags.add(t)
    return sorted(tags)


def build_problem_report(table: Dict[str, Any]) -> TableProblemReport:
    """从 legacy 表 dict + `_anomaly` 生成问题报告。"""
    anomaly = table.get("_anomaly") or {}
    rule_ids = [str(r) for r in (anomaly.get("rule_ids") or [])]
    data = table.get("data") or []
    if not isinstance(data, list):
        data = []

    tags = set(tags_from_rule_ids(rule_ids))

    if table.get("_glue_repaired"):
        tags.add("cell_glue")

    if _heuristic_wrap_split(data):
        tags.add("wrap_split")
    if _heuristic_hierarchy_lost(data):
        tags.add("hierarchy_lost")
    if _heuristic_data_loss(table, data):
        tags.add("data_loss")

    # 文本/非表不进修复路由
    if table.get("type") in ("text", "paragraph") or table.get("is_real_table") is False:
        if table.get("table_category") in ("页眉", "页脚", "文本段落"):
            return TableProblemReport(
                table_id=str(table.get("table_id", "")),
                page=int(table.get("page") or 0),
                rule_ids=rule_ids,
                problem_tags=[],
                severity=SEVERITY_AUTO,
                evidence={"skipped": "non_table_entry"},
                repair_status=str(table.get("repair_status") or "none"),
            )

    tag_list = sorted(t for t in tags if t in PROBLEM_TAGS)
    severity = _merge_severity(tag_list) if tag_list else SEVERITY_AUTO
    # 无问题但有 needs_review → 至少 llm_candidate
    if not tag_list and anomaly.get("needs_review"):
        severity = SEVERITY_LLM
        tag_list = ["misalignment"]

    existing_status = str(table.get("repair_status") or "none")
    if severity == SEVERITY_HUMAN and existing_status in ("none", "rule_fixed"):
        repair_status = "human_needed"
    else:
        repair_status = existing_status

    return TableProblemReport(
        table_id=str(table.get("table_id", "")),
        page=int(table.get("page") or 0),
        rule_ids=rule_ids,
        problem_tags=tag_list,
        severity=severity,
        evidence={
            "anomaly_score": anomaly.get("anomaly_score"),
            "header_missing": anomaly.get("header_missing"),
            "needs_review": anomaly.get("needs_review"),
            "glue_repaired": bool(table.get("_glue_repaired")),
            "category": table.get("table_category"),
        },
        repair_status=repair_status,
    )


def attach_problem_report(table: Dict[str, Any]) -> TableProblemReport:
    report = build_problem_report(table)
    table["_problem_report"] = report.to_dict()
    table["repair_status"] = report.repair_status
    return report
