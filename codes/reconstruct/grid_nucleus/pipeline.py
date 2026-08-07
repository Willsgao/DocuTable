# -*- coding: utf-8 -*-
"""凝结核网格恢复主编排。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from codes.reconstruct.grid_nucleus.assign_cells import assign_to_grid
from codes.reconstruct.grid_nucleus.column_infer import (
    assign_nuclei_to_slots,
    compute_column_bands,
    fix_column_crossings,
    infer_column_slots,
    mark_abnormal_rows,
)
from codes.reconstruct.grid_nucleus.config import GRID_NUCLEUS
from codes.reconstruct.grid_nucleus.fallback import try_fallbacks
from codes.reconstruct.grid_nucleus.glue_audit import audit_repair_on_validate_fail
from codes.reconstruct.grid_nucleus.header_align import align_header_to_body_columns
from codes.reconstruct.grid_nucleus.preprocess import preprocess_words
from codes.reconstruct.grid_nucleus.row_cluster import cluster_rows
from codes.reconstruct.grid_nucleus.split_lines import build_col_lines, build_row_lines
from codes.reconstruct.grid_nucleus.types import GridResult
from codes.reconstruct.grid_nucleus.validate import validate_grid


def _apply_soft_pass(
    ok: bool,
    errs: List[str],
    metrics: Dict[str, Any],
    *,
    cover_thresh: float,
) -> Tuple[bool, List[str]]:
    """覆盖率略低 / 金额列含多行中文表头 → 软通过；粘连须先走专项修复。"""
    if ok or not errs:
        return ok, errs
    soft_ok = True
    critical: List[str] = []
    for e in errs:
        if e.startswith("cover_low"):
            continue
        if e.startswith("value_col_type_break"):
            if float(metrics.get("value_col_amt_ratio") or 0) >= 0.55:
                metrics["value_col_soft_pass"] = True
                continue
        # glue_residual：不软过，交给粘连专项
        critical.append(e)
    cover = float(metrics.get("cover") or 0)
    if critical:
        soft_ok = False
    elif cover < float(cover_thresh) - 0.05:
        soft_ok = False
    if soft_ok:
        metrics["soft_pass"] = True
        return True, []
    return False, errs


def _postprocess_ok_grid(
    data: List[List[str]],
    col_lines: List[float],
) -> Tuple[List[List[str]], List[float], Dict[str, Any]]:
    data, col_lines = prune_empty_columns(data, col_lines)
    data, col_lines, ha = align_header_to_body_columns(
        data, col_lines=col_lines,
    )
    from codes.reconstruct.grid_nucleus.word_segment import (
        strip_footnote_rows_from_data,
        strip_leading_page_chrome_rows_from_data,
        strip_trailing_next_table_header_rows_from_data,
    )

    before_rows = len(data)
    data = strip_footnote_rows_from_data(data)
    data = strip_leading_page_chrome_rows_from_data(data)
    data = strip_trailing_next_table_header_rows_from_data(data)
    ha = dict(ha or {})
    if len(data) < before_rows:
        ha["chrome_or_footnote_rows_stripped"] = before_rows - len(data)

    # 逻辑行：金额锚上下并折行；歧义标 needs_review（缩进已在 assign 前导空格）
    from codes.reconstruct.grid_nucleus.logical_rows import assemble_wrapped_label_rows

    before_lr = len(data)
    data, lr_meta = assemble_wrapped_label_rows(data)
    ha["logical_rows"] = {
        "merges": lr_meta.get("merges") or [],
        "ambiguous_rows": lr_meta.get("ambiguous_rows") or [],
        "label_col": lr_meta.get("label_col"),
        "rows_before": before_lr,
        "rows_after": len(data),
        "needs_review": bool(lr_meta.get("ambiguous_rows")),
    }
    return data, col_lines, ha


def prune_empty_columns(
    data: List[List[str]],
    col_lines: List[float],
    *,
    min_fill: float = 0.02,
) -> Tuple[List[List[str]], List[float]]:
    """去掉几乎全空的列，避免槽位过碎（如 a/b/c 之间的空槽）。"""
    if not data:
        return data, col_lines
    n_cols = max((len(r) for r in data), default=0)
    if n_cols <= 1:
        return data, col_lines
    n_rows = len(data)
    keep: List[int] = []
    for c in range(n_cols):
        filled = sum(
            1 for r in data
            if c < len(r) and str(r[c] or "").strip()
        )
        if filled / max(n_rows, 1) >= min_fill or filled >= 1:
            # 至少有一格非空即保留（表头字母列可能很稀）
            if filled >= 1:
                keep.append(c)
    if len(keep) < 2 or len(keep) == n_cols:
        return data, col_lines
    new_data = [[(row[c] if c < len(row) else "") for c in keep] for row in data]
    new_lines: List[float] = []
    if col_lines and len(col_lines) >= n_cols + 1:
        new_lines = [col_lines[keep[0]]]
        for c in keep:
            new_lines.append(col_lines[c + 1])
        # 去重单调
        mono = [new_lines[0]]
        for x in new_lines[1:]:
            if x <= mono[-1]:
                x = mono[-1] + 1.0
            mono.append(x)
        new_lines = mono
    return new_data, new_lines


def restore_table_grid(
    table: Dict[str, Any],
    *,
    source_words: Optional[List[Dict[str, Any]]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> GridResult:
    """从 liteparse 字框恢复网格。失败返回 ok=False，不抛异常。"""
    cfg = {**GRID_NUCLEUS, **(cfg or {})}
    result = GridResult(ok=False, method="none")

    words = source_words
    if words is None:
        words = table.get("_source_words") or []
    if not words:
        result.errors.append("no_source_words")
        result.method = "fallback_keep"
        return result

    # 字框若跨多段年报表，按当前 data 标签收窄，避免两表混建；并去掉表尾「注：」/表顶页眉字框
    try:
        from codes.reconstruct.grid_nucleus.word_segment import (
            select_source_words_for_data,
            split_source_words_by_year_bands,
            trim_leading_narrative_words,
            trim_leading_page_chrome_words,
            trim_trailing_footnote_words,
            trim_trailing_next_header_words,
        )

        data_hint = table.get("data") or []
        segs = split_source_words_by_year_bands(words)
        if len(segs) > 1 and data_hint:
            narrowed = select_source_words_for_data(words, data_hint)
            if narrowed and len(narrowed) < len(words):
                result.metrics["word_segment_trim"] = (
                    f"{len(words)}->{len(narrowed)}/{len(segs)}"
                )
                words = narrowed
        before_n = len(words)
        words = trim_trailing_footnote_words(words)
        if len(words) < before_n:
            result.metrics["footnote_word_trim"] = f"{before_n}->{len(words)}"
        before_h = len(words)
        words = trim_trailing_next_header_words(words)
        if len(words) < before_h:
            result.metrics["next_header_word_trim"] = f"{before_h}->{len(words)}"
        before_c = len(words)
        words = trim_leading_page_chrome_words(words)
        if len(words) < before_c:
            result.metrics["page_chrome_word_trim"] = f"{before_c}->{len(words)}"
        before_n2 = len(words)
        words = trim_leading_narrative_words(words)
        if len(words) < before_n2:
            result.metrics["narrative_word_trim"] = f"{before_n2}->{len(words)}"
    except Exception:
        pass

    try:
        nuclei = preprocess_words(words)
        if len(nuclei) < 2:
            result.errors.append("nuclei_too_few")
            result.method = "fallback_keep"
            return result

        rows = cluster_rows(nuclei, gap_factor=float(cfg["row_gap_factor"]))
        n_cols, centers = infer_column_slots(
            rows,
            col_gap_factor=float(cfg["col_gap_factor"]),
            max_cols=int(cfg["max_cols"]),
        )
        if n_cols < int(cfg["min_cols"]):
            result.errors.append(f"cols_too_few:{n_cols}")
            # 尝试退化
            packed, chain = try_fallbacks(
                nuclei, rows,
                col_gap_factor=float(cfg["col_gap_factor"]),
                max_cols=int(cfg["max_cols"]),
                cover_thresh=float(cfg["cover_thresh"]),
                reasons=["cols_too_few"],
            )
            if packed:
                ok, bands, row_lines, col_lines, data, method, errs, metrics, chain2 = packed
                result.ok = ok
                result.method = method
                result.data = data
                result.row_lines = row_lines
                result.col_lines = col_lines
                result.n_rows = len(data)
                result.n_cols = len(col_lines) - 1 if col_lines else 0
                result.errors = errs
                result.metrics = {**metrics, "fallback_chain": chain2}
                result.columns_meta = [b.to_dict() for b in bands]
                result.rows_meta = [r.to_dict() for r in rows]
                return result
            result.method = "fallback_keep"
            return result

        assign_nuclei_to_slots(rows, centers)
        mark_abnormal_rows(
            rows, n_cols,
            count_ratio=float(cfg["abnormal_count_ratio"]),
            wide_factor=float(cfg["wide_factor"]),
            cross_eps=float(cfg["cross_eps_pt"]),
        )
        bands = compute_column_bands(rows, n_cols)
        crossed = fix_column_crossings(
            rows, bands,
            cross_eps=float(cfg["cross_eps_pt"]),
            min_gap=float(cfg["min_gap_pt"]),
        )
        adj = max(n_cols - 1, 1)
        cross_ratio = crossed / adj
        result.metrics["cross_pairs"] = crossed
        result.metrics["cross_ratio"] = round(cross_ratio, 3)

        # 先走主路径校验；交叉偏多只作信号，不再直接丢掉可用网格
        # （CC2 等表 cross_ratio≈0.44 时旧逻辑会误进 fallback → cover_low）
        col_lines = build_col_lines(bands)
        row_lines = build_row_lines(rows)
        data = assign_to_grid(rows, n_cols=n_cols, col_lines=col_lines)
        ok, errs, metrics = validate_grid(
            rows, data, row_lines, col_lines,
            cover_thresh=float(cfg["cover_thresh"]),
            min_cols=int(cfg["min_cols"]),
            max_cols=int(cfg["max_cols"]),
        )
        result.metrics.update(metrics)
        result.rows_meta = [r.to_dict() for r in rows]
        result.columns_meta = [b.to_dict() for b in bands]

        ok, errs = _apply_soft_pass(
            ok, errs, metrics, cover_thresh=float(cfg["cover_thresh"]),
        )

        # 校验失败 → 粘连专项：先看有没有文本粘连，能拆则拆后再过软通过
        if not ok:
            data_g, glue_report = audit_repair_on_validate_fail(data)
            metrics["glue_audit"] = glue_report
            result.metrics["glue_audit"] = glue_report
            if glue_report.get("note") == "glue_repaired":
                # 插列后 col_lines 可能对不齐：按新列数重建等分线仅用于复检 cover
                n_new = max((len(r) for r in data_g), default=0)
                if n_new >= 2 and (
                    not col_lines or len(col_lines) != n_new + 1
                ):
                    x0 = float(col_lines[0]) if col_lines else 0.0
                    x1 = float(col_lines[-1]) if col_lines else float(n_new)
                    step = (x1 - x0) / max(n_new, 1)
                    col_lines_g = [x0 + i * step for i in range(n_new + 1)]
                else:
                    col_lines_g = list(col_lines or [])
                ok2, errs2, metrics2 = validate_grid(
                    rows, data_g, row_lines, col_lines_g,
                    cover_thresh=float(cfg["cover_thresh"]),
                    min_cols=int(cfg["min_cols"]),
                    max_cols=int(cfg["max_cols"]),
                )
                # 粘连修复后：仅剩 cover_low / 旧 glue 已清 → 软过
                metrics2 = {**metrics, **metrics2, "glue_audit": glue_report}
                # 插列后落格 cover 可能失真：若粘连已清且非结构错，放宽 cover
                remaining = (glue_report.get("repair") or {}).get("remaining") or {}
                if remaining.get("count", 1) == 0:
                    errs2 = [
                        e for e in errs2
                        if not e.startswith("glue_residual")
                        and not e.startswith("cover_low")
                    ]
                    if not errs2:
                        ok2 = True
                        metrics2["glue_repair_soft_pass"] = True
                ok2, errs2 = _apply_soft_pass(
                    ok2, errs2, metrics2,
                    cover_thresh=float(cfg["cover_thresh"]),
                )
                if ok2:
                    data = data_g
                    col_lines = col_lines_g
                    ok, errs = True, []
                    metrics = metrics2
                    metrics["method_note"] = "nucleus_glue_repaired"
                else:
                    metrics.update(metrics2)
                    errs = errs2

        if ok:
            data, col_lines, ha = _postprocess_ok_grid(data, col_lines)
            if ha.get("chrome_or_footnote_rows_stripped"):
                result.metrics["chrome_or_footnote_rows_stripped"] = ha.pop(
                    "chrome_or_footnote_rows_stripped"
                )
            n_cols = max(0, len(col_lines) - 1) if col_lines else max(
                (len(r) for r in data), default=0
            )
            result.ok = True
            result.method = "nucleus"
            result.data = data
            result.row_lines = row_lines
            result.col_lines = col_lines
            result.n_rows = len(data)
            result.n_cols = n_cols
            result.errors = []
            result.metrics.update(metrics)
            result.metrics["n_cols"] = n_cols
            result.metrics["header_align"] = ha
            lr = ha.get("logical_rows") or {}
            if lr.get("needs_review"):
                result.metrics["logical_rows_needs_review"] = True
            if cross_ratio > float(cfg["cross_ratio_fallback"]):
                result.metrics["cross_high_but_kept"] = True
            return result

        # 验证失败 → 退化；退化失败则保留本次 nucleus 结果供诊断（不写回）
        packed, chain = try_fallbacks(
            nuclei, rows,
            col_gap_factor=float(cfg["col_gap_factor"]),
            max_cols=int(cfg["max_cols"]),
            cover_thresh=float(cfg["cover_thresh"]),
            reasons=["validate_fail:" + ",".join(errs[:3])],
        )
        if packed:
            ok2, bands, row_lines, col_lines, data, method, errs2, metrics2, chain2 = packed
            if ok2 and data:
                # 退化成功路径也跑一次粘连审计（记录；能修则修）
                data_g, glue_report = audit_repair_on_validate_fail(data)
                metrics2 = {**metrics2, "glue_audit": glue_report}
                if glue_report.get("note") == "glue_repaired":
                    data = data_g
                    metrics2["method_note"] = "fallback_glue_repaired"
                data, col_lines, ha = _postprocess_ok_grid(data, col_lines)
                metrics2 = {
                    **metrics2,
                    "header_align": ha,
                    "n_cols": max((len(r) for r in data), default=0),
                }
            result.ok = ok2
            result.method = method
            result.data = data
            result.row_lines = row_lines
            result.col_lines = col_lines
            result.n_rows = len(data)
            result.n_cols = max((len(r) for r in data), default=0) if data else (
                len(col_lines) - 1 if col_lines else 0
            )
            result.errors = errs2 if not ok2 else []
            result.metrics = {**result.metrics, **metrics2, "fallback_chain": chain2}
            result.columns_meta = [b.to_dict() for b in bands]
            return result

        # 最终失败仍带上粘连审计，便于 UI/质检看是不是粘连导致
        if "glue_audit" not in result.metrics:
            _, glue_report = audit_repair_on_validate_fail(data or [])
            result.metrics["glue_audit"] = glue_report
        result.ok = False
        result.method = "fallback_keep"
        result.errors = errs
        result.data = data  # 诊断用，apply 不会写回
        result.row_lines = row_lines
        result.col_lines = col_lines
        result.n_rows = len(data) if data else 0
        result.n_cols = n_cols
        return result
    except Exception as exc:
        result.ok = False
        result.method = "fallback_keep"
        result.errors.append(f"exception:{exc}")
        return result


def apply_grid_to_table(
    table: Dict[str, Any],
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> GridResult:
    """对单表跑凝结核；仅在 ok 且金额守恒时覆盖 data。"""
    cfg = {**GRID_NUCLEUS, **(cfg or {})}
    result = restore_table_grid(table, cfg=cfg)
    table["_grid_nucleus"] = result.to_dict()

    if not cfg.get("enabled", True):
        result.metrics["skipped"] = "disabled"
        return result

    if not result.ok or not result.data:
        return result

    if not cfg.get("allow_overwrite_data", True):
        result.metrics["skipped"] = "overwrite_disabled"
        return result

    before = deepcopy(table.get("data") or [])
    after = result.data
    try:
        from codes.table_repair.validator import amounts_not_in_source

        # 网格真源是 liteparse 字框，不能拿破损旧 data 做「补造」对照
        # （否则几乎张张 conservation_block，表现为「全部保留原表」）
        # 对照预处理后凝结核（含 100+.00→100.00），与落格文本一致
        from codes.reconstruct.grid_nucleus.preprocess import preprocess_words

        raw_words = [
            w for w in (table.get("_source_words") or []) if isinstance(w, dict)
        ]
        source_texts = [n.text for n in preprocess_words(raw_words)]
        if not source_texts:
            source_texts = [str(w.get("text") or "") for w in raw_words]
        invented = amounts_not_in_source(after, source_texts)
        if invented:
            result.ok = False
            result.errors.append("conservation_block:" + ",".join(invented[:6]))
            result.method = "fallback_keep"
            result.metrics["conservation_vs"] = "source_words"
            table["_grid_nucleus"] = result.to_dict()
            return result
        result.metrics["conservation_vs"] = "source_words"
    except Exception as exc:
        result.errors.append(f"conservation_check_error:{exc}")
        # 保守：不写回
        result.ok = False
        result.method = "fallback_keep"
        table["_grid_nucleus"] = result.to_dict()
        return result

    # 行标签守恒（仅结构拆分段）：禁止后段被错字框重建成前表
    if table.get("_format_structure_split"):
        try:
            from codes.reconstruct.grid_nucleus.word_segment import _data_label_tokens

            before_labels = {x for x in _data_label_tokens(before) if len(x) >= 3}
            after_labels = {x for x in _data_label_tokens(after) if len(x) >= 3}
            missing = before_labels - after_labels
            overlap = before_labels & after_labels
            if before_labels and missing and not overlap:
                result.ok = False
                result.errors.append(
                    "label_conservation_block:" + ",".join(sorted(missing)[:6])
                )
                result.method = "fallback_keep"
                result.metrics["label_conservation"] = {
                    "before": sorted(before_labels)[:8],
                    "after": sorted(after_labels)[:8],
                    "missing": sorted(missing)[:6],
                }
                table["_grid_nucleus"] = result.to_dict()
                return result
        except Exception as exc:
            result.metrics["label_conservation_skip"] = str(exc)[:120]

    # 记录相对旧表的差异量（仅诊断，不拦截）
    try:
        from codes.table_repair.validator import amounts_invented

        vs_old = amounts_invented(before, after)
        if vs_old:
            result.metrics["amounts_diff_vs_old_data"] = len(vs_old)
    except Exception:
        pass

    table["data"] = after
    table["rows"] = len(after)
    table["cols"] = max((len(r) for r in after), default=0)
    # 次：行列已由凝结核定稿 → 仅标注跨格，不得再改列结构
    try:
        from codes.reconstruct.grid_nucleus.span_mark import apply_span_marks_to_table

        n_cols_before_span = max((len(r) for r in (table.get("data") or [])), default=0)
        spans = apply_span_marks_to_table(table, col_lines=result.col_lines)
        after = table.get("data") or after
        n_cols_after_span = max((len(r) for r in after), default=0)
        if n_cols_before_span and n_cols_after_span != n_cols_before_span:
            result.metrics["span_mark_cols_guard"] = (
                f"{n_cols_before_span}->{n_cols_after_span}"
            )
        result.data = after
        result.metrics["span_marks"] = len(spans)
        if spans:
            result.metrics["span_cover_cells"] = sum(
                len(s.get("covered") or []) for s in spans
            )
    except Exception as exc:
        result.metrics["span_mark_error"] = str(exc)[:120]
        table.setdefault("_cell_spans", [])
    result.metrics["overwrote_data"] = True
    table["_grid_nucleus"] = result.to_dict()
    return result
