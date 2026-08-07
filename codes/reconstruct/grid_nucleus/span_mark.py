# -*- coding: utf-8 -*-
"""行列分好后：用符号标记证明跨单元格（不真合并）。

次要后处理：服从凝结核已定的列结构，只标注不改列。
- 锚点格：原文后追加「 ⟦↔N⟧」，N=跨列数（含自身）
- 被覆盖邻格：写入「⟦↔⟧」
- 跨列表头文本可放到跨度左首格，但不得增删列或并槽

结构化结果仍写入 table['_cell_spans']。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 锚点：… ⟦↔2⟧
_ANCHOR_MARK_RE = re.compile(r"\s*⟦↔(\d+)⟧\s*$")
# 覆盖格：⟦↔⟧ 或旧式 ⟦跨←r,c⟧
_COVER_MARK_RE = re.compile(r"^⟦↔⟧$|^⟦跨←(\d+),(\d+)⟧$")
COVER_MARK = "⟦↔⟧"


def is_span_cover_mark(text: str) -> bool:
    return bool(_COVER_MARK_RE.match(str(text or "").strip()))


def is_span_anchor_mark(text: str) -> bool:
    return bool(_ANCHOR_MARK_RE.search(str(text or "")))


def strip_span_anchor_mark(text: str) -> str:
    return _ANCHOR_MARK_RE.sub("", str(text or "")).strip()


def with_span_anchor_mark(text: str, colspan: int) -> str:
    base = strip_span_anchor_mark(text)
    n = max(2, int(colspan))
    return f"{base} ⟦↔{n}⟧" if base else f"⟦↔{n}⟧"


def span_cover_mark(anchor_r: int = 0, anchor_c: int = 0) -> str:
    """覆盖格统一符号（保留参数以兼容旧调用）。"""
    return COVER_MARK


def parse_anchor_colspan(text: str) -> Optional[int]:
    m = _ANCHOR_MARK_RE.search(str(text or ""))
    return int(m.group(1)) if m else None


def _cell(data: Sequence[Sequence[Any]], r: int, c: int) -> str:
    if r < 0 or r >= len(data):
        return ""
    row = data[r]
    if c < 0 or c >= len(row):
        return ""
    return str(row[c] or "").strip()


def _cols_overlapped_by_box(
    x0: float,
    x1: float,
    col_lines: Sequence[float],
    *,
    min_ratio: float = 0.18,
    min_overlap_pt: float = 12.0,
) -> List[int]:
    """字框水平投影覆盖到的列。

    宽标签列上短小节标题（如「资本充足率」）重叠比例可能 <20%，
    故同时接受「绝对重叠 ≥ min_overlap_pt」。
    """
    if len(col_lines) < 3 or x1 <= x0:
        return []
    hit: List[int] = []
    for c in range(len(col_lines) - 1):
        lo = float(col_lines[c])
        hi = float(col_lines[c + 1])
        cw = max(hi - lo, 1e-6)
        ov = max(0.0, min(x1, hi) - max(x0, lo))
        if ov >= min_overlap_pt or ov / cw >= min_ratio:
            hit.append(c)
    if not hit:
        return []
    best: List[int] = [hit[0]]
    cur = [hit[0]]
    for c in hit[1:]:
        if c == cur[-1] + 1:
            cur.append(c)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [c]
    if len(cur) > len(best):
        best = cur
    return best


def _find_anchor_cell(
    data: Sequence[Sequence[Any]],
    text: str,
    cand_cols: Sequence[int],
) -> Optional[Tuple[int, int]]:
    """在网格中定位含该文本的锚点格（优先落在 cand_cols）。"""
    t = str(text or "").strip()
    if not t or len(t) < 2:
        return None
    for r, row in enumerate(data):
        for c in cand_cols:
            if c >= len(row):
                continue
            cell = strip_span_anchor_mark(str(row[c] or ""))
            if not cell or is_span_cover_mark(cell):
                continue
            if cell == t or t in cell or cell in t:
                return r, c
    for r, row in enumerate(data):
        for c, cell0 in enumerate(row):
            cell = strip_span_anchor_mark(str(cell0 or ""))
            if not cell or is_span_cover_mark(cell):
                continue
            if cell == t or (len(t) >= 4 and t in cell):
                return r, c
    return None


def _collect_covered(
    data: Sequence[Sequence[Any]],
    ar: int,
    ac: int,
    c0: int,
    c1: int,
    *,
    allow_text_at: Optional[int] = None,
) -> Optional[List[int]]:
    """跨度内除锚点外的空邻格；allow_text_at 上的原文将被挪到左首格，可视为可覆盖。"""
    covered: List[int] = []
    for c in range(c0, c1 + 1):
        if c == ac:
            continue
        v = _cell(data, ar, c)
        if v and not is_span_cover_mark(v):
            if allow_text_at is not None and c == allow_text_at:
                covered.append(c)
                continue
            return None
        covered.append(c)
    return covered if covered else None


def _detect_spans_from_words(
    data: List[List[str]],
    words: Sequence[Dict[str, Any]],
    col_lines: Sequence[float],
) -> List[Dict[str, Any]]:
    if not data or not words or len(col_lines) < 3:
        return []
    n_cols = max(len(r) for r in data)
    spans: List[Dict[str, Any]] = []
    seen: set = set()

    for w in words:
        if not isinstance(w, dict):
            continue
        text = str(w.get("text") or "").strip()
        if len(text) < 2:
            continue
        compact = text.replace(",", "").replace(" ", "")
        if re.fullmatch(r"-?\d+(\.\d+)?%?", compact):
            continue
        try:
            x0 = float(w.get("x0", 0))
            x1 = float(w.get("x1", x0))
        except (TypeError, ValueError):
            continue
        cols = _cols_overlapped_by_box(x0, x1, col_lines)
        # 纯文本科目：不以右缘毛刺单独扩成跨格（连续文本整体凝结核）
        if len(cols) >= 2 and _is_pure_text_label(text):
            if _is_long_body_label_left_edge_only(text):
                cols = _primary_col_by_left_edge(x0, cols, col_lines)
            else:
                cols = _trim_label_right_tip_cols(cols, x0, x1, col_lines)
        if len(cols) < 2:
            continue
        c0, c1 = cols[0], cols[-1]
        colspan = c1 - c0 + 1
        if colspan < 2 or colspan >= n_cols:
            continue
        anchor = _find_anchor_cell(data, text, cols)
        if anchor is None:
            continue
        ar, ac_found = anchor
        # 跨列表头：文本放到左对齐首格，邻格只标覆盖（不并列）
        ac = c0
        cell_text = strip_span_anchor_mark(_cell(data, ar, ac_found))
        if not cell_text:
            continue
        c0_cell = strip_span_anchor_mark(_cell(data, ar, c0))
        if c0_cell and c0_cell != cell_text and cell_text not in c0_cell and c0_cell not in cell_text:
            # 左首格已有其它内容：锚点留在原格（仍须落在跨度内）
            if not (c0 <= ac_found <= c1):
                continue
            ac = ac_found
        key = (ar, c0, 1, colspan)
        if key in seen:
            continue
        covered = _collect_covered(
            data, ar, ac, c0, c1,
            allow_text_at=ac_found if ac_found != ac else None,
        )
        if covered is None:
            continue
        seen.add(key)
        sp: Dict[str, Any] = {
            "r": ar,
            "c": ac,
            "rowspan": 1,
            "colspan": c1 - c0 + 1,
            "c0": c0,
            "c1": c1,
            "text": cell_text[:40],
            "covered": covered,
            "evidence": "word_bbox",
            "confidence": 0.8,
        }
        if ac_found != ac:
            sp["move_from"] = ac_found
        spans.append(sp)
    return spans


def _looks_like_amount_cell(text: str) -> bool:
    t = strip_span_anchor_mark(str(text or "")).replace(",", "").strip()
    if not t or is_span_cover_mark(t):
        return False
    return bool(re.search(r"\d", t)) and bool(
        re.fullmatch(r"[\(\)\-\d.%％\s]+", t)
    )


def _is_section_title_text(text: str) -> bool:
    """表内分组标题（资本充足率 / 可用资本（数额）等）。"""
    t = strip_span_anchor_mark(str(text or "")).strip()
    if not t:
        return False
    # 数值列头：跟金额列同列，绝不当通栏小节标题挪到左首格
    if t in {"数额", "金额", "代码", "指标值", "余额", "面值"}:
        return False
    try:
        from codes.table_repair.wrap_repair import _is_intra_table_section_title

        if _is_intra_table_section_title(t):
            return True
    except Exception:
        pass
    known = {
        "资本充足率",
        "杠杆率",
        "杠杆率相关信息",
        "其他各级资本要求",
        "可用资本（数额）",
        "风险加权资产（数额）",
    }
    if t in known:
        return True
    if (
        re.fullmatch(r"[\u4e00-\u9fff（）()]{2,16}", t)
        and not re.search(r"\d", t)
        and (
            t.endswith(("数额）", "数额)", "相关信息", "资本要求"))
            or "充足率" in t
            or t in {"资产", "负债", "权益"}
        )
    ):
        return True
    return False


def _is_pure_text_label(text: str) -> bool:
    """纯中文科目/说明（非小节通栏标题）：跨格不得只靠右缘毛刺。"""
    t = strip_span_anchor_mark(str(text or "")).strip()
    if len(t) < 4:
        return False
    if _is_section_title_text(t):
        return False
    if re.search(r"\d{3,}", t.replace(",", "")):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", t))


def _is_long_body_label_left_edge_only(text: str) -> bool:
    """长科目连续文本：列归属只认左缘，不以右缘擦边算跨格。

    例外：折算/合计等跨列表头仍允许按字宽跨列。
    """
    t = strip_span_anchor_mark(str(text or "")).strip()
    if len(t) < 10:
        return False
    if re.search(r"折算|合计|小计|总计|平均", t):
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", t))
    return cn >= 8


def _primary_col_by_left_edge(
    x0: float,
    cols: Sequence[int],
    col_lines: Sequence[float],
) -> List[int]:
    primary = None
    for c in cols:
        lo = float(col_lines[c])
        hi = float(col_lines[c + 1])
        if lo <= float(x0) < hi:
            primary = int(c)
            break
    if primary is None:
        primary = int(cols[0])
    return [primary]


def _trim_label_right_tip_cols(
    cols: Sequence[int],
    x0: float,
    x1: float,
    col_lines: Sequence[float],
    *,
    min_extra_width_ratio: float = 0.28,
) -> List[int]:
    """纯文本凝结核：次列须真正吃到足够字宽，右缘擦边不扩跨格。"""
    if len(cols) < 2 or len(col_lines) < 3:
        return list(cols)
    w = max(float(x1) - float(x0), 1.0)
    primary = None
    for c in cols:
        lo = float(col_lines[c])
        hi = float(col_lines[c + 1])
        if lo <= x0 < hi:
            primary = int(c)
            break
    if primary is None:
        primary = int(cols[0])
    kept = [primary]
    for c in cols:
        c = int(c)
        if c == primary:
            continue
        lo = float(col_lines[c])
        hi = float(col_lines[c + 1])
        ov = max(0.0, min(float(x1), hi) - max(float(x0), lo))
        if ov / w >= min_extra_width_ratio:
            kept.append(c)
    if len(kept) < 2:
        return [primary]
    kept = sorted(set(kept))
    out = [primary]
    for c in kept:
        if c == primary:
            continue
        if c == out[-1] + 1:
            out.append(c)
        elif c < out[0] and c == out[0] - 1:
            out.insert(0, c)
        elif c > out[-1] + 1:
            break
    return out if len(out) >= 2 else [primary]


def _detect_section_title_row_spans(
    data: List[List[str]],
) -> List[Dict[str, Any]]:
    """小节标题行：仅左侧有标题、数值列全空 → 按整行空列标跨格。

    PDF 中常为通栏灰底（如「资本充足率」），字框却偏短，单靠 bbox 会漏检。
    """
    if not data:
        return []
    n_cols = max(len(r) for r in data)
    spans: List[Dict[str, Any]] = []
    for r, row in enumerate(data):
        filled: List[Tuple[int, str]] = []
        for c in range(n_cols):
            v = strip_span_anchor_mark(_cell(data, r, c))
            if not v or is_span_cover_mark(v):
                continue
            filled.append((c, v))
        if len(filled) != 1:
            continue
        ac, title = filled[0]
        if not _is_section_title_text(title):
            continue
        # 数值列表头落在右列：保持凝结核落列，禁止挪到左首格通栏
        t0 = strip_span_anchor_mark(title).strip()
        if t0 in {"数额", "金额", "代码", "指标值", "余额", "面值"} or (
            ac >= n_cols - 1 and len(t0) <= 4 and not t0.endswith(("：", ":"))
        ):
            continue
        if any(_looks_like_amount_cell(_cell(data, r, c)) for c in range(n_cols)):
            continue
        # 通栏标题放到左首格；邻格标覆盖
        ac_left = 0
        covered = [c for c in range(n_cols) if c != ac_left]
        if not covered:
            continue
        sp: Dict[str, Any] = {
            "r": r,
            "c": ac_left,
            "rowspan": 1,
            "colspan": n_cols,
            "c0": 0,
            "c1": n_cols - 1,
            "text": title[:40],
            "covered": covered,
            "evidence": "section_title_row",
            "confidence": 0.85,
        }
        if ac != ac_left:
            sp["move_from"] = ac
        spans.append(sp)
    return spans


def _detect_spans_from_empty_neighbors(
    data: List[List[str]],
    *,
    header_rows: int = 4,
) -> List[Dict[str, Any]]:
    """表头区：有字 + 右侧连续空格 → 疑似 colspan。"""
    if not data:
        return []
    n_rows = len(data)
    n_cols = max(len(r) for r in data)
    limit = min(n_rows, max(2, header_rows))
    spans: List[Dict[str, Any]] = []
    seen: set = set()
    for r in range(limit):
        c = 0
        while c < n_cols:
            v = strip_span_anchor_mark(_cell(data, r, c))
            if not v or is_span_cover_mark(v) or len(v) < 2:
                c += 1
                continue
            empty = 0
            for nc in range(c + 1, n_cols):
                nv = _cell(data, r, nc)
                if not nv or is_span_cover_mark(nv):
                    empty += 1
                else:
                    break
            if empty >= 1:
                colspan = empty + 1
                key = (r, c, 1, colspan)
                if key not in seen:
                    is_date = bool(re.search(r"(?:19|20)\d{2}\s*年", v))
                    is_long = len(v) >= 6 and bool(re.search(r"[\u4e00-\u9fff]", v))
                    # 纯文本折行碎片：右侧空金额列不是跨格（不以右缘/空邻格单独依据）
                    wrap_frag = bool(
                        re.search(r"[\u4e00-\u9fff]", v)
                        and v.endswith(("的", "及", "与", "和", "或", "等", "、", "：", ":"))
                    )
                    if wrap_frag:
                        c += 1
                        continue
                    if not re.fullmatch(r"[a-zA-Z]", v) and (is_date or is_long):
                        covered = list(range(c + 1, c + colspan))
                        spans.append({
                            "r": r,
                            "c": c,
                            "rowspan": 1,
                            "colspan": colspan,
                            "c0": c,
                            "c1": c + colspan - 1,
                            "text": v[:40],
                            "covered": covered,
                            "evidence": "empty_neighbors",
                            "confidence": 0.55 if not is_date else 0.65,
                        })
                        seen.add(key)
                c += colspan
            else:
                c += 1
    return spans


def _merge_span_lists(
    primary: List[Dict[str, Any]],
    secondary: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """合并跨格列表；同行列区间重叠时保留已有（避免左移后又被空邻格检测写回原格）。"""
    out = list(primary)
    keys = {(s["r"], s["c"]) for s in out}

    def _row_cols_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        if int(a["r"]) != int(b["r"]):
            return False
        a0 = int(a.get("c0", a["c"]))
        a1 = int(a.get("c1", a["c"] + int(a.get("colspan") or 1) - 1))
        b0 = int(b.get("c0", b["c"]))
        b1 = int(b.get("c1", b["c"] + int(b.get("colspan") or 1) - 1))
        return not (a1 < b0 or b1 < a0)

    for s in secondary:
        if (s["r"], s["c"]) in keys:
            continue
        if any(_row_cols_overlap(s, p) for p in out):
            continue
        out.append(s)
        keys.add((s["r"], s["c"]))
    return out


def mark_spanned_neighbor_cells(
    data: List[List[str]],
    *,
    words: Optional[Sequence[Dict[str, Any]]] = None,
    col_lines: Optional[Sequence[float]] = None,
) -> Tuple[List[List[str]], List[Dict[str, Any]]]:
    """检测跨格并用符号标识。返回 (新 data, spans)。"""
    if not data:
        return data, []
    out = [list(r) for r in data]
    n_cols = max(len(r) for r in out)
    for r in out:
        while len(r) < n_cols:
            r.append("")

    # 先清掉旧标记，避免重复追加
    for r in range(len(out)):
        for c in range(n_cols):
            v = str(out[r][c] or "")
            if is_span_cover_mark(v):
                out[r][c] = ""
            elif is_span_anchor_mark(v):
                out[r][c] = strip_span_anchor_mark(v)

    spans_w: List[Dict[str, Any]] = []
    if words and col_lines and len(col_lines) >= 3:
        spans_w = _detect_spans_from_words(out, words, col_lines)
    spans_sec = _detect_section_title_row_spans(out)
    spans_e = _detect_spans_from_empty_neighbors(out)
    # 小节标题整行优先于短字框局部跨格
    spans = _merge_span_lists(spans_sec, spans_w)
    spans = _merge_span_lists(spans, spans_e)

    for sp in spans:
        ar, ac = int(sp["r"]), int(sp["c"])
        colspan = int(sp.get("colspan") or 2)
        if not (0 <= ar < len(out) and 0 <= ac < n_cols):
            continue
        text = str(sp.get("text") or "").strip() or strip_span_anchor_mark(
            str(out[ar][ac] or "")
        )
        move_from = sp.get("move_from")
        if move_from is not None:
            mf = int(move_from)
            if mf != ac and 0 <= mf < n_cols:
                # 原文挪到左首格后清空原格
                if not text:
                    text = strip_span_anchor_mark(str(out[ar][mf] or ""))
                out[ar][mf] = ""
        out[ar][ac] = with_span_anchor_mark(text, colspan)
        for c in sp.get("covered") or []:
            c = int(c)
            if c == ac or c < 0 or c >= n_cols or ar >= len(out):
                continue
            cur = str(out[ar][c] or "").strip()
            if cur and not is_span_cover_mark(cur):
                continue
            out[ar][c] = COVER_MARK
    return out, spans


def apply_span_marks_to_table(
    table: Dict[str, Any],
    *,
    col_lines: Optional[Sequence[float]] = None,
) -> List[Dict[str, Any]]:
    """就地写回 data / _cell_spans，返回 spans。

    约束：不得改变列数（凝结核分列结果为真值）。
    """
    data = table.get("data") or []
    if not isinstance(data, list) or not data:
        table["_cell_spans"] = []
        return []
    n_cols_before = max((len(r) for r in data if isinstance(r, list)), default=0)
    words = table.get("_source_words") or []
    lines = col_lines
    if not lines:
        gn = table.get("_grid_nucleus") or {}
        lines = gn.get("col_lines") or []
    new_data, spans = mark_spanned_neighbor_cells(
        data, words=words, col_lines=lines,
    )
    n_cols_after = max((len(r) for r in new_data if isinstance(r, list)), default=0)
    if n_cols_before and n_cols_after and n_cols_after != n_cols_before:
        # 次要标注不得破坏主分列：保底回退
        table["_cell_spans"] = []
        return []
    table["data"] = new_data
    table["_cell_spans"] = spans
    return spans
