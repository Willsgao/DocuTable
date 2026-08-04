# -*- coding: utf-8 -*-
"""
表分流（入库前置）
================

在纠错/质检重流程之前区分表种类，避免目录等非数据表进入结构 AI。

种类：
- toc: 目录（章节 + 点线 + 页码）
- non_data: 无稳定金额数据区（说明/封面碎片等）
- data: 有金额数据区，才值得规则修 / LLM / 入库候选
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# 目录引导线 / 页码
_LEADER_RE = re.compile(r"\.{3,}|…{1,}|·{3,}|…+")
_PAGE_TAIL_RE = re.compile(r"(?:\.{2,}|…+|·{2,})\s*\d{1,3}\s*$|^\s*\d{1,3}\s*$")
_SECTION_NO_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,3}(?:\s|$)")
_TOC_TITLE_RE = re.compile(r"(目录|目\s*录|contents|table\s+of\s+contents)", re.I)
# 真金额迹象：千分位、较长整数、两位小数
_REAL_AMOUNT_RE = re.compile(
    r"[\d,]{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d{4,}(?:\.\d+)?"
    r"|\d+\.\d{2}\b"
)


@dataclass
class TableKindResult:
    kind: str  # toc | non_data | data
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    toc_row_ratio: float = 0.0
    amount_cell_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_data_table(self) -> bool:
        return self.kind == "data"

    @property
    def skip_structure_repair(self) -> bool:
        return self.kind in ("toc", "non_data")


def _cell_text(cell: Any) -> str:
    return str(cell or "").strip()


def _row_joined(row: Sequence[Any]) -> str:
    return " ".join(_cell_text(c) for c in (row or []) if _cell_text(c))


def _looks_like_toc_row(row: Sequence[Any]) -> bool:
    """单行像目录项：引导线+页码，或「章节号+长标题+页码」。"""
    parts = [_cell_text(c) for c in (row or []) if _cell_text(c)]
    if not parts:
        return False
    joined = " ".join(parts)
    if _LEADER_RE.search(joined) and re.search(r"\d{1,3}\s*$", joined):
        return True
    # 拆列：一列全是点+页码，另一列是标题
    if any(_PAGE_TAIL_RE.search(p) and (_LEADER_RE.search(p) or p.strip().isdigit()) for p in parts):
        if any(len(p) >= 4 and not p.replace(".", "").isdigit() for p in parts):
            return True
    if _SECTION_NO_RE.match(joined) and len(joined) >= 6 and re.search(r"\d{1,3}\s*$", joined):
        # 有章节号+页码，且几乎无真金额
        if not _REAL_AMOUNT_RE.search(joined.replace(",", "")):
            return True
    return False


def _count_real_amount_cells(data: Sequence[Sequence[Any]]) -> int:
    n = 0
    for row in data or []:
        for cell in row or []:
            t = _cell_text(cell)
            if t and _REAL_AMOUNT_RE.search(t.replace(" ", "")):
                n += 1
    return n


def classify_table_kind(
    data: Sequence[Sequence[Any]],
    *,
    caption: str = "",
    table_category: str = "",
) -> TableKindResult:
    """根据网格内容分流。"""
    grid = [list(r) if isinstance(r, (list, tuple)) else [r] for r in (data or [])]
    out = TableKindResult(kind="data", confidence=0.4, reasons=[])
    if not grid:
        out.kind = "non_data"
        out.confidence = 0.9
        out.reasons.append("empty")
        return out

    # 显式类别/标题
    blob = (caption or "") + " " + (table_category or "")
    if _TOC_TITLE_RE.search(blob):
        out.kind = "toc"
        out.confidence = 0.95
        out.reasons.append("caption/category 含目录")
        return out

    # 首行/前几行含「目录」
    head = " ".join(_row_joined(r) for r in grid[:3])
    if _TOC_TITLE_RE.search(head) and _count_real_amount_cells(grid) < 3:
        out.kind = "toc"
        out.confidence = 0.9
        out.reasons.append("表头区含目录关键词")
        return out

    n_rows = len(grid)
    toc_hits = sum(1 for r in grid if _looks_like_toc_row(r))
    ratio = toc_hits / max(n_rows, 1)
    out.toc_row_ratio = round(ratio, 3)
    amt = _count_real_amount_cells(grid)
    out.amount_cell_count = amt

    # 目录：大量目录行 + 真金额很少
    if n_rows >= 4 and ratio >= 0.45 and amt <= max(2, n_rows // 8):
        out.kind = "toc"
        out.confidence = min(0.98, 0.55 + ratio * 0.4)
        out.reasons.append(
            f"目录行占比 {ratio:.0%}（{toc_hits}/{n_rows}），真金额格 {amt}"
        )
        return out

    if n_rows >= 6 and ratio >= 0.35 and amt == 0:
        out.kind = "toc"
        out.confidence = 0.85
        out.reasons.append(f"无真金额且目录行占比 {ratio:.0%}")
        return out

    # 非数据：几乎无真金额、也不是目录形态
    if amt == 0 and n_rows >= 2:
        # 可能是纯文本说明表
        out.kind = "non_data"
        out.confidence = 0.7
        out.reasons.append("无千分位/大额金额，缺少数据区")
        return out

    if amt <= 1 and n_rows >= 8 and ratio < 0.2:
        out.kind = "non_data"
        out.confidence = 0.65
        out.reasons.append("金额信号过弱")
        return out

    out.kind = "data"
    out.confidence = 0.75 if amt >= 3 else 0.55
    out.reasons.append(f"真金额格 {amt}，按数据表处理")
    return out


def attach_table_kind(table: Dict[str, Any]) -> TableKindResult:
    """写入 table['_table_kind']，返回结果。"""
    if not table or table.get("type") in ("text", "paragraph"):
        res = TableKindResult(kind="non_data", confidence=1.0, reasons=["not_table"])
        table["_table_kind"] = res.to_dict()
        return res
    res = classify_table_kind(
        table.get("data") or [],
        caption=str(table.get("caption") or table.get("title") or ""),
        table_category=str(table.get("table_category") or ""),
    )
    table["_table_kind"] = res.to_dict()
    return res


def get_table_kind(table: Optional[Dict[str, Any]]) -> str:
    if not table:
        return "non_data"
    cached = table.get("_table_kind") or {}
    if isinstance(cached, dict) and cached.get("kind"):
        return str(cached["kind"])
    return attach_table_kind(table).kind


def should_run_structure_repair(table: Optional[Dict[str, Any]]) -> bool:
    """仅数据表进入结构 AI / 重修复。"""
    return get_table_kind(table) == "data"


def should_run_repair_pipeline(table: Optional[Dict[str, Any]]) -> bool:
    """规则修流水线：数据表才跑；目录/非数据直接跳过。"""
    return get_table_kind(table) == "data"
