# -*- coding: utf-8 -*-
"""表格异常检测桥接：words 裁剪、边界推导、终检入口（V2 / Table Engine 共用）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from codes.v2_steps.step1_column_split import Step1ColumnSplit


def table_bbox(table: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """从 legacy 表 dict 解析 (x0, y0, x1, y1)。优先 bbox，其次顶层坐标。"""
    bbox = table.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    if table.get("x0") is not None and table.get("y0") is not None:
        return (
            float(table["x0"]),
            float(table["y0"]),
            float(table.get("x1", table["x0"])),
            float(table.get("y1", table["y0"])),
        )
    return None


def bbox_overlap_ratio(
    ax0: float, ay0: float, ax1: float, ay1: float,
    bx0: float, by0: float, bx1: float, by1: float,
) -> float:
    """两矩形交集面积 / 较小矩形面积。"""
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max((ax1 - ax0) * (ay1 - ay0), 1e-6)
    area_b = max((bx1 - bx0) * (by1 - by0), 1e-6)
    return inter / min(area_a, area_b)


def item_overlaps_table_bbox(
    item: Dict[str, Any],
    region: Tuple[float, float, float, float],
    *,
    min_overlap: float = 0.15,
) -> bool:
    """item bbox 与表区域有足够重叠则纳入 words。"""
    ix0 = float(item.get("x0", 0))
    iy0 = float(item.get("y0", 0))
    ix1 = float(item.get("x1", ix0))
    iy1 = float(item.get("y1", iy0))
    if ix1 <= ix0:
        ix1 = ix0 + 1.0
    if iy1 <= iy0:
        iy1 = iy0 + 1.0
    rx0, ry0, rx1, ry1 = region
    ratio = bbox_overlap_ratio(ix0, iy0, ix1, iy1, rx0, ry0, rx1, ry1)
    if ratio >= min_overlap:
        return True
    # 中心点在表内也算命中（窄条 item 与表区域轻微错位）
    cx = (ix0 + ix1) / 2.0
    cy = (iy0 + iy1) / 2.0
    return rx0 <= cx <= rx1 and ry0 <= cy <= ry1


def word_dict_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "x0": float(item.get("x0", 0)),
        "x1": float(item.get("x1", item.get("x0", 0))),
        "y0": float(item.get("y0", 0)),
        "y1": float(item.get("y1", item.get("y0", 0))),
        "text": str(item.get("text", "")),
    }


def words_from_text_items(
    text_items: Sequence[Dict[str, Any]],
    region: Optional[Tuple[float, float, float, float]] = None,
) -> List[Dict[str, Any]]:
    """liteparse text_items → 异常检测用 words 列表。"""
    out: List[Dict[str, Any]] = []
    for item in text_items:
        if not str(item.get("text", "")).strip():
            continue
        if region is not None and not item_overlaps_table_bbox(item, region):
            continue
        out.append(word_dict_from_item(item))
    return out


def filter_ctx_words(
    words: Sequence[Dict[str, Any]],
    region: Tuple[float, float, float, float],
) -> List[Dict[str, Any]]:
    """PyMuPDF ctx.words 按表 bbox 裁剪。"""
    return [
        w for w in words
        if item_overlaps_table_bbox(w, region)
    ]


def col_bounds_for_anomaly(
    table: Dict[str, Any],
    n_cols: int,
) -> Optional[List[float]]:
    """列边界：优先 _col_bounds，否则等宽反推。"""
    col_bounds = table.get("_col_bounds")
    if isinstance(col_bounds, list) and len(col_bounds) == n_cols + 1:
        return [float(x) for x in col_bounds]

    region = table_bbox(table)
    if region is None or n_cols < 1:
        return None
    rx0, _, rx1, _ = region
    col_w = (rx1 - rx0) / n_cols
    return [rx0 + i * col_w for i in range(n_cols + 1)]


def row_bounds_for_anomaly(
    table: Dict[str, Any],
    n_rows: int,
) -> List[Tuple[float, float]]:
    """行边界：优先 _row_bounds，否则等高分摊。"""
    row_bounds = table.get("_row_bounds")
    if isinstance(row_bounds, list) and len(row_bounds) == n_rows:
        return [(float(a), float(b)) for a, b in row_bounds]

    region = table_bbox(table)
    if region is None or n_rows < 1:
        return [(0.0, 1.0)] * max(n_rows, 1)
    _, ry0, _, ry1 = region
    row_h = (ry1 - ry0) / n_rows
    return [
        (ry0 + i * row_h, ry0 + (i + 1) * row_h)
        for i in range(n_rows)
    ]


# 报告版本：契约规则变更时递增，触发缓存补检
ANOMALY_REPORT_VERSION = 8  # R10 + 缓存表就地拆列修复


def _empty_anomaly_report() -> Dict[str, Any]:
    return {
        "version": ANOMALY_REPORT_VERSION,
        "is_normal_table": True,
        "has_anomalies": False,
        "header_missing": False,
        "anomaly_class": "none",
        "anomaly_score": 0.0,
        "needs_review": False,
        "empty_cols": [],
        "empty_rows": [],
        "length_outliers": [],
        "mixed_type_cells": [],
        "merged_values": [],
        "cross_boundary_words": [],
        "reasons": [],
        "issues": [],
        "rule_ids": [],
    }


def _iter_table_buckets(
    payload: Any,
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    if isinstance(payload, dict):
        out: List[Tuple[str, List[Dict[str, Any]]]] = []
        for key in ("tables", "tables_before_segmentation"):
            lst = payload.get(key)
            if isinstance(lst, list):
                out.append((key, lst))
        return out
    if isinstance(payload, list):
        return [("tables", payload)]
    return []


def apply_anomaly_table_category(table: Dict[str, Any], report: Dict[str, Any]) -> None:
    """表头缺失单独归类：可续表/跨页合并候选，不当作质量异常。"""
    if not report.get("header_missing"):
        return
    cat = str(table.get("table_category") or "").strip()
    if cat in ("", "财务数据表", "非标准表格"):
        table["table_category"] = "数据表(缺表头)"
    table["has_header"] = False
    if not report.get("needs_review"):
        table["quality_decision"] = "header_missing"


def ensure_anomaly_reports(
    payload: Any,
    *,
    pdf_path: Optional[str] = None,
    liteparse_data: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> int:
    """对 payload 内所有 table 写入/刷新 _anomaly。

    返回 needs_review=True 的表格数量。
    """
    if liteparse_data is None and pdf_path:
        try:
            from codes.liteparse_extractor.cache_manager import load_parse_result
            lp_result = load_parse_result(pdf_path)
            if lp_result is not None:
                liteparse_data = lp_result.to_dict()
        except Exception:
            liteparse_data = None

    flagged = 0
    for _bucket, tables in _iter_table_buckets(payload):
        for table in tables:
            if table.get("type") != "table":
                continue
            data = table.get("data") or []
            if len(data) < 2 or not data[0] or len(data[0]) < 2:
                table["_anomaly"] = _empty_anomaly_report()
                continue

            # 金额+文本粘连：先就地拆列，再质检（缓存旧表也能立刻修好）
            force_this = force
            try:
                from codes.v2_steps.table_glue_repair import (
                    repair_table_numeric_text_glue,
                )

                glue_notes = repair_table_numeric_text_glue(table)
                if glue_notes:
                    force_this = True
                    for n in glue_notes:
                        print(f"  [GlueRepair] {n}")
            except Exception as exc:
                print(f"  [GlueRepair] skip: {exc}")

            existing = table.get("_anomaly") or {}
            if (
                not force_this
                and existing.get("version") == ANOMALY_REPORT_VERSION
                and "needs_review" in existing
            ):
                apply_anomaly_table_category(table, existing)
                if existing.get("needs_review"):
                    flagged += 1
                continue

            lp_page = None
            if liteparse_data:
                lp_page = _get_liteparse_page_for_anomaly(
                    liteparse_data, table.get("page"),
                )

            report = detect_anomalies_for_table(
                table, liteparse_page=lp_page,
            )
            report["version"] = ANOMALY_REPORT_VERSION
            table["_anomaly"] = report
            apply_anomaly_table_category(table, report)
            if report.get("needs_review"):
                flagged += 1
                print(
                    f"  [Anomaly] P{table.get('page')} "
                    f"表标记异常 score={report.get('anomaly_score', 0):.2f} "
                    f"rules={report.get('rule_ids', [])}"
                )
            elif report.get("header_missing"):
                print(
                    f"  [Anomaly] P{table.get('page')} "
                    f"表头缺失（单独归类） rules={report.get('rule_ids', [])}"
                )
    return flagged


def _get_liteparse_page_for_anomaly(
    liteparse_data: Dict[str, Any],
    page_num: Any,
) -> Optional[Dict[str, Any]]:
    if page_num is None:
        return None
    pages = liteparse_data.get("pages", [])
    for p in pages:
        if p.get("page_number") == page_num or p.get("page") == page_num:
            return p
    return None


def detect_anomalies_for_table(
    table: Dict[str, Any],
    *,
    words: Optional[Sequence[Dict[str, Any]]] = None,
    liteparse_page: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对 legacy 表 dict 的最终 data 跑异常检测。

    words 优先使用调用方传入；否则从 liteparse_page.text_items 按 bbox 裁剪。
    """
    empty_report: Dict[str, Any] = _empty_anomaly_report()

    data = table.get("data", [])
    if not data or len(data) < 2 or not data[0] or len(data[0]) < 2:
        return empty_report

    from codes.v2_steps.table_anomaly_rules import strip_blank_rows_cols
    cleaned = strip_blank_rows_cols([list(row) for row in data])
    if cleaned and cleaned != data:
        table["data"] = cleaned
        table["rows"] = len(cleaned)
        if cleaned[0]:
            table["cols"] = len(cleaned[0])
    data = table.get("data", [])
    if len(data) < 2 or not data[0] or len(data[0]) < 2:
        return empty_report

    n_rows = len(data)
    n_cols = len(data[0])
    region = table_bbox(table)

    region_words: List[Dict[str, Any]] = []
    if words is not None:
        if region is not None:
            region_words = filter_ctx_words(words, region)
        else:
            region_words = list(words)
    elif liteparse_page:
        region_words = words_from_text_items(
            liteparse_page.get("text_items", []),
            region,
        )

    col_bounds = col_bounds_for_anomaly(table, n_cols)
    row_bounds = row_bounds_for_anomaly(table, n_rows)

    return Step1ColumnSplit._detect_table_anomalies(
        data, region_words, row_bounds, col_bounds,
    )


def anomaly_reasons_to_step4_list(raw_anomaly: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Step1 _anomaly → Step4 路由可消费的 anomalies 列表。"""
    if not raw_anomaly.get("has_anomalies"):
        return []
    score = float(raw_anomaly.get("anomaly_score", 0.0))
    severity = "high" if score >= 0.5 else "medium"
    return [
        {
            "type": "column_merge_anomaly",
            "severity": severity,
            "confidence": max(0.0, 1.0 - score),
            "description": reason,
        }
        for reason in raw_anomaly.get("reasons", [])
    ]
