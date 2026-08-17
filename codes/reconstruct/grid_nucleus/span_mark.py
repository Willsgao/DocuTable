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


def _columns_with_body_data(
    data: Sequence[Sequence[Any]],
    *,
    exclude_rows: Optional[Sequence[int]] = None,
) -> List[int]:
    """上下表体中「有实质内容」的列（序号/科目/金额/占位等）。"""
    if not data:
        return []
    skip = set(int(r) for r in (exclude_rows or []))
    n_cols = max((len(r) for r in data if isinstance(r, (list, tuple))), default=0)
    hits: List[int] = []
    for c in range(n_cols):
        for r, row in enumerate(data):
            if r in skip or not isinstance(row, (list, tuple)) or c >= len(row):
                continue
            v = strip_span_anchor_mark(str(row[c] or "")).strip()
            if not v or is_span_cover_mark(v):
                continue
            hits.append(c)
            break
    return hits


def _cols_spanned_by_nucleus_width(
    x0: float,
    x1: float,
    col_lines: Sequence[float],
    body_cols: Sequence[int],
    *,
    min_extra_width_ratio: float = 0.28,
    min_col_overlap_ratio: float = 0.35,
) -> List[int]:
    """凝结核 [x0,x1] 覆盖到的、且上下有表体数据的列。

    跨格只认几何：核宽真正吃进多列有数据 → 跨列；右缘毛刺不算；不以字数判断。
    主列取与核重叠最大的有数据列（不用左缘）：避免宽左列带把右列头核的左缘吞进去，
    误把并列同文头（两个「的资产负债表」）标成跨格。
    """
    if not body_cols or len(col_lines) < 3 or x1 <= x0:
        return []
    body_set = set(int(c) for c in body_cols)
    best_c: Optional[int] = None
    best_ov = -1.0
    for c in range(len(col_lines) - 1):
        if c not in body_set:
            continue
        lo = float(col_lines[c])
        hi = float(col_lines[c + 1])
        ov = max(0.0, min(float(x1), hi) - max(float(x0), lo))
        if ov > best_ov + 1e-6:
            best_ov = ov
            best_c = c
        elif abs(ov - best_ov) <= 1e-6 and best_c is not None and c < best_c:
            # 重叠并列时取更左列（稳定）
            best_c = c
    primary = best_c
    if primary is None or best_ov <= 0:
        return []

    w = max(float(x1) - float(x0), 1.0)
    hit: List[int] = [primary]
    for c in sorted(body_set):
        if c == primary or c >= len(col_lines) - 1:
            continue
        lo = float(col_lines[c])
        hi = float(col_lines[c + 1])
        cw = max(hi - lo, 1e-6)
        ov = max(0.0, min(float(x1), hi) - max(float(x0), lo))
        if ov <= 0:
            continue
        # 次列须真正吃到足够字宽，或占该列带足够比例（排除右缘毛刺）
        if ov / w >= min_extra_width_ratio or ov / cw >= min_col_overlap_ratio:
            hit.append(c)
    if len(hit) < 2:
        return [primary]
    hit = sorted(set(hit))
    # 只保留含 primary 的连续列段
    out = [primary]
    for c in hit:
        if c == primary:
            continue
        if c == out[-1] + 1:
            out.append(c)
        elif c < out[0] and c == out[0] - 1:
            out.insert(0, c)
        elif c > out[-1] + 1:
            break
    return out if len(out) >= 2 else [primary]


def _find_word_box_for_text(
    words: Sequence[Dict[str, Any]],
    text: str,
) -> Optional[Tuple[float, float]]:
    """在源字框中定位与单元格文本匹配的凝结核 [x0,x1]。"""
    t = strip_span_anchor_mark(str(text or "")).strip()
    if not t or len(t) < 2:
        return None
    best: Optional[Tuple[float, float, int]] = None
    for w in words or []:
        if not isinstance(w, dict):
            continue
        wt = str(w.get("text") or "").strip()
        if not wt:
            continue
        if not (wt == t or t in wt or wt in t):
            continue
        try:
            x0 = float(w.get("x0", 0))
            x1 = float(w.get("x1", x0))
        except (TypeError, ValueError):
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        score = 2 if wt == t else (1 if t in wt else 0)
        if best is None or score > best[2] or (
            score == best[2] and (x1 - x0) > (best[1] - best[0])
        ):
            best = (x0, x1, score)
    if best is None:
        return None
    return best[0], best[1]


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


