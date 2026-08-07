# -*- coding: utf-8 -*-
"""在格式纠错扫描前，用 Table Engine 同一套结构拆分逻辑切开粘连表。

格式纠错本身不重建 PDF，但应对当前 data[][] 复用
`find_structure_break_row`，避免「前表」里仍夹着（五）/重复表头。
另：字框里多段年报表头但 data 未覆盖时，提升为独立表（如信用成本小表）。
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import List, Optional, Sequence, Tuple

from codes.table_engine.split.structure_split import find_structure_break_row

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _partition_source_words(
    table: dict,
    parts: List[List[List]],
) -> List[List[dict]]:
    """结构拆分后为各段分配字框，避免后段仍带着前表字框被凝结核重建合并。"""
    words = table.get("_source_words") or []
    if not isinstance(words, list) or not words:
        return [[] for _ in parts]
    try:
        from codes.reconstruct.grid_nucleus.word_segment import (
            assign_source_words_to_parts,
        )

        return assign_source_words_to_parts(words, parts)
    except Exception:
        return [list(words) for _ in parts]


def _slice_data(data: List[List], start: int, end: int = None) -> List[List]:
    chunk = data[start:end] if end is not None else data[start:]
    return [list(row) for row in chunk]


def split_table_data_by_structure(data: List[List]) -> List[List[List]]:
    """递归按结构切点拆分一张表的 data，返回 1..N 段。"""
    if not data or len(data) < 4:
        return [data]

    parts: List[List[List]] = []
    remaining = [list(r) for r in data]
    guard = 0
    while remaining and guard < 32:
        guard += 1
        br = find_structure_break_row(remaining)
        if br < 2 or br >= len(remaining) - 1:
            parts.append(remaining)
            break
        left = _slice_data(remaining, 0, br)
        right = _slice_data(remaining, br)
        if not left or not right:
            parts.append(remaining)
            break
        parts.append(left)
        remaining = right
    else:
        if remaining:
            parts.append(remaining)
    return parts if parts else [data]


def _peel_table_footnotes(table: dict) -> Tuple[dict, List[str]]:
    """去掉表顶页眉/叙述、表体后脚注/下一表表头，并裁掉对应字框。"""
    from codes.reconstruct.grid_nucleus.word_segment import (
        strip_footnote_rows_from_data,
        strip_leading_non_table_rows_from_data,
        strip_trailing_next_table_header_rows_from_data,
        trim_leading_narrative_words,
        trim_leading_page_chrome_words,
        trim_trailing_footnote_words,
        trim_trailing_next_header_words,
    )

    def _row_text(row: Sequence) -> str:
        return " ".join(str(c).strip() for c in row if str(c).strip())

    t = table
    data = t.get("data")
    peeled: List[str] = []
    if isinstance(data, list) and data:
        cleaned = strip_leading_non_table_rows_from_data(data)
        cleaned = strip_footnote_rows_from_data(cleaned)
        cleaned = strip_trailing_next_table_header_rows_from_data(cleaned)
        if len(cleaned) < len(data):
            kept = {_row_text(r) for r in cleaned if _row_text(r)}
            peeled = [
                jt for row in data
                for jt in [_row_text(row)]
                if jt and jt not in kept
            ]
            t["data"] = cleaned
            t["rows"] = len(cleaned)
            t["cols"] = max((len(r) for r in cleaned), default=0)
            if peeled:
                t["_peeled_footnotes"] = peeled
    words = t.get("_source_words")
    if isinstance(words, list) and words:
        words = trim_trailing_footnote_words(words)
        words = trim_trailing_next_header_words(words)
        words = trim_leading_page_chrome_words(words)
        words = trim_leading_narrative_words(words)
        t["_source_words"] = words
        # 页眉/叙述/尾表头剥掉后收紧几何，避免后续 attach 再扩回
        if words and (t.get("y0") is not None or t.get("bbox")):
            _apply_words_geometry(t, words)
    return t, peeled


def _apply_words_geometry(table: dict, words: List[dict]) -> None:
    """按本段字框收紧 y0/y1/bbox，避免后续 attach 用大区域把前表字框挂回来。"""
    if not words:
        return
    try:
        x0 = min(float(w.get("x0") or 0) for w in words)
        x1 = max(float(w.get("x1") or 0) for w in words)
        y0 = min(float(w.get("y0") or 0) for w in words)
        y1 = max(float(w.get("y1") or 0) for w in words)
    except (TypeError, ValueError):
        return
    pad = 2.0
    table["x0"] = x0 - pad
    table["y0"] = y0 - pad
    table["x1"] = x1 + pad
    table["y1"] = y1 + pad
    table["bbox"] = [table["x0"], table["y0"], table["x1"], table["y1"]]
    table["_source_word_y_span"] = [y0, y1]


def _part_is_footnote_only(part: List[List]) -> bool:
    """整段都是表尾注释，不应再当成一张表。"""
    from codes.reconstruct.grid_nucleus.word_segment import strip_footnote_rows_from_data

    if not part:
        return True
    return len(strip_footnote_rows_from_data(part)) == 0


def _title_from_data(data: Sequence[Sequence]) -> str:
    """用首个中文行标签作列表标题（避免两段都显示成「2025年」）。"""
    for row in data or []:
        if not row:
            continue
        for cell in row[:2]:
            t = str(cell or "").strip()
            if not t or len(t) < 2:
                continue
            if not _CJK_RE.search(t):
                continue
            if re.match(r"^(?:19|20)\d{2}", t):
                continue
            if "增减" in t and len(t) <= 16:
                continue
            if t in ("注：", "注:", "注"):
                continue
            return t[:24]
    return ""


def _set_part_title(table: dict, data: Sequence[Sequence]) -> None:
    title = _title_from_data(data)
    if title:
        table["title"] = title
        table["context_text"] = title


def _labels_in_data(data: Sequence[Sequence]) -> set:
    from codes.reconstruct.grid_nucleus.word_segment import _data_label_tokens

    return {x for x in _data_label_tokens(data) if len(x) >= 3}


def _normalize_cell(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"\s+", "", t)
    return t


def _data_content_fingerprint(data: Sequence[Sequence]) -> frozenset:
    """非空单元格归一化集合，用于精确/近精确去重。"""
    cells: set = set()
    for row in data or []:
        for c in row:
            t = _normalize_cell(str(c))
            if t:
                cells.add(t)
    return frozenset(cells)


def _is_near_duplicate_data(a: Sequence[Sequence], b: Sequence[Sequence]) -> bool:
    """两段 data 内容高度重合 → 同一张表，只能留一份。"""
    fa, fb = _data_content_fingerprint(a), _data_content_fingerprint(b)
    if len(fa) >= 4 and len(fb) >= 4:
        inter = len(fa & fb)
        union = len(fa | fb) or 1
        if inter >= 4 and inter / union >= 0.85:
            return True
        # 一方是另一方的超集（拆分残片 vs 完整表）
        if inter >= 4 and (fa <= fb or fb <= fa):
            return True
    la, lb = _labels_in_data(a), _labels_in_data(b)
    if len(la) < 2 or len(lb) < 2:
        return False
    inter_l = len(la & lb)
    return inter_l >= 2 and inter_l >= min(len(la), len(lb)) * 0.75


def _find_duplicate_in_out(part: Sequence[Sequence], out: List[dict]) -> int:
    """若 out 中已有近重复真表，返回其下标；否则 -1。"""
    for i, t in enumerate(out):
        if not isinstance(t, dict) or t.get("type") in ("text", "paragraph"):
            continue
        d = t.get("data")
        if isinstance(d, list) and _is_near_duplicate_data(part, d):
            return i
    return -1


def _table_keep_score(table: dict) -> tuple:
    """去重时保留优先级：原始表 > 字框多 > 行多 > 非提升段。"""
    data = table.get("data") or []
    n_words = len(table.get("_source_words") or [])
    n_rows = len(data) if isinstance(data, list) else 0
    is_split = 1 if table.get("_format_structure_split") else 0
    is_promoted = 1 if table.get("_format_word_band_promoted") else 0
    # 分数越高越优先保留
    return (0 if is_split else 1, 0 if is_promoted else 1, n_words, n_rows)


def dedupe_tables_keep_unique(tables: List[dict]) -> Tuple[List[dict], List[str]]:
    """终扫：同一内容只留一份（优先保留先抽出的完整原表）。"""
    notes: List[str] = []
    kept: List[dict] = []
    for t in tables or []:
        if not isinstance(t, dict) or t.get("type") in ("text", "paragraph"):
            kept.append(t)
            continue
        data = t.get("data")
        if not isinstance(data, list) or not data:
            kept.append(t)
            continue
        dup_at = _find_duplicate_in_out(data, kept)
        if dup_at < 0:
            kept.append(t)
            continue
        # 与已保留表重复：留下分更高的那份
        old = kept[dup_at]
        if _table_keep_score(t) > _table_keep_score(old):
            notes.append(
                f"去重: 用「{t.get('title') or '?'}」替换先保留的重复表"
            )
            kept[dup_at] = t
        else:
            notes.append(
                f"去重: 丢弃重复表「{t.get('title') or old.get('title') or '?'}」"
            )
    return kept, notes


def strip_cross_table_leading_duplicates(
    tables: List[dict],
) -> Tuple[List[dict], List[str]]:
    """下表首行若与上表尾行同内容（如资产合计串到负债表顶）→ 从下表删除。

    同一数据只保留在一张表里。
    """
    from codes.table_engine.split.boundary_overlap import count_lower_leading_duplicate_rows

    notes: List[str] = []
    out: List[dict] = []
    prev_table: Optional[dict] = None
    for t in tables or []:
        if not isinstance(t, dict) or t.get("type") in ("text", "paragraph"):
            out.append(t)
            continue
        data = t.get("data")
        if (
            prev_table is not None
            and isinstance(data, list)
            and data
            and isinstance(prev_table.get("data"), list)
            and prev_table.get("data")
        ):
            # 仅同页相邻表
            same_page = True
            try:
                if prev_table.get("page") is not None and t.get("page") is not None:
                    same_page = int(prev_table["page"]) == int(t["page"])
            except (TypeError, ValueError):
                same_page = True
            if same_page:
                trim = count_lower_leading_duplicate_rows(
                    prev_table["data"], data
                )
                if trim > 0 and trim < len(data):
                    peeled = data[:trim]
                    data = data[trim:]
                    t = dict(t)
                    t["data"] = data
                    t["rows"] = len(data)
                    t["cols"] = max((len(r) for r in data), default=0)
                    label = " ".join(
                        str(c).strip()
                        for c in (peeled[0] if peeled else [])
                        if str(c).strip()
                    )[:24]
                    notes.append(f"跨表去重: 下表去掉与上表重复的首部「{label or '?'}」")
                    # 同步去掉误挂字框（探视吸上的上一表合计）
                    words = t.get("_source_words")
                    if isinstance(words, list) and words and peeled:
                        drop_texts = {
                            str(c).strip()
                            for row in peeled
                            for c in row
                            if str(c).strip()
                        }
                        # 仅当字框在剩余表头之上
                        try:
                            keep_y0 = min(
                                float(w.get("y0") or 0)
                                for w in words
                                if str(w.get("text") or "").strip()
                                and str(w.get("text") or "").strip() not in drop_texts
                            )
                        except ValueError:
                            keep_y0 = None
                        if keep_y0 is not None:
                            words = [
                                w
                                for w in words
                                if not (
                                    str(w.get("text") or "").strip() in drop_texts
                                    and float(w.get("y0") or 0) < keep_y0 - 0.5
                                )
                            ]
                            t["_source_words"] = words
                            if words:
                                _apply_words_geometry(t, words)
        out.append(t)
        prev_table = t
    return out, notes


def _materialize_table_from_words(
    template: dict,
    words: List[dict],
    *,
    from_idx: int,
    part_idx: int,
) -> Optional[dict]:
    """从一段字框重建小表；失败返回 None。"""
    if len(words) < 4:
        return None
    from codes.reconstruct.grid_nucleus import restore_table_grid
    from codes.reconstruct.grid_nucleus.word_segment import (
        strip_footnote_rows_from_data,
        strip_leading_page_chrome_rows_from_data,
        strip_trailing_next_table_header_rows_from_data,
    )

    res = restore_table_grid({"_source_words": words, "data": []})
    data = strip_footnote_rows_from_data(res.data or [])
    data = strip_leading_page_chrome_rows_from_data(data)
    data = strip_trailing_next_table_header_rows_from_data(data)
    if len(data) < 2:
        return None
    # 至少要有一个非年头的中文标签行
    if not _title_from_data(data):
        return None
    t = deepcopy(template)
    t["data"] = data
    t["rows"] = len(data)
    t["cols"] = max((len(r) for r in data), default=0)
    t["_source_words"] = list(words)
    t["type"] = "table"
    t["_format_structure_split"] = True
    t["_format_structure_split_from"] = from_idx
    t["_format_structure_split_part"] = part_idx
    t["_format_word_band_promoted"] = True
    _apply_words_geometry(t, words)
    _set_part_title(t, data)
    anomaly = dict(t.get("_anomaly") or {})
    anomaly["header_missing"] = False
    t["_anomaly"] = anomaly
    if t.get("table_category") == "数据表(缺表头)":
        t["table_category"] = "财务数据表"
    return t


def _promote_unused_word_bands(
    template: dict,
    words: List[dict],
    used_segs: Sequence[Sequence[dict]],
    *,
    from_idx: int,
    start_part: int,
    covered_labels: set,
) -> Tuple[List[dict], List[str]]:
    """字框年头带多于已拆 data 段时，把未用带提升为独立表。"""
    from codes.reconstruct.grid_nucleus.word_segment import (
        score_words_against_data,
        split_source_words_by_year_bands,
    )

    notes: List[str] = []
    extras: List[dict] = []
    segs = split_source_words_by_year_bands(words)
    if len(segs) <= 1:
        return extras, notes

    used_ids = set()
    for used in used_segs:
        if not used:
            continue
        # 用 y 跨度匹配已用段
        try:
            uy0 = min(float(w.get("y0") or 0) for w in used)
            uy1 = max(float(w.get("y1") or 0) for w in used)
        except (TypeError, ValueError):
            continue
        for si, seg in enumerate(segs):
            if si in used_ids or not seg:
                continue
            try:
                sy0 = min(float(w.get("y0") or 0) for w in seg)
                sy1 = max(float(w.get("y1") or 0) for w in seg)
            except (TypeError, ValueError):
                continue
            # 重叠则视为已用
            if not (sy1 < uy0 - 1 or sy0 > uy1 + 1):
                used_ids.add(si)

    part_i = start_part
    for si, seg in enumerate(segs):
        if si in used_ids:
            continue
        t = _materialize_table_from_words(
            template, list(seg), from_idx=from_idx, part_idx=part_i
        )
        if t is None:
            continue
        labs = _labels_in_data(t.get("data") or [])
        # 已有表覆盖相同标签则跳过
        if labs and labs <= covered_labels:
            continue
        extras.append(t)
        covered_labels |= labs
        notes.append(
            f"表#{from_idx} P{template.get('page', 0)}: "
            f"字框年头带提升为独立表「{t.get('title') or '?'}」"
        )
        part_i += 1
    return extras, notes


def expand_tables_with_structure_split(
    tables: List[dict],
) -> Tuple[List[dict], List[str]]:
    """对每张真表做结构拆分，插入为多张表（保持文档顺序）。

    Returns:
        (new_tables, notes)
    """
    out: List[dict] = []
    notes: List[str] = []
    covered_labels: set = set()

    for idx, table in enumerate(tables or []):
        data = table.get("data")
        if not isinstance(data, list) or not data:
            out.append(deepcopy(table))
            continue
        if table.get("type") in ("text", "paragraph"):
            out.append(deepcopy(table))
            continue

        # 先剥表尾「注：」/脚注，避免脚注被当成第二张表拆出去
        base, peeled = _peel_table_footnotes(deepcopy(table))
        if peeled:
            notes.append(
                f"表#{idx} P{base.get('page', 0)}: 剥离表尾注释 {len(peeled)} 行"
            )
        data = base.get("data") or []
        if not data:
            continue

        parts = split_table_data_by_structure(data)
        parts = [p for p in parts if p and not _part_is_footnote_only(p)]
        words = list(base.get("_source_words") or [])

        if len(parts) <= 1:
            if parts:
                base["data"] = parts[0]
                base["rows"] = len(parts[0])
                base["cols"] = max((len(r) for r in parts[0]), default=0)
                _set_part_title(base, parts[0])
                # 与已有表重复：不重复插入，但仍可从字框提升未覆盖段
                dup_at = _find_duplicate_in_out(parts[0], out)
                if words:
                    from codes.reconstruct.grid_nucleus.word_segment import (
                        split_source_words_by_year_bands,
                    )

                    segs = split_source_words_by_year_bands(words)
                    if len(segs) >= 2:
                        used0 = list(segs[0])
                        if dup_at < 0:
                            base["_source_words"] = used0
                            _apply_words_geometry(base, used0)
                            covered_labels |= _labels_in_data(base.get("data") or [])
                            out.append(base)
                        else:
                            notes.append(
                                f"表#{idx} P{base.get('page', 0)}: "
                                f"与已有表#{dup_at}重复，跳过；尝试提升其余年头带"
                            )
                            covered_labels |= _labels_in_data(
                                out[dup_at].get("data") or []
                            )
                        extras, extra_notes = _promote_unused_word_bands(
                            base,
                            words,
                            used_segs=[used0],
                            from_idx=idx,
                            start_part=1,
                            covered_labels=covered_labels,
                        )
                        notes.extend(extra_notes)
                        for e in extras:
                            if _find_duplicate_in_out(e.get("data") or [], out) >= 0:
                                continue
                            covered_labels |= _labels_in_data(e.get("data") or [])
                            out.append(e)
                        continue
                    if dup_at >= 0:
                        notes.append(
                            f"表#{idx} P{base.get('page', 0)}: "
                            f"与已有表#{dup_at}重复，跳过"
                        )
                        continue
                    _apply_words_geometry(base, words)
                elif dup_at >= 0:
                    notes.append(
                        f"表#{idx} P{base.get('page', 0)}: "
                        f"与已有表#{dup_at}重复，跳过"
                    )
                    continue
                covered_labels |= _labels_in_data(base.get("data") or [])
            out.append(base)
            continue

        page = base.get("page", 0)
        notes.append(
            f"表#{idx} P{page}: 结构拆分为 {len(parts)} 段 "
            f"（小节/重复表头等，复用 TE find_structure_break_row）"
        )
        word_parts = _partition_source_words(base, parts)
        emitted_words: List[List[dict]] = []
        emitted_any = False
        for pi, part in enumerate(parts):
            # 页上已有同内容表（常见：先抽出干净资产质量，混表再拆出前段）→ 跳过
            dup_at = _find_duplicate_in_out(part, out)
            if dup_at >= 0:
                notes.append(
                    f"表#{idx}.{pi} P{page}: 与已有表#{dup_at}重复，跳过该段"
                )
                wp_skip = word_parts[pi] if pi < len(word_parts) else []
                if wp_skip:
                    emitted_words.append(wp_skip)
                covered_labels |= _labels_in_data(part)
                continue

            t = deepcopy(base)
            t["data"] = part
            t["rows"] = len(part)
            t["cols"] = max((len(r) for r in part), default=0)
            wp = word_parts[pi] if pi < len(word_parts) else []
            if wp:
                t["_source_words"] = wp
                _apply_words_geometry(t, wp)
                emitted_words.append(wp)
            elif words:
                # 后段字框为空时，尝试按标签从整表字框再选，避免信用成本等小表丢字框
                from codes.reconstruct.grid_nucleus.word_segment import (
                    select_source_words_for_data,
                )

                picked = select_source_words_for_data(words, part)
                if picked and len(picked) < len(words):
                    t["_source_words"] = picked
                    _apply_words_geometry(t, picked)
                    emitted_words.append(picked)
            t["_format_structure_split"] = True
            t["_format_structure_split_from"] = idx
            t["_format_structure_split_part"] = pi
            _set_part_title(t, part)
            if pi > 0:
                anomaly = dict(t.get("_anomaly") or {})
                if anomaly.get("header_missing"):
                    from codes.table_engine.scope.header_scope import (
                        is_annual_report_column_header_row,
                    )
                    from codes.format_corrector.conservation import looks_like_header_row

                    head = part[0] if part else []
                    cells = [str(c).strip() for c in head if str(c).strip()]
                    if (
                        looks_like_header_row(head)
                        or is_annual_report_column_header_row(cells)
                        or (cells and str(cells[0]).startswith(("（", "(")))
                        or bool(t.get("title"))
                    ):
                        anomaly["header_missing"] = False
                        t["_anomaly"] = anomaly
                        if t.get("table_category") == "数据表(缺表头)":
                            t["table_category"] = "财务数据表"
            covered_labels |= _labels_in_data(part)
            out.append(t)
            emitted_any = True

        if not emitted_any and not any(
            "信用成本" in " ".join(str(c) for r in p for c in r) for p in parts
        ):
            # 全被跳过且没有新内容时不强制保留
            pass

        # data 已拆完，若字框仍有未覆盖年头带（偶发），再提升
        if words:
            extras, extra_notes = _promote_unused_word_bands(
                base,
                words,
                used_segs=emitted_words,
                from_idx=idx,
                start_part=len(parts),
                covered_labels=covered_labels,
            )
            notes.extend(extra_notes)
            for e in extras:
                if _find_duplicate_in_out(e.get("data") or [], out) >= 0:
                    continue
                covered_labels |= _labels_in_data(e.get("data") or [])
                out.append(e)

    out, dedupe_notes = dedupe_tables_keep_unique(out)
    notes.extend(dedupe_notes)
    out, cross_notes = strip_cross_table_leading_duplicates(out)
    notes.extend(cross_notes)
    return out, notes
