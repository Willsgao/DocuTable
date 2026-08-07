# -*- coding: utf-8 -*-
"""凝结核审核失败时的文本粘连专项：检出 → 就地拆粘 → 供复检。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def _cell(row: Sequence[Any], c: int) -> str:
    if c < 0 or c >= len(row):
        return ""
    return str(row[c] or "").strip()


def classify_glue_cell(text: str) -> Optional[str]:
    """返回粘连类型；非粘连则 None。"""
    t = str(text or "").strip()
    if not t:
        return None
    try:
        from codes.table_engine.geometry.numeric import split_percent_point_change_text

        if split_percent_point_change_text(t):
            return "percent_point"
    except Exception:
        pass
    # 多单位表头：（股） （人民币百万元） …
    try:
        from codes.reconstruct.grid_nucleus.header_align import _split_multi_unit_header

        if _split_multi_unit_header(t):
            return "multi_unit_header"
    except Exception:
        pass
    try:
        from codes.v2_steps.table_glue_repair import split_glue_cell
        from codes.v2_steps.table_anomaly_rules import _looks_like_numeric_text_glue

        parts = split_glue_cell(t)
        if parts:
            left, right = parts
            if "金额" in left and ("率" in right or "比率" in right):
                return "dual_metric_header"
            if _looks_like_numeric_text_glue(t):
                return "numeric_text"
            return "dual_header"
        if _looks_like_numeric_text_glue(t):
            return "numeric_text"
    except Exception:
        pass
    return None


def scan_grid_glue(data: Sequence[Sequence[Any]]) -> Dict[str, Any]:
    """扫描网格粘连：位置、类型、是否可拆。"""
    hits: List[Dict[str, Any]] = []
    for ri, row in enumerate(data or []):
        if not isinstance(row, list):
            continue
        for ci, cell in enumerate(row):
            kind = classify_glue_cell(str(cell or ""))
            if not kind:
                continue
            hits.append({
                "row": ri,
                "col": ci,
                "kind": kind,
                "snippet": str(cell or "").strip()[:40],
                "reparable": True,
            })
    kinds = sorted({h["kind"] for h in hits})
    return {
        "has_glue": bool(hits),
        "count": len(hits),
        "kinds": kinds,
        "hits": hits[:24],
        "reparable": bool(hits),
    }


def _spill_pair_into_empty_right(
    row: List[Any],
    col: int,
    left: str,
    right: str,
) -> bool:
    """右邻空则就地拆入，不插列。"""
    if col + 1 >= len(row):
        return False
    if _cell(row, col + 1):
        return False
    row[col] = left
    row[col + 1] = right
    return True


def try_repair_grid_glue(
    data: List[List[str]],
) -> Tuple[List[List[str]], Dict[str, Any]]:
    """就地拆粘连（优先 spill 右空列；必要时插列拆金额+文本）。"""
    meta: Dict[str, Any] = {
        "repaired": False,
        "actions": [],
        "spill": 0,
        "insert_cols": 0,
    }
    if not data:
        return data, meta

    out = [list(r) if isinstance(r, list) else [] for r in data]
    n_cols = max((len(r) for r in out), default=0)
    for r in out:
        while len(r) < n_cols:
            r.append("")

    # 1) 百分点 / 双表头 / 可拆双词：右邻空则 spill
    for ri, row in enumerate(out):
        for ci in range(len(row) - 1):
            cell = _cell(row, ci)
            kind = classify_glue_cell(cell)
            if not kind:
                continue
            pair = None
            if kind == "percent_point":
                try:
                    from codes.table_engine.geometry.numeric import (
                        split_percent_point_change_text,
                    )

                    pair = split_percent_point_change_text(cell)
                except Exception:
                    pair = None
            elif kind == "multi_unit_header":
                try:
                    from codes.reconstruct.grid_nucleus.header_align import (
                        _split_multi_unit_header,
                    )

                    units = _split_multi_unit_header(cell)
                except Exception:
                    units = None
                if units and len(units) >= 2:
                    # 多单位：写入本列起连续空列
                    need = len(units) - 1
                    if ci + need < len(row) and not any(
                        _cell(row, ci + i) for i in range(1, need + 1)
                    ):
                        for i, u in enumerate(units):
                            row[ci + i] = u
                        meta["spill"] += 1
                        meta["actions"].append(
                            f"spill:{ri},{ci}:multi_unit:" + "|".join(units)
                        )
                continue
            else:
                try:
                    from codes.v2_steps.table_glue_repair import split_glue_cell

                    pair = split_glue_cell(cell)
                except Exception:
                    pair = None
            if not pair:
                continue
            left, right = pair
            if _spill_pair_into_empty_right(row, ci, left, right):
                meta["spill"] += 1
                meta["actions"].append(
                    f"spill:{ri},{ci}:{kind}:{left}|{right}"
                )

    # 2) 仍残留金额+文本粘连：插列拆（与 table_glue_repair 一致，最多 2 次）
    try:
        from codes.v2_steps.table_glue_repair import (
            _split_column_in_data,
            split_glue_cell,
        )
        from codes.v2_steps.table_anomaly_rules import _looks_like_numeric_text_glue
    except Exception:
        split_glue_cell = None  # type: ignore
        _looks_like_numeric_text_glue = None  # type: ignore
        _split_column_in_data = None  # type: ignore

    if split_glue_cell is not None and _looks_like_numeric_text_glue is not None:
        for _ in range(2):
            n_cols = max((len(r) for r in out), default=0)
            target = None
            for c in range(n_cols):
                need = False
                for row in out:
                    if c >= len(row):
                        continue
                    t = _cell(row, c)
                    if _looks_like_numeric_text_glue(t) and split_glue_cell(t):
                        need = True
                        break
                if need:
                    target = c
                    break
            if target is None:
                break
            sample = ""
            for row in out:
                if target < len(row):
                    p = split_glue_cell(_cell(row, target))
                    if p:
                        sample = f"{p[0]}|{p[1]}"
                        break
            out = _split_column_in_data(out, target)
            meta["insert_cols"] += 1
            meta["actions"].append(
                f"insert_col:{target}" + (f":{sample}" if sample else "")
            )

    remaining = scan_grid_glue(out)
    meta["remaining"] = remaining
    meta["repaired"] = bool(meta["actions"])
    return out, meta


def audit_repair_on_validate_fail(
    data: List[List[str]],
) -> Tuple[List[List[str]], Dict[str, Any]]:
    """校验失败入口：先审计粘连，有则尝试修复。"""
    audit = scan_grid_glue(data)
    report: Dict[str, Any] = {
        "triggered": True,
        "audit": audit,
        "repair": None,
    }
    if not audit.get("has_glue"):
        report["note"] = "no_glue"
        return data, report
    repaired, repair_meta = try_repair_grid_glue(data)
    report["repair"] = repair_meta
    if repair_meta.get("repaired"):
        report["note"] = "glue_repaired"
        return repaired, report
    report["note"] = "glue_unrepaired"
    return data, report