def _is_short_year_header(text: str) -> bool:
    """短报告期列组头：2025年 / 2024年（非 2025年12月31日）。"""
    t = strip_span_anchor_mark(str(text or "")).strip()
    return bool(re.fullmatch(r"(?:19|20)\d{2}\s*年", t))


def _columns_with_amount_data(
    data: Sequence[Sequence[Any]],
    *,
    exclude_rows: Optional[Sequence[int]] = None,
) -> List[int]:
    """表体中含金额/占比数字的列（短年头只跨这些列，不跨科目列）。"""
    if not data:
        return []
    skip = set(int(r) for r in (exclude_rows or []))
    n_cols = max((len(r) for r in data if isinstance(r, (list, tuple))), default=0)
    hits: List[int] = []
    for c in range(n_cols):
        for r, row in enumerate(data):
            if r in skip or not isinstance(row, (list, tuple)) or c >= len(row):
                continue
            v = strip_span_anchor_mark(str(row[c] or "")).strip()
            if not v or is_span_cover_mark(v):
                continue
            if _looks_like_amount_cell(v) or re.fullmatch(r"-?\d+(?:\.\d+)?%?", v.replace(",", "")):
                hits.append(c)
                break
            # 占比小数
            if re.fullmatch(r"\d+\.\d+", v):
                hits.append(c)
                break
        else:
            continue
    return hits


