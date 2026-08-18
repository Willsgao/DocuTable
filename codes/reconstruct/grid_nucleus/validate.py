# -*- coding: utf-8 -*-
"""网格自检。"""

from __future__ import annotations

from statistics import median
from typing import List, Tuple

from codes.reconstruct.grid_nucleus.preprocess import is_amount_nucleus
from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster


def _in_cell(
    n: Nucleus,
    row_lines: List[float],
    col_lines: List[float],
    r: int,
    c: int,
    *,
    pad: float = 2.0,
) -> bool:
    if r < 0 or c < 0 or r >= len(row_lines) - 1 or c >= len(col_lines) - 1:
        return False
    # 与 assign_to_grid 一致：列半开区间；行带少量 pad，避免 median 顶边误杀
    y_ok = (row_lines[r] - pad) <= n.cy <= (row_lines[r + 1] + pad)
    if c == len(col_lines) - 2:
        x_ok = (col_lines[c] - pad) <= n.cx <= (col_lines[c + 1] + pad)
    else:
        x_ok = (col_lines[c] - pad) <= n.cx < (col_lines[c + 1] + pad)
    return y_ok and x_ok


def validate_grid(
    rows: List[RowCluster],
    data: List[List[str]],
    row_lines: List[float],
    col_lines: List[float],
    *,
    cover_thresh: float = 0.95,
    min_cols: int = 2,
    max_cols: int = 20,
) -> Tuple[bool, List[str], dict]:
    errors: List[str] = []
    metrics: dict = {}

    n_rows = len(data)
    n_cols = len(col_lines) - 1 if col_lines else 0
    metrics["n_rows"] = n_rows
    metrics["n_cols"] = n_cols

    if n_cols < min_cols or n_cols > max_cols:
        errors.append(f"cols_out_of_range:{n_cols}")
    if n_rows < 2:
        errors.append(f"rows_too_few:{n_rows}")

    # 单调
    for name, lines in (("row", row_lines), ("col", col_lines)):
        for i in range(1, len(lines)):
            if lines[i] <= lines[i - 1]:
                errors.append(f"{name}_lines_not_monotonic")
                break

    # 覆盖率：优先「已落格」；几何命中作辅证（median 带边过严时不单杀）
    total = 0
    assigned = 0
    geo_hit = 0
    for r in rows:
        for n in r.nuclei:
            total += 1
            if 0 <= n.col_id < n_cols and 0 <= r.row_id < n_rows:
                assigned += 1
            c = n.col_id if n.col_id >= 0 else 0
            if _in_cell(n, row_lines, col_lines, r.row_id, c):
                geo_hit += 1
            elif 0 <= n.col_id < n_cols and _in_cell(
                n, row_lines, col_lines, r.row_id, n.col_id
            ):
                geo_hit += 1
    cover_assign = (assigned / total) if total else 0.0
    cover_geo = (geo_hit / total) if total else 0.0
    # 主覆盖率取落格率；几何过低只记 metrics
    cover = cover_assign
    metrics["cover"] = round(cover, 4)
    metrics["cover_geo"] = round(cover_geo, 4)
    metrics["nuclei_total"] = total
    if total and cover < cover_thresh:
        errors.append(f"cover_low:{cover:.3f}")

    # 阅读序守恒：同行内从左到右的核，最终列号不得回退。
    # 跨列表头仍只落一个主列，因此也应满足单调性。
    order_inversions = 0
    order_examples: List[dict] = []
    for row in rows:
        ordered = sorted(
            (n for n in row.nuclei if 0 <= int(n.col_id) < n_cols),
            key=lambda n: (float(n.x0), float(n.x1)),
        )
        for left, right in zip(ordered, ordered[1:]):
            if int(left.col_id) <= int(right.col_id):
                continue
            order_inversions += 1
            if len(order_examples) < 4:
                order_examples.append({
                    "row": int(row.row_id),
                    "left": str(left.text or "")[:30],
                    "left_col": int(left.col_id),
                    "right": str(right.text or "")[:30],
                    "right_col": int(right.col_id),
                })
    metrics["reading_order_inversions"] = order_inversions
    if order_examples:
        metrics["reading_order_examples"] = order_examples
    if order_inversions:
        errors.append(f"reading_order_inversions:{order_inversions}")

    # 源词身份守恒：合并可共享多个 source_id；只有显式粘连拆分允许
    # 同一个 source_id 出现在两个核中，其余重复消费直接阻断写回。
    source_owners: dict = {}
    legal_split_flags = {
        "glued_split",
        "glued_multi_value",
        "glued_serial_label",
    }
    for row in rows:
        for nucleus in row.nuclei:
            for source_id in nucleus.source_ids:
                source_owners.setdefault(str(source_id), []).append(nucleus)
    illegal_duplicate_sources: List[str] = []
    synthetic_split_sources = 0
    for source_id, owners in source_owners.items():
        if len(owners) <= 1:
            continue
        if all(set(n.flags) & legal_split_flags for n in owners):
            synthetic_split_sources += 1
            continue
        illegal_duplicate_sources.append(source_id)
    metrics["source_fidelity"] = {
        "unique_source_words": len(source_owners),
        "synthetic_split_sources": synthetic_split_sources,
        "illegal_duplicate_sources": illegal_duplicate_sources[:8],
    }
    if illegal_duplicate_sources:
        errors.append(
            f"source_duplicate_consumption:{len(illegal_duplicate_sources)}"
        )

    # 列宽：财务表常见「窄序号 + 宽科目 + 金额」，放宽到相对中位 >5 且异常列≥3 才报
    if n_cols >= 2:
        widths = [col_lines[i + 1] - col_lines[i] for i in range(n_cols)]
        w_med = median(widths) if widths else 1.0
        bad = sum(1 for w in widths if w_med > 0 and abs(w - w_med) / w_med > 5.0)
        metrics["col_width_outliers"] = bad
        if bad >= 3:
            errors.append(f"col_width_outliers:{bad}")

    # 粘连残留（金额+文本）；双表头粘连记入 metrics，由校验失败专项/header_align 处理
    glue_hits = 0
    glue_kinds: dict = {}
    try:
        from codes.v2_steps.table_anomaly_rules import _looks_like_numeric_text_glue
        from codes.reconstruct.grid_nucleus.glue_audit import classify_glue_cell

        for row in data:
            for cell in row:
                t = str(cell or "")
                kind = classify_glue_cell(t)
                if kind:
                    glue_kinds[kind] = glue_kinds.get(kind, 0) + 1
                if _looks_like_numeric_text_glue(t):
                    glue_hits += 1
    except Exception:
        pass
    metrics["glue_residual"] = glue_hits
    if glue_kinds:
        metrics["glue_kinds"] = glue_kinds
    if glue_hits > 0:
        errors.append(f"glue_residual:{glue_hits}")
    # 可拆双表头粘连：不单独否决写回，但标需专项关注
    dual = int(glue_kinds.get("dual_metric_header", 0)) + int(
        glue_kinds.get("dual_header", 0)
    )
    if dual > 0:
        metrics["dual_header_glue"] = dual

    # 同列同型：找金额占比最高的列（跳过多行中文表头，勿把「余额比例」当类型破坏）
    if data and n_cols > 0:
        body: List[List[str]] = []
        if rows and len(rows) == len(data):
            body = [
                data[i]
                for i, r in enumerate(rows)
                if getattr(r, "role", None) not in ("header", "title")
            ]
        if len(body) < 2:
            start = 0
            if rows and rows[0].role in ("header", "title"):
                start = 1
            elif len(data) >= 2:
                # 首行几乎无金额则当表头
                first = data[0]
                if not any(
                    is_amount_nucleus(Nucleus(text=str(c), x0=0, y0=0, x1=1, y1=1))
                    for c in first
                ):
                    start = 1
            body = data[start:]
        best_c, best_ratio = 0, -1.0
        for c in range(n_cols):
            vals = [str(row[c] if c < len(row) else "") for row in body]
            nonempty = [v for v in vals if v.strip()]
            if len(nonempty) < 2:
                continue
            amt = sum(1 for v in nonempty if is_amount_nucleus(
                Nucleus(text=v, x0=0, y0=0, x1=1, y1=1)
            ))
            ratio = amt / len(nonempty)
            if ratio > best_ratio:
                best_ratio, best_c = ratio, c
        if best_ratio >= 0.5:
            vals = [str(row[best_c] if best_c < len(row) else "") for row in body]
            textish = 0
            checked = 0
            for v in vals:
                if not v.strip():
                    continue
                checked += 1
                if not is_amount_nucleus(Nucleus(text=v, x0=0, y0=0, x1=1, y1=1)):
                    plain = str(v).split("\u27e6")[0].strip().replace(" ", "")
                    # 多层表头短词（传统型/合成型/小计）不算科目污染
                    if plain in {
                        "传统型",
                        "合成型",
                        "小计",
                        "合计",
                        "数额",
                        "金额",
                        "代码",
                    }:
                        continue
                    if len(plain) <= 1 and plain.isalpha():
                        continue
                    if sum(1 for ch in v if "\u4e00" <= ch <= "\u9fff") >= 3:
                        textish += 1
            if checked and textish / checked > 0.15:
                errors.append(f"value_col_type_break:c{best_c}")
            metrics["value_col"] = best_c
            metrics["value_col_amt_ratio"] = round(best_ratio, 3)

    ok = len(errors) == 0
    return ok, errors, metrics
