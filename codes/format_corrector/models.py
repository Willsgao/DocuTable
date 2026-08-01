# -*- coding: utf-8 -*-
"""格式纠错 — 数据模型。

硬约束（全模块共享）：
1. 不改变原始数据顺序（行/列/表序只允许续表合并与格内拆分归位）
2. 不丢失任何非空内容；相邻重复也不得擅自删除
3. 不纠正 OCR 错字，只做格式/边界/合并
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskType(str, Enum):
    HEADER_CROSS_PAGE = "header_cross_page"  # 缺表头 → 跨页候选
    EMPTY_SPLIT = "empty_split"              # 连续空行/空列 → 分割可疑
    CROSS_PAGE_MERGE = "cross_page_merge"    # 跨页合并
    TEXT_TABLE_SPLIT = "text_table_split"    # 文表边界


class TaskStatus(str, Enum):
    CANDIDATE = "candidate"
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    BLOCKED = "blocked"  # 守恒校验失败


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


@dataclass
class FormatTask:
    """单条纠错任务。"""
    task_id: str
    task_type: TaskType
    table_index: int
    related_indices: List[int] = field(default_factory=list)
    page: int = 0
    status: TaskStatus = TaskStatus.CANDIDATE
    confidence: Confidence = Confidence.MEDIUM
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    # 提议的数据变更（不直接改原表，由 engine.apply 写入）
    proposal: Dict[str, Any] = field(default_factory=dict)
    conservation_ok: Optional[bool] = None
    conservation_detail: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["task_type"] = self.task_type.value
        d["status"] = self.status.value
        d["confidence"] = self.confidence.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FormatTask":
        return cls(
            task_id=d["task_id"],
            task_type=TaskType(d["task_type"]),
            table_index=int(d["table_index"]),
            related_indices=list(d.get("related_indices") or []),
            page=int(d.get("page") or 0),
            status=TaskStatus(d.get("status", "candidate")),
            confidence=Confidence(d.get("confidence", "medium")),
            reason=d.get("reason") or "",
            evidence=dict(d.get("evidence") or {}),
            proposal=dict(d.get("proposal") or {}),
            conservation_ok=d.get("conservation_ok"),
            conservation_detail=d.get("conservation_detail") or "",
        )


@dataclass
class FormatCorrectionReport:
    """一次纠错跑批的完整报告。"""
    pdf_path: str = ""
    tasks: List[FormatTask] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    # 合并后产生的新 tables 快照（仅含被改动的索引映射说明）
    # 真正写回由 apply_to_tables 完成
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pdf_path": self.pdf_path,
            "tasks": [t.to_dict() for t in self.tasks],
            "summary": self.summary,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FormatCorrectionReport":
        return cls(
            pdf_path=d.get("pdf_path") or "",
            tasks=[FormatTask.from_dict(t) for t in d.get("tasks") or []],
            summary=dict(d.get("summary") or {}),
            notes=list(d.get("notes") or []),
        )