def _body_cols_owned_by_year_header(
    x0: float,
    x1: float,
    words: Sequence[Dict[str, Any]],
    col_lines: Sequence[float],
    body_cols: Sequence[int],
) -> List[int]:
    """短年头管辖的金额列：列中心距哪一年头最近就归谁（发生额+比例同属 2025年）。"""
    if not body_cols or len(col_lines) < 3:
        return []
    year_cxs: List[float] = []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        if not _is_short_year_header(str(w.get("text") or "")):
            continue
        try:
            wx0 = float(w.get("x0", 0))
            wx1 = float(w.get("x1", wx0))
        except (TypeError, ValueError):
            continue
        year_cxs.append((wx0 + wx1) / 2.0)
    if not year_cxs:
        return []
    year_cxs.sort()
    my_cx = (float(x0) + float(x1)) / 2.0
    my_yx = min(year_cxs, key=lambda y: abs(y - my_cx))
    owned: List[int] = []
    for c in sorted(int(x) for x in body_cols):
        if c < 0 or c >= len(col_lines) - 1:
            continue
        mid = (float(col_lines[c]) + float(col_lines[c + 1])) / 2.0
        nearest = min(year_cxs, key=lambda y: abs(y - mid))
        if abs(nearest - my_yx) <= 1e-6:
            owned.append(c)
    if len(owned) < 2:
        return []
    primary = min(owned, key=lambda c: abs(
        (float(col_lines[c]) + float(col_lines[c + 1])) / 2.0 - my_yx
    ))
    out = [primary]
    for c in owned:
        if c == primary:
            continue
        if c == out[-1] + 1:
            out.append(c)
        elif c < out[0] and c == out[0] - 1:
            out.insert(0, c)
        elif c > out[-1] + 1:
            break
    return out if len(out) >= 2 else []


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
    body_cols_all = _columns_with_body_data(data)

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
        if x1 < x0:
            x0, x1 = x1, x0
        # 先找锚点行，再用「核宽 ∩ 上下有数据的列」判定跨格（不以字数）
        rough_cols = _cols_overlapped_by_box(x0, x1, col_lines)
        if not rough_cols:
            continue
        anchor = _find_anchor_cell(data, text, rough_cols)
        if anchor is None:
            anchor = _find_anchor_cell(data, text, range(n_cols))
        if anchor is None:
            continue
        ar, ac_found = anchor
        body_cols = _columns_with_body_data(data, exclude_rows=[ar]) or body_cols_all
        cols = _cols_spanned_by_nucleus_width(x0, x1, col_lines, body_cols)
        # 短年头字框偏窄：按其下最近的金额列归属扩成跨列（2025年→发生额+比例）
        if len(cols) < 2 and _is_short_year_header(text):
            amt_cols = _columns_with_amount_data(data, exclude_rows=[ar]) or [
                c for c in body_cols if c > 0
            ]
            owned = _body_cols_owned_by_year_header(
                x0, x1, words, col_lines, amt_cols,
            )
            if len(owned) >= 2:
                cols = owned
        # 与旁列独立表头（合计/最大损失敞口）交叉 → 剔出跨度
        if len(cols) >= 2:
            cols = _trim_span_cols_crossing_peer_headers(
                data, ar, cols, x0, x1, col_lines, text,
            )
        # 主列弹性加宽可吞溢出且不破坏列结构 → 不引入跨列
        if len(cols) >= 2 and _overflow_absorbable_by_primary_stretch(
            data, ar, cols, x0, x1, col_lines, text,
        ):
            primary = _primary_col_by_max_overlap(x0, x1, cols, col_lines)
            cols = [primary] if primary is not None else cols[:1]
        # 纯文本右缘毛刺：次列重叠不足则收回单列
        elif len(cols) >= 2 and _is_pure_text_label(text):
            cols = _trim_label_right_tip_cols(cols, x0, x1, col_lines)
        if len(cols) < 2:
            continue
        c0, c1 = cols[0], cols[-1]
        colspan = c1 - c0 + 1
        if colspan < 2 or colspan > n_cols:
            continue
        # 跨列表头：文本放到左对齐首格，邻格只标覆盖（不并列）
        ac = c0
        cell_text = strip_span_anchor_mark(_cell(data, ar, ac_found))
        if not cell_text:
            continue
        c0_cell = strip_span_anchor_mark(_cell(data, ar, c0))
        # 跨度内其它列已有同文 → 并列凝结核（两列各写「的资产负债表」），禁止盖成 ⟦↔⟧
        peer_same = False
        for c in range(c0, c1 + 1):
            if c == ac_found:
                continue
            other = strip_span_anchor_mark(_cell(data, ar, c))
            if not other or is_span_cover_mark(other):
                continue
            if other == cell_text or (
                len(cell_text) >= 4 and (cell_text in other or other in cell_text)
            ):
                peer_same = True
                break
        if peer_same:
            continue
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
            "evidence": "nucleus_width_vs_body_cols",
            "confidence": 0.86,
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
        "核心一级资本：",
        "核心一级资本",
        "核心一级资本：扣除项",
        "其他一级资本：",
        "其他一级资本：扣除项",
        "二级资本：",
        "二级资本：扣除项",
    }
    if t in known:
        return True
    # 「其中：…」是表体层次科目，不是通栏小节
    if re.match(r"^其中[：:]", t):
        return False
    # 「核心一级资本：」/「核心一级资本：扣除项」：左缘常在序号列，核宽盖科目列 → 应跨列
    if ("：" in t or ":" in t) and not re.search(r"\d", t):
        cn = len(re.findall(r"[\u4e00-\u9fff]", t))
        if re.match(
            r"^(?:核心一级资本|其他一级资本|二级资本|一级资本)[：:]",
            t,
        ) or ("：扣除项" in t or ":扣除项" in t):
            return True
        if t.endswith(("：", ":")) and 4 <= cn <= 12:
            return True
    if (
        re.fullmatch(r"[\u4e00-\u9fff（）()]{2,16}", t)
        and not re.search(r"\d", t)
        and (
            t.endswith(("数额）", "数额)", "相关信息", "资本要求", "要求", "信息", "部分", "情况"))
            or "充足率" in t
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


def _primary_col_by_max_overlap(
    x0: float,
    x1: float,
    cols: Sequence[int],
    col_lines: Sequence[float],
) -> Optional[int]:
    best_c: Optional[int] = None
    best_ov = -1.0
    for c in cols:
        c = int(c)
        if c < 0 or c >= len(col_lines) - 1:
            continue
        lo = float(col_lines[c])
        hi = float(col_lines[c + 1])
        ov = max(0.0, min(float(x1), hi) - max(float(x0), lo))
        if ov > best_ov + 1e-6:
            best_ov = ov
            best_c = c
        elif abs(ov - best_ov) <= 1e-6 and best_c is not None and c < best_c:
            best_c = c
    return best_c if best_ov > 0 else None


def _is_parent_group_header_text(text: str) -> bool:
    """允许压住子列头的父级通栏（账面余额 / 年头等）。"""
    t = strip_span_anchor_mark(str(text or "")).strip()
    if not t:
        return False
    if re.search(r"(?:19|20)\d{2}\s*年", t):
        return True
    if t in {"账面余额", "账面价值", "公允价值", "摊余成本"}:
        return True
    return False


def _looks_like_standalone_column_header(text: str) -> bool:
    """并列金额/汇总列头（合计、最大损失敞口等），不是表体科目行。"""
    t = strip_span_anchor_mark(str(text or "")).strip()
    if not t or is_span_cover_mark(t):
        return False
    if t in {
        "合计", "小计", "总计", "数额", "金额", "代码", "指标值",
        "最大损失敞口", "账面余额", "账面价值", "面值", "余额", "占比",
    }:
        return True
    # 仅极短列头（≤4 字）；「审慎估值调整」等表体科目不得挡真交叉
    if re.fullmatch(r"[\u4e00-\u9fffA-Za-z]{2,4}", t) and "：" not in t and ":" not in t:
        if re.search(r"扣除|投资|资产|负债|收益|资本|商誉|调整", t):
            return False
        return True
    return False


def _column_has_distinct_nearby_header(
    data: Sequence[Sequence[Any]],
    row: int,
    col: int,
    text: str,
    *,
    window: int = 6,
) -> bool:
    """邻近行该列已有独立「列头」（合计/敞口等），不是普通表体科目。

    表体科目（实收资本…）不得挡真交叉：如「核心一级资本：」核宽盖住序号+科目列。
    """
    t = strip_span_anchor_mark(str(text or "")).strip()
    n_rows = len(data)
    for r in range(max(0, row - window), min(n_rows, row + window + 1)):
        if r == row:
            continue
        v = strip_span_anchor_mark(_cell(data, r, col))
        if not v or is_span_cover_mark(v) or len(v) < 2:
            continue
        if v == t or (len(t) >= 4 and (t in v or v in t)):
            continue
        if not _looks_like_standalone_column_header(v):
            continue
        return True
    return False


def _trim_span_cols_crossing_peer_headers(
    data: Sequence[Sequence[Any]],
    row: int,
    cols: Sequence[int],
    x0: float,
    x1: float,
    col_lines: Sequence[float],
    text: str,
) -> List[int]:
    """核几何压到旁列、且旁列邻近已有独立列头 → 从跨度中剔除这些列。

    例：折行「以公允价值计量」右缘擦进「合计」「最大损失敞口」列带，
    不得标 ↔3 盖住倒数两列；应落回主列（与下方续文同列）。
    父级通栏「账面余额/年头」除外。
    """
    if len(cols) < 2 or len(col_lines) < 3:
        return list(cols)
    t = strip_span_anchor_mark(str(text or "")).strip()
    if not t or _is_parent_group_header_text(t) or _is_section_title_text(t):
        return list(cols)
    # 折行/列头碎片：主列取左缘所在列（与下方续文同界）
    left_cols = _primary_col_by_left_edge(x0, cols, col_lines)
    primary = int(left_cols[0]) if left_cols else None
    if primary is None:
        primary = _primary_col_by_max_overlap(x0, x1, cols, col_lines)
    if primary is None:
        return list(cols)
    blocked = {
        int(c)
        for c in cols
        if int(c) != primary
        and _column_has_distinct_nearby_header(data, row, int(c), t)
    }
    if not blocked:
        return list(cols)
    kept = [int(c) for c in cols if int(c) not in blocked]
    if primary not in kept:
        kept = [primary] + kept
    # 只保留含 primary 的连续段
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
    return out if out else [primary]


def _overflow_absorbable_by_primary_stretch(
    data: Sequence[Sequence[Any]],
    row: int,
    cols: Sequence[int],
    x0: float,
    x1: float,
    col_lines: Sequence[float],
    text: str,
) -> bool:
    """跨列嫌疑若可用「主列弹性加宽」消化且不破坏已有列结构 → True（不引入跨列）。

    条件：
    - 主列 = 核重叠最大的嫌疑列，且左缘落在该主列（文本本属此列，只是偏长）；
    - 嫌疑次列在本行为空（无并列核）；
    - 次列仍由其它行的表体数据支撑（加宽主列不会抹掉列结构）；
    - 非通栏小节标题 / 年头（那些仍应标跨格）。
    """
    if len(cols) < 2 or len(col_lines) < 3:
        return False
    t = strip_span_anchor_mark(str(text or "")).strip()
    if not t:
        return False
    if _is_section_title_text(t) or _is_parent_group_header_text(t):
        return False
    primary = _primary_col_by_max_overlap(x0, x1, cols, col_lines)
    if primary is None:
        return False
    lo = float(col_lines[primary])
    hi = float(col_lines[primary + 1])
    # 左缘不在主列 → 真跨多列（如小节标题从序号列伸进科目列），不可用加宽消化
    if not (lo <= float(x0) < hi):
        return False
    for c in cols:
        c = int(c)
        if c == primary:
            continue
        v = _cell(data, row, c)
        if v and not is_span_cover_mark(v):
            return False
    body_elsewhere = set(_columns_with_body_data(data, exclude_rows=[row]))
    for c in cols:
        c = int(c)
        if c == primary:
            continue
        if c not in body_elsewhere:
            return False
    return True


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
    *,
    words: Optional[Sequence[Dict[str, Any]]] = None,
    col_lines: Optional[Sequence[float]] = None,
) -> List[Dict[str, Any]]:
    """单格标题行：用凝结核宽度对照上下表体多列 → 跨格。

    不以字数/后缀判断。无字框时不做通栏猜测（避免语义误杀）。
    """
    if not data:
        return []
    if not words or not col_lines or len(col_lines) < 3:
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
        t0 = strip_span_anchor_mark(title).strip()
        # 数值列头：跟金额列同列，绝不当通栏挪到左首格
        if t0 in {"数额", "金额", "代码", "指标值", "余额", "面值"}:
            continue
        if any(_looks_like_amount_cell(_cell(data, r, c)) for c in range(n_cols)):
            continue
        box = _find_word_box_for_text(words, t0)
        if box is None:
            continue
        x0, x1 = box
        body_cols = _columns_with_body_data(data, exclude_rows=[r])
        cols = _cols_spanned_by_nucleus_width(x0, x1, col_lines, body_cols)
        if len(cols) >= 2:
            cols = _trim_span_cols_crossing_peer_headers(
                data, r, cols, x0, x1, col_lines, t0,
            )
        if len(cols) < 2:
            continue
        # 偏长科目：加宽主列即可，不标通栏
        if _overflow_absorbable_by_primary_stretch(
            data, r, cols, x0, x1, col_lines, t0,
        ):
            continue
        c0, c1 = cols[0], cols[-1]
        # 通栏标题放到跨度左首格；邻格标覆盖
        ac_left = c0
        covered = [c for c in range(c0, c1 + 1) if c != ac_left]
        if not covered:
            continue
        sp: Dict[str, Any] = {
            "r": r,
            "c": ac_left,
            "rowspan": 1,
            "colspan": c1 - c0 + 1,
            "c0": c0,
            "c1": c1,
            "text": title[:40],
            "covered": covered,
            "evidence": "nucleus_width_vs_body_cols",
            "confidence": 0.88,
        }
        if ac != ac_left:
            sp["move_from"] = ac
        spans.append(sp)
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
    """检测跨格并用符号标识。返回 (新 data, spans)。

    铁律：无凝结核几何交叉则不跨列。禁止靠空邻格/语义猜测强制标 ↔。
    """
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

    # 仅凝结核路径：字框核宽确实吃进多列表体列，才标跨格
    spans_w: List[Dict[str, Any]] = []
    if words and col_lines and len(col_lines) >= 3:
        spans_w = _detect_spans_from_words(out, words, col_lines)
    spans_sec = _detect_section_title_row_spans(
        out, words=words, col_lines=col_lines,
    )
    spans = _merge_span_lists(spans_sec, spans_w)

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
