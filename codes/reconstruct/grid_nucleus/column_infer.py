# -*- coding: utf-8 -*-
"""列槽推断、异常行、公共边界、交叉修复。

**凝结核分列是第一位**（本模块主路径）：
- 同左右边界 → 必须同列
- 同列取最小公共边界（缩进不得拆多列）
- 仅当同一行存在两段不连续文本时才拆列
- 表体优先；跨列表头不得并掉表体两列

跨格标注/表头回挂/质检启发式均为次要，不得为它们放宽/收紧本模块并槽与落列。
"""

from __future__ import annotations

import re
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from codes.reconstruct.grid_nucleus.preprocess import (
    is_amount_nucleus,
    is_code_nucleus,
)
from codes.reconstruct.grid_nucleus.types import ColumnBand, Nucleus, RowCluster

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_change_col_header_text(text: str) -> bool:
    """折行增减列表头碎片：本年末比 / 上年末增减 / 增减(%) 等。"""
    t = str(text or "").strip().replace(" ", "")
    if not t:
        return False
    return any(
        k in t
        for k in ("本年末", "上年末", "本年比", "上年比", "增减", "同比", "环比")
    )


def _is_change_desc_text(text: str) -> bool:
    """列内增减描述：下降0.01个百分点 等。"""
    t = str(text or "").strip()
    if not t:
        return False
    if "百分点" in t:
        return True
    return bool(re.search(r"(?:上升|下降|增加|减少|提高|降低)[\d,，.\s]*", t))


def _is_change_rate_amount(n: Nucleus) -> bool:
    """增减(%) 列常见小数（0.88），排除千分位大额与年份列金额。"""
    if not is_amount_nucleus(n):
        return False
    raw = str(n.text or "").strip()
    if "," in raw or "，" in raw:
        return False
    t = raw.replace("%", "").replace("％", "").strip()
    try:
        v = abs(float(t))
    except ValueError:
        return False
    return v < 80.0


def _cluster_1d(values: Sequence[float], gap: float) -> List[float]:
    if not values:
        return []
    ordered = sorted(values)
    centers: List[float] = []
    buckets: List[List[float]] = []
    for v in ordered:
        if not buckets:
            buckets.append([v])
            centers.append(v)
            continue
        if abs(v - centers[-1]) <= gap:
            buckets[-1].append(v)
            centers[-1] = sum(buckets[-1]) / len(buckets[-1])
        else:
            buckets.append([v])
            centers.append(v)
    return centers


def _slot_anchor(n: Nucleus) -> float:
    """落列初筛种子：左右缘中点（双侧）；最终落列以 [x0,x1] 区间分为准。"""
    return (float(n.x0) + float(n.x1)) / 2.0


def _text_slot_seed(n: Nucleus) -> float:
    """造槽用文本种子：科目用左缘；时段/指标值表头仍用中点（与金额同列）。"""
    if not _looks_like_label_text_nucleus(n):
        return _slot_anchor(n)
    t = str(n.text or "").strip().replace(" ", "")
    if (
        _PERIOD_COL_HEADER_RE.match(t)
        or _is_date_header_nucleus(n)
        or _looks_like_metric_header(n)
        or _looks_like_value_column_header(n)
    ):
        return _slot_anchor(n)
    return float(n.x0)


# 表体两列数值被 OCR/预处理粘成一框：如「0.47% 37」「2.20% 77,221,526」
_GLUED_MULTI_VALUE_RE = re.compile(
    r"^("
    r"[-\d,]+(?:\.\d+)?%?"
    r")\s+("
    r"[-\d,]+(?:\.\d+)?%?"
    r")$"
)


def _is_glued_multi_value_nucleus(n: Nucleus) -> bool:
    """单框内空格分隔的两个数值（常见：PD% + 客户数）。分列须当两列，勿撑开并槽。"""
    t = re.sub(r"\s+", " ", str(n.text or "").strip())
    m = _GLUED_MULTI_VALUE_RE.match(t)
    if not m:
        return False
    a, b = m.group(1), m.group(2)
    # 至少一侧带 %，或两侧都是带逗号/多位的数，避免误伤「下降 0.01」类短描述
    if "%" in a or "%" in b or "％" in a or "％" in b:
        return True
    return ("," in a or "," in b) and (len(a) >= 2 and len(b) >= 2)


def _amount_slot_anchors(n: Nucleus) -> List[float]:
    """金额核槽种子；粘连双值按字符比例拆成两个锚点（表体优先分列）。"""
    if not _is_glued_multi_value_nucleus(n):
        return [_slot_anchor(n)]
    t = re.sub(r"\s+", " ", str(n.text or "").strip())
    m = _GLUED_MULTI_VALUE_RE.match(t)
    if not m:
        return [_slot_anchor(n)]
    a, b = m.group(1), m.group(2)
    w = max(float(n.width or 0.0), 1.0)
    x0 = float(n.x0)
    la, lb = max(len(a), 1), max(len(b), 1)
    total = float(la + lb + 1)
    left_c = x0 + w * (la * 0.5) / total
    right_c = x0 + w * (la + 1 + lb * 0.5) / total
    return [left_c, right_c]


def _strong_body_amt_center(ns: Sequence[Nucleus]) -> Optional[float]:
    """表体金额列中心：≥2 个非粘连金额核的锚点中位数。"""
    xs = [
        _slot_anchor(n)
        for n in ns
        if is_amount_nucleus(n) and not _is_glued_multi_value_nucleus(n)
    ]
    return float(median(xs)) if len(xs) >= 2 else None


def _dist_point_to_interval(x: float, x0: float, x1: float) -> float:
    """点到水平区间的距离：落在区间内为 0（同时用到左右缘）。"""
    lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
    if lo <= x <= hi:
        return 0.0
    return min(abs(x - lo), abs(x - hi))


def _interval_gap(a0: float, a1: float, b0: float, b1: float) -> float:
    """两水平区间间隙：相交为 0，否则为中间空隙长度。"""
    lo_a, hi_a = (a0, a1) if a0 <= a1 else (a1, a0)
    lo_b, hi_b = (b0, b1) if b0 <= b1 else (b1, b0)
    if hi_a < lo_b:
        return lo_b - hi_a
    if hi_b < lo_a:
        return lo_a - hi_b
    return 0.0


def _h_overlap(n: Nucleus, left: float, right: float) -> float:
    """字框 [x0,x1] 与列带 [left,right] 的水平重叠长度。"""
    a0, a1 = (n.x0, n.x1) if n.x0 <= n.x1 else (n.x1, n.x0)
    b0, b1 = (left, right) if left <= right else (right, left)
    return max(0.0, min(a1, b1) - max(a0, b0))


def _both_edges_score(n: Nucleus, lo: float, hi: float) -> float:
    """左右缘同时贴近列带左右缘的加分（双侧约束，禁止只盯一边）。"""
    if hi < lo:
        lo, hi = hi, lo
    # 核宽与列带宽都参与：左缘对左界、右缘对右界
    left_fit = max(0.0, 14.0 - abs(float(n.x0) - lo))
    right_fit = max(0.0, 14.0 - abs(float(n.x1) - hi))
    return left_fit + right_fit


def _score_nucleus_against_band(
    n: Nucleus,
    lo: float,
    hi: float,
    center: float,
    *,
    col_i: int,
    code_cols: set,
    n_cols: int,
    bands: List[Tuple[float, float]],
    serial_cols: Optional[set] = None,
) -> float:
    """字框对列带的匹配分：重叠为主，左右缘同时计分。"""
    if hi < lo:
        lo, hi = hi, lo
    ov = _h_overlap(n, lo, hi)
    if ov > 0:
        score = float(ov)
        score += _both_edges_score(n, lo, hi)
        # 槽心落在字框 [x0,x1] 内：双侧夹住槽心
        if float(n.x0) <= center <= float(n.x1):
            score += 12.0
        if is_amount_nucleus(n):
            # 金额列常见右对齐，右缘权重略高，但仍保留左缘分
            score += max(0.0, 10.0 - abs(float(n.x1) - hi))
        elif is_code_nucleus(n):
            score += max(
                0.0,
                10.0 - abs((float(n.x0) + float(n.x1)) / 2.0 - (lo + hi) / 2.0),
            )
    else:
        # 无重叠：用区间间隙（不是单点到中点）
        gap = _interval_gap(float(n.x0), float(n.x1), lo, hi)
        score = -gap
        # 双侧到列带的偏离，避免只认左或只认右
        score -= 0.12 * (
            abs(float(n.x0) - lo) + abs(float(n.x1) - hi)
        )

    if is_amount_nucleus(n) and col_i in code_cols:
        score -= 80.0
    if is_code_nucleus(n) and col_i not in code_cols and n_cols > 1 and code_cols:
        score -= 10.0
    # 中文科目/折行续文不得进序号列（「底线前）」x 虽落在撑宽后的序号带内）
    if (
        serial_cols
        and col_i in serial_cols
        and n_cols > 1
        and _looks_like_label_text_nucleus(n)
        and not is_serial_nucleus(n)
    ):
        score -= 80.0

    is_chg = _is_change_col_header_text(n.text) or _is_change_desc_text(n.text)
    if is_chg:
        # 增减列：双侧贴合 + 右缘略加强（折行表头常右对齐）
        score += 0.5 * _both_edges_score(n, lo, hi)
        score += max(0.0, 16.0 - abs(float(n.x1) - hi))
    elif (
        col_i >= 1
        and not is_amount_nucleus(n)
        and not is_code_nucleus(n)
        and _CJK_RE.search(str(n.text or ""))
    ):
        # 空隙内中文→右列：须左缘过左列右界、右缘未深入右列太深（双侧）
        prev_lo, prev_hi = bands[col_i - 1]
        gap = lo - prev_hi
        if (
            gap >= 8.0
            and float(n.x0) >= prev_hi - 1.0
            and float(n.x0) < lo
            and float(n.x1) <= lo + max(12.0, gap * 0.35)
        ):
            score += 48.0
    return score


def _band_overlap_ratio(
    a: Tuple[float, float],
    b: Tuple[float, float],
) -> float:
    """两列带重叠 / min(宽度)。凝结核同列时左右边界应大幅重叠。"""
    a0, a1 = (a[0], a[1]) if a[0] <= a[1] else (a[1], a[0])
    b0, b1 = (b[0], b[1]) if b[0] <= b[1] else (b[1], b[0])
    ov = max(0.0, min(a1, b1) - max(a0, b0))
    wa, wb = max(a1 - a0, 1e-6), max(b1 - b0, 1e-6)
    return ov / min(wa, wb)


_SERIAL_TEXT_RE = re.compile(r"^\d{1,3}[a-zA-Z]?$", re.I)
_SERIAL_ALPHA_RE = re.compile(r"^\d{1,3}[a-zA-Z]$", re.I)  # 14a/24b，非纯金额
_SERIAL_LETTER_RE = re.compile(r"^[a-zA-Z]$")  # 纯字母行号 a/b（非右侧列码）
_SERIAL_HEADER_RE = re.compile(r"^(?:序号|序|编号|No\.?|NO\.?|#)$", re.I)
_VALUE_PLACEHOLDER_RE = re.compile(r"^[-–—－]$")
_PLAIN_NUMERIC_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?%?$")
_METRIC_HEADER_RE = re.compile(
    r"(?:交易)?余额|金额|数额|比例|比重|数量|股数|面值|成本|公允价值|账面|变动|指标值"
)
_DATE_HEADER_RE = re.compile(r"(?:19|20)\d{2}\s*年")
# 跨多数据列的上层表头：不得单独成列、不得撑开金额列带
_CROSS_COL_HEADER_RE = re.compile(
    r"折算前数值|折算后数值|折算前|折算后|季度|年度|合计栏|其中：?$"
)
# 分档/字段列表头（有表头即可与表体组成正常列）
_PERIOD_COL_HEADER_RE = re.compile(
    r"^(?:无期限|< ?6个?月|6-12个?月|≥ ?1年|≧ ?1年|值|项目|指标|名称)$"
)
_YEAR_COL_HEADER_RE = re.compile(r"^(?:19|20)\d{2}\s*年$")
_CJK_IN_TEXT_RE = re.compile(r"[\u4e00-\u9fff]")
# 第一列序号带：再右则更像金额/列码，不宜当行号锚点
_SERIAL_LEFT_X0_MAX = 200.0


def is_serial_nucleus(n: Nucleus) -> bool:
    """序号列核：短整数/14a/左侧纯字母，或「序号」表头。"""
    t = str(n.text or "").strip()
    if not t:
        return False
    if _SERIAL_HEADER_RE.match(t):
        return True
    if _SERIAL_TEXT_RE.match(t):
        return True
    # 纯字母：仅左侧才当行号（右侧 a/b 仍是列码）
    return bool(_SERIAL_LETTER_RE.match(t) and float(n.x0) < _SERIAL_LEFT_X0_MAX)


def _is_left_serial_candidate(n: Nucleus) -> bool:
    """可独立成列的左侧序号候选（纯数字 / 数字+字母 / 字母）。"""
    if not is_serial_nucleus(n):
        return False
    t = str(n.text or "").strip()
    if _SERIAL_HEADER_RE.match(t):
        return True
    if _SERIAL_ALPHA_RE.match(t) or _SERIAL_LETTER_RE.match(t):
        return True
    return float(n.x0) < _SERIAL_LEFT_X0_MAX


def _row_has_label_right_of(row: RowCluster, ser: Nucleus) -> bool:
    """同行序号右侧是否有中文科目（独立成列的强证据）。"""
    sx0 = float(ser.x0)
    for m in row.nuclei:
        if m is ser:
            continue
        if not _looks_like_label_text_nucleus(m):
            continue
        if float(m.x0) >= sx0 - 2.0:
            return True
    return False


def _count_serial_label_pairs(rows: Sequence[RowCluster]) -> int:
    """左侧序号 + 右侧中文科目 成对行数。"""
    n = 0
    for r in rows:
        for ser in r.nuclei:
            if not _is_left_serial_candidate(ser):
                continue
            if _row_has_label_right_of(r, ser):
                n += 1
                break
    return n


def _is_date_header_nucleus(n: Nucleus) -> bool:
    """年/日期列表头（常跨子列），不得撑开单列列带。"""
    t = str(n.text or "").strip()
    if not t:
        return False
    if not _DATE_HEADER_RE.search(t):
        return False
    return ("月" in t) or ("日" in t) or (len(t) >= 6)


def _is_cross_column_header_nucleus(n: Nucleus) -> bool:
    """跨多数据列的上层表头（折算前数值/季度等）：不得单独成列。

    只认关键词/日期类跨列表头；不用「短而宽」几何启发式，以免误伤科目文案。
    """
    if is_amount_nucleus(n) or _is_value_like_nucleus(n):
        return False
    t = str(n.text or "").strip().replace(" ", "")
    if not t:
        return False
    if _is_date_header_nucleus(n):
        return True
    return bool(_CROSS_COL_HEADER_RE.search(t))


def _is_value_like_nucleus(n: Nucleus) -> bool:
    """并槽用：金额 / 短整数余额 / 占位「-」。

    14a / 左侧字母行号不算数值；纯 1–3 位数字仍可作余额（与序号列几何区分）。
    """
    if is_amount_nucleus(n):
        return True
    t = str(n.text or "").strip().replace(" ", "")
    if not t:
        return False
    if _SERIAL_ALPHA_RE.match(t) or _SERIAL_LETTER_RE.match(t):
        return False
    if is_serial_nucleus(n) and float(n.x0) < _SERIAL_LEFT_X0_MAX:
        # 左侧短数字更像行号，勿当金额同伴
        return False
    if _VALUE_PLACEHOLDER_RE.match(t):
        return True
    return bool(_PLAIN_NUMERIC_RE.match(t))


def _is_body_data_nucleus(n: Nucleus) -> bool:
    """表体单元格证据：金额/占位/纯文本/文本+数字等均算数据列。

    不含跨列表头、列码、左侧序号。用于「列数以表体为准」——数据不限于数字。
    """
    if is_code_nucleus(n):
        return False
    if _is_left_serial_candidate(n) and float(n.x0) < _SERIAL_LEFT_X0_MAX:
        return False
    # 金额/横线占位/短数字
    if is_amount_nucleus(n) or _is_value_like_nucleus(n):
        return True
    t = str(n.text or "").strip()
    if not t:
        return False
    # 跨列表头/日期跨列表头不是表体数据
    if _is_cross_column_header_nucleus(n) or _is_date_header_nucleus(n):
        return False
    # 列头文案不算表体数据
    if _is_slot_header_nucleus(n):
        return False
    # 纯文本、文本+数字、英文代号等凡非空单元格内容
    return True


def _is_slot_header_nucleus(n: Nucleus) -> bool:
    """列头证据：列码 a/b、分档名、指标名等（跨列表头不算单列头）。"""
    if is_amount_nucleus(n) or _is_value_like_nucleus(n):
        return False
    if _is_cross_column_header_nucleus(n):
        return False
    if is_code_nucleus(n):
        return True
    t = str(n.text or "").strip().replace(" ", "")
    if not t:
        return False
    if _SERIAL_HEADER_RE.match(t):
        return True
    if _PERIOD_COL_HEADER_RE.match(t):
        return True
    if _YEAR_COL_HEADER_RE.match(t):
        return True
    if _looks_like_metric_header(n):
        return True
    if _is_date_header_nucleus(n):
        return True
    return False


def _looks_like_label_text_nucleus(n: Nucleus) -> bool:
    """科目/指标文本（含中文），非序号非金额。"""
    if is_serial_nucleus(n) or is_amount_nucleus(n) or is_code_nucleus(n):
        return False
    t = str(n.text or "").strip()
    # 右侧 a/b 列码不算科目
    if _SERIAL_LETTER_RE.match(t) and float(n.x0) >= _SERIAL_LEFT_X0_MAX:
        return False
    return bool(t and _CJK_IN_TEXT_RE.search(t))


def _looks_like_metric_header(n: Nucleus) -> bool:
    """金额/比例/指标值等列头：交易余额、指标值、占有关余额比例(%) 等。"""
    if _is_value_like_nucleus(n) or is_code_nucleus(n):
        return False
    if _is_date_header_nucleus(n):
        return False
    t = str(n.text or "").strip().replace(" ", "")
    return bool(t and _METRIC_HEADER_RE.search(t))


def _looks_like_value_column_header(n: Nucleus) -> bool:
    """数值列上方的表头：指标值/金额类，或年期「2023年」（与右对齐金额同列）。"""
    if _looks_like_metric_header(n):
        return True
    t = str(n.text or "").strip().replace(" ", "")
    if not t:
        return False
    return bool(_YEAR_COL_HEADER_RE.match(t))


def _col_is_serial_like(ns: Sequence[Nucleus]) -> bool:
    """列主体为左侧序号（≥2 个左侧序号核，且占多数）。

    右侧短整数余额（150）也会命中 is_serial_nucleus，必须用几何排除，
    否则序号列带收紧/中文罚分会打坏「交易余额」列。
    """
    if not ns:
        return False
    if any(is_amount_nucleus(n) for n in ns):
        return False
    xs = [float(n.cx) for n in ns if n.width > 0]
    if xs and float(median(xs)) >= _SERIAL_LEFT_X0_MAX:
        return False
    left_ser = [n for n in ns if _is_left_serial_candidate(n)]
    n_ser = len(left_ser)
    if n_ser < 2:
        if n_ser >= 1 and any(
            _SERIAL_HEADER_RE.match(str(n.text or "").strip()) for n in ns
        ):
            return True
        return False
    return n_ser >= max(2, int(len(ns) * 0.45))


def _is_spanning_nucleus(n: Nucleus, ref_width: float) -> bool:
    """跨多列的宽字框（表题/单位说明）：不得撑开列带，否则相邻金额列会被误并。"""
    w = float(n.width or 0.0)
    if w <= 0:
        return False
    # 粘连双值（PD%+客户数）虽标成金额，实为跨两列，不得撑开列带
    if _is_glued_multi_value_nucleus(n):
        return True
    # 跨列表头（折算前数值等）不得撑开/自成数据列
    if _is_cross_column_header_nucleus(n):
        return True
    # 金额核再宽也是单列右对齐大数，不按跨列剔除
    if is_amount_nucleus(n):
        return False
    # 序号核很窄，永不按跨列剔除
    if is_serial_nucleus(n):
        return False
    # 年列表头跨「交易余额|比例」两子列，不得撑宽金额列带
    if _is_date_header_nucleus(n):
        return True
    # 略收紧：133pt 年标题也曾撑开序号列带与指标列误并
    return w > max(64.0, float(ref_width) * 2.8)


def _provisional_bands(
    rows: List[RowCluster],
    slot_centers: List[float],
) -> List[Tuple[float, float]]:
    """用已分配核的左右缘包络估列带：min(x0)～max(x1)，双侧都用。

    仅用中位数时，增减列会被右对齐小数挤成窄带，折行表头/「下降…」对不上。
    表题等跨列宽框不参与包络，避免把多列金额并成一槽。
    序号列：只用序号核包络，避免小节标题撑宽后把「底线前）」折行续文吸入。
    """
    n_cols = len(slot_centers)
    all_w = [
        float(n.width)
        for r in rows
        for n in r.nuclei
        if n.width > 0 and not is_amount_nucleus(n)
    ]
    ref_w = float(median(all_w)) if all_w else 40.0
    bands: List[Tuple[float, float]] = []
    for c in range(n_cols):
        members = [n for r in rows for n in r.nuclei if n.col_id == c]
        serial_like = _col_is_serial_like(members)
        lefts: List[float] = []
        rights: List[float] = []
        for n in members:
            if _is_spanning_nucleus(n, ref_w):
                continue
            # 序号列带禁止被中文小节标题/折行碎片撑开
            if serial_like and not is_serial_nucleus(n):
                continue
            lefts.append(float(n.x0))
            rights.append(float(n.x1))
        if lefts and rights:
            lo = float(min(lefts))
            hi = float(max(rights))
            if hi <= lo:
                hi = lo + 8.0
            bands.append((lo, hi))
        else:
            mid = float(slot_centers[c])
            bands.append((mid - 16.0, mid + 16.0))
    return bands


def _merge_overlapping_slots(
    rows: List[RowCluster],
    slot_centers: List[float],
    *,
    overlap_ratio: float = 0.35,
    right_eps: float = 10.0,
) -> List[float]:
    """合并左右边界重叠的槽：仅同列大数/小数（右缘对齐）或指标表头↔金额。

    跨列表头 / 列带空重叠不得并槽——分列以凝结核为准，合并单元格只做标注。
    """
    if len(slot_centers) < 2:
        return list(slot_centers)

    # 先按类型偏好初筛，得到可估的列带
    for r in rows:
        for n in r.nuclei:
            pref = _slot_anchor(n)
            n.col_id = min(
                range(len(slot_centers)),
                key=lambda i: abs(slot_centers[i] - pref),
            )
    bands = _provisional_bands(rows, slot_centers)

    parent = list(range(len(slot_centers)))
    # 各槽表体金额中心（粘连双值不计）：分列以表体为准，表头跨格不得把两列并掉
    slot_body_amt: List[Optional[float]] = []
    for c in range(len(slot_centers)):
        ns = [n for r in rows for n in r.nuclei if n.col_id == c]
        slot_body_amt.append(_strong_body_amt_center(ns))
    body_sep = max(18.0, float(right_eps) * 1.8)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    def _component_body_center(root: int) -> Optional[float]:
        xs = [
            slot_body_amt[k]
            for k in range(len(slot_centers))
            if find(k) == root and slot_body_amt[k] is not None
        ]
        return float(median(xs)) if xs else None

    def _body_forbids_union(a: int, b: int) -> bool:
        """两侧组件均有清晰表体金额列且中心分叉 → 禁止并（表头合并仅次要参考）。"""
        ca = _component_body_center(find(a))
        cb = _component_body_center(find(b))
        if ca is None or cb is None:
            return False
        return abs(ca - cb) > body_sep

    for i in range(len(bands) - 1):
        j = i + 1
        left_ns = [n for r in rows for n in r.nuclei if n.col_id == i]
        right_ns = [n for r in rows for n in r.nuclei if n.col_id == j]
        left_code = sum(1 for n in left_ns if is_code_nucleus(n))
        right_code = sum(1 for n in right_ns if is_code_nucleus(n))
        left_amt = sum(1 for n in left_ns if is_amount_nucleus(n))
        right_amt = sum(1 for n in right_ns if is_amount_nucleus(n))
        left_val = sum(1 for n in left_ns if _is_value_like_nucleus(n))
        right_val = sum(1 for n in right_ns if _is_value_like_nucleus(n))
        left_hdr = sum(1 for n in left_ns if _looks_like_value_column_header(n))
        right_hdr = sum(1 for n in right_ns if _looks_like_value_column_header(n))
        left_txt = len(left_ns) - left_amt - left_code
        right_txt = len(right_ns) - right_amt - right_code

        def _val_right_edge(ns: Sequence[Nucleus]) -> Optional[float]:
            xs = [float(n.x1) for n in ns if _is_value_like_nucleus(n)]
            return float(median(xs)) if xs else None

        # 两侧都是金额/数值主体列：默认不并（避免两年度金额列误并）
        # 例外：右缘对齐 → 同列大数与小数/括号数/短杠（杠杆率 13,070,523 vs (98,297)）
        both_amt_cols = left_amt >= 2 and right_amt >= 2
        both_val_cols = (
            left_val >= 2 and right_val >= 2 and left_hdr == 0 and right_hdr == 0
        )
        if both_amt_cols or both_val_cols:
            le = _val_right_edge(left_ns)
            re = _val_right_edge(right_ns)
            same_col_right_align = (
                le is not None
                and re is not None
                and abs(le - re) <= right_eps
            )
            if not same_col_right_align:
                continue
            # 右缘对齐：走下方 overlap / right_close 判定，勿直接 continue
        # 表体优先：组件级金额中心已分叉则永不并（粘连核/跨列表头不得当桥）
        if _body_forbids_union(i, j):
            continue
        # 序号列 ↔ 指标/文本列：绝不并（否则「1 调整后…」粘连、中间空列）
        # 例外：金额列表头（交易余额）↔ 短整数余额列（会被误判为 serial）
        left_ser = _col_is_serial_like(left_ns)
        right_ser = _col_is_serial_like(right_ns)
        metric_hdr_vs_vals = (
            (left_hdr >= 1 and right_val >= 2 and left_val == 0)
            or (right_hdr >= 1 and left_val >= 2 and right_val == 0)
        )
        if not metric_hdr_vs_vals:
            if left_ser and not right_ser and right_amt == 0 and right_txt >= 1:
                continue
            if right_ser and not left_ser and left_amt == 0 and left_txt >= 1:
                continue
        # 纯代码窄列（仅 a/b/「代码」）不得与金额并槽；带表头文案的 a/b 列可以
        pure_code_l = left_code >= 1 and left_txt <= 0 and left_amt == 0
        pure_code_r = right_code >= 1 and right_txt <= 0 and right_amt == 0
        if (pure_code_l and right_amt) or (pure_code_r and left_amt):
            continue
        if (pure_code_l and right_val) or (pure_code_r and left_val):
            continue
        ratio = _band_overlap_ratio(bands[i], bands[j])
        right_close = abs(bands[i][1] - bands[j][1]) <= right_eps
        ov = _h_overlap(
            Nucleus(text="", x0=bands[i][0], y0=0, x1=bands[i][1], y1=1),
            bands[j][0], bands[j][1],
        )
        # 同列右对齐大数/小数：右缘贴齐时直接并（即便双边都是金额列）
        if (both_amt_cols or both_val_cols) and right_close:
            union(i, j)
            continue
        # 仅「指标表头 ↔ 同列金额」可并（同物理列过拆）；跨列表头/空带重叠一律不并，留给跨格标注
        complementary = (
            (left_val == 0 and right_val >= 1 and (left_txt + left_code) >= 1)
            or (right_val == 0 and left_val >= 1 and (right_txt + right_code) >= 1)
        )
        if (
            complementary
            and metric_hdr_vs_vals
            and ov >= 4.0
            and ratio >= 0.12
        ):
            # 左侧科目列在金额列左外侧：勿与首个金额列并
            if left_val == 0 and right_val >= 1:
                amt_ns = [n for n in right_ns if _is_value_like_nucleus(n)]
                txt_ns = [
                    n for n in left_ns
                    if not _is_value_like_nucleus(n)
                    and not is_code_nucleus(n)
                    and not _is_spanning_nucleus(n, 40.0)
                ]
                if amt_ns and txt_ns:
                    amt_lo = float(min(n.x0 for n in amt_ns))
                    txt_hi = float(median([n.x1 for n in txt_ns]))
                    if txt_hi < amt_lo - 6.0:
                        continue
            union(i, j)
            continue
        # 折行增减表头 ↔「下降…百分点」：左缘可差一截，但同属一列（右缘常对齐/邻接）
        left_chg = sum(1 for n in left_ns if _is_change_desc_text(n.text))
        right_chg = sum(1 for n in right_ns if _is_change_desc_text(n.text))
        left_ch = sum(1 for n in left_ns if _is_change_col_header_text(n.text))
        right_ch = sum(1 for n in right_ns if _is_change_col_header_text(n.text))
        pair = (left_chg >= 1 and right_ch >= 1) or (left_ch >= 1 and right_chg >= 1)
        if pair and left_amt < 2 and right_amt < 2:
            gap = bands[j][0] - bands[i][1]
            le = (
                (min(n.x0 for n in left_ns), max(n.x1 for n in left_ns))
                if left_ns else None
            )
            rext = (
                (min(n.x0 for n in right_ns), max(n.x1 for n in right_ns))
                if right_ns else None
            )
            edge_close = bool(le and rext and abs(le[1] - rext[1]) <= 56.0)
            near = -12.0 <= gap <= 28.0
            # 左块落在右块水平投影内（或反向）也视为同列
            nested = False
            if le and rext:
                nested = (
                    (le[0] >= rext[0] - 8 and le[1] <= rext[1] + 8)
                    or (rext[0] >= le[0] - 8 and rext[1] <= le[1] + 8)
                    or (le[1] >= rext[0] - 6 and le[1] <= rext[1] + 6)
                    or (rext[1] >= le[0] - 6 and rext[1] <= le[1] + 6)
                )
            if edge_close or near or nested:
                union(i, j)
                continue
        # 增减列常见：左对齐「下降…百分点」+ 右侧右对齐小数 0.88
        # 小数右缘须贴近描述/表头右缘（同列）；下一年度列的 0.95 不能并进来
        gap = bands[j][0] - bands[i][1]
        if -8.0 <= gap <= 48.0 and left_amt == 0:
            left_edge_ns = [
                n for n in left_ns
                if _is_change_desc_text(n.text) or _is_change_col_header_text(n.text)
            ]
            right_rate_ns = [n for n in right_ns if _is_change_rate_amount(n)]
            if left_edge_ns and right_rate_ns:
                left_x1 = float(median([n.x1 for n in left_edge_ns]))
                rate_x1 = float(median([n.x1 for n in right_rate_ns]))
                if abs(left_x1 - rate_x1) <= 50.0 and rate_x1 <= left_x1 + 40.0:
                    union(i, j)

    # 生成合并后槽心（组内中心均值）
    groups: dict = {}
    for i in range(len(slot_centers)):
        r = find(i)
        groups.setdefault(r, []).append(slot_centers[i])
    merged = [sum(vs) / len(vs) for _, vs in sorted(groups.items())]
    return merged


def _same_row_text_gap(
    rows: Sequence[RowCluster],
    left_col: int,
    right_col: int,
) -> Optional[float]:
    """同行落在两槽的文本核之间的最大水平空隙；无同行双文本则 None。"""
    best: Optional[float] = None
    for r in rows:
        left_ns = [
            n for n in r.nuclei
            if n.col_id == left_col and not is_amount_nucleus(n) and not is_code_nucleus(n)
        ]
        right_ns = [
            n for n in r.nuclei
            if n.col_id == right_col and not is_amount_nucleus(n) and not is_code_nucleus(n)
        ]
        if not left_ns or not right_ns:
            continue
        # 排除纯序号
        left_ns = [n for n in left_ns if not _is_left_serial_candidate(n)]
        right_ns = [n for n in right_ns if not _is_left_serial_candidate(n)]
        if not left_ns or not right_ns:
            continue
        gap = float(min(n.x0 for n in right_ns) - max(n.x1 for n in left_ns))
        if best is None or gap > best:
            best = gap
    return best


def _slot_is_label_like(ns: Sequence[Nucleus]) -> bool:
    """科目/叙述列：有中文文本、无金额/数值、非序号列。

    含金额的年列（即便只有 1 个核）绝不能当科目缩进并槽，否则会把相邻金额列粘死。
    """
    if not ns or _col_is_serial_like(ns):
        return False
    if any(is_amount_nucleus(n) for n in ns):
        return False
    if any(_is_value_like_nucleus(n) for n in ns):
        return False
    n_txt = sum(
        1 for n in ns
        if not is_code_nucleus(n)
        and not _is_left_serial_candidate(n)
        and str(n.text or "").strip()
    )
    if n_txt < 1:
        return False
    return any(_looks_like_label_text_nucleus(n) or _CJK_RE.search(str(n.text or "")) for n in ns)


def _collapse_adjacent_label_slots(
    rows: List[RowCluster],
    slot_centers: List[float],
    *,
    min_same_row_gap: float = 40.0,
) -> List[float]:
    """相邻科目槽若从不同时出现在同一行，则并成一列（缩进≠新列）。

    凝结核原则：同列取最小公共边界。只有同行上两段不连续字符串，才保留拆列。
    min_same_row_gap 保留参数兼容；现行规则是「有同行双文本即不并」。
    """
    del min_same_row_gap  # 显式：空隙大小不参与；有无同行双文本才是判据
    if len(slot_centers) < 2:
        return list(slot_centers)
    centers = list(slot_centers)
    guard = 0
    while len(centers) >= 2 and guard < 32:
        guard += 1
        for r in rows:
            for n in r.nuclei:
                pref = _slot_anchor(n)
                n.col_id = min(
                    range(len(centers)),
                    key=lambda i: abs(centers[i] - pref),
                )
        merged_any = False
        for i in range(len(centers) - 1):
            left_ns = [n for r in rows for n in r.nuclei if n.col_id == i]
            right_ns = [n for r in rows for n in r.nuclei if n.col_id == i + 1]
            if not _slot_is_label_like(left_ns) or not _slot_is_label_like(right_ns):
                continue
            gap = _same_row_text_gap(rows, i, i + 1)
            # 只要存在同行双文本，就是真两列（空隙可大可小，密表仅 20pt 也算）
            # 仅「从不出现在同一行」的缩进层级才并回公共边界
            if gap is not None:
                continue
            new_c = (centers[i] + centers[i + 1]) / 2.0
            centers = centers[:i] + [new_c] + centers[i + 2 :]
            merged_any = True
            break
        if not merged_any:
            break
    return centers


def _absorb_centered_header_slots(
    rows: List[RowCluster],
    slot_centers: List[float],
) -> List[float]:
    """仅吸收「指标/项目」类居中短字段名槽；禁止吞掉分档/期限等并列短表头。

    凝结核核心：不得改变原始左右阅读顺序。无期限 | <6个月 | 6-12个月 | ≥1年
    等同行短表头必须各留一列，绝不能因「短」被并进邻槽。
    """
    if len(slot_centers) < 3:
        return list(slot_centers)
    centers = list(slot_centers)
    for r in rows:
        for n in r.nuclei:
            pref = _slot_anchor(n)
            n.col_id = min(range(len(centers)), key=lambda i: abs(centers[i] - pref))

    def _slot_ns(i: int) -> List[Nucleus]:
        return [n for r in rows for n in r.nuclei if n.col_id == i]

    def _is_field_name_only(texts: Sequence[str]) -> bool:
        """仅「指标/项目」等字段名可吸收；期限分档/字母码等短词不可。"""
        joined = "".join(str(t or "").strip().replace(" ", "") for t in texts)
        if not joined or len(joined) > 8:
            return False
        return any(
            k in joined
            for k in ("指标", "项目", "名称", "内容", "事项", "科目", "摘要")
        )

    def _same_row_peer_short_slots(slot_i: int) -> bool:
        """同行另有其它短文本槽 → 属分列表头带，禁止吸收（保前后顺序）。"""
        my_ns = _slot_ns(slot_i)
        my_rows = {
            id(r)
            for r in rows
            for n in r.nuclei
            if n.col_id == slot_i and str(n.text or "").strip()
        }
        if not my_rows:
            return False
        for r in rows:
            if id(r) not in my_rows:
                continue
            other_cols = {
                n.col_id
                for n in r.nuclei
                if n.col_id != slot_i
                and not is_amount_nucleus(n)
                and not is_code_nucleus(n)
                and not _is_left_serial_candidate(n)
                and str(n.text or "").strip()
                and len(str(n.text or "").strip()) <= 12
            }
            if other_cols:
                return True
        return False

    changed = True
    while changed and len(centers) >= 3:
        changed = False
        for r in rows:
            for n in r.nuclei:
                pref = _slot_anchor(n)
                n.col_id = min(
                    range(len(centers)), key=lambda i: abs(centers[i] - pref)
                )
        absorb_i = None
        for i in range(len(centers)):
            ns = _slot_ns(i)
            if not ns or _col_is_serial_like(ns):
                continue
            ref_w = 40.0
            ns_core = [n for n in ns if not _is_spanning_nucleus(n, ref_w)]
            use_ns = ns_core or ns
            n_amt = sum(1 for n in use_ns if is_amount_nucleus(n))
            if n_amt >= 1:
                continue
            texts = [
                str(n.text or "").strip() for n in use_ns if str(n.text or "").strip()
            ]
            if not texts:
                continue
            # 同行多短表头（期限分档）→ 永不吸收
            if _same_row_peer_short_slots(i):
                continue
            short_field = _is_field_name_only(texts)
            singleton_long = len(texts) == 1 and len(texts[0]) >= 10
            if not (short_field or singleton_long):
                continue
            left_cands = []
            for j in range(i):
                jns = _slot_ns(j)
                if not jns or _col_is_serial_like(jns):
                    continue
                j_core = [n for n in jns if not _is_spanning_nucleus(n, ref_w)] or jns
                j_amt = sum(1 for n in j_core if is_amount_nucleus(n))
                j_txt = len(j_core) - j_amt
                if j_amt >= 2 and j_txt == 0:
                    continue
                if j_txt >= 2:
                    left_cands.append(j)
            if not left_cands:
                continue
            if short_field:
                gap = centers[i] - centers[left_cands[-1]]
                if gap > 160.0:
                    continue
            absorb_i = i
            break
        if absorb_i is None:
            break
        centers = [centers[k] for k in range(len(centers)) if k != absorb_i]
        changed = True
    return centers


def _prune_slots_without_body_evidence(
    rows: List[RowCluster],
    slot_centers: List[float],
) -> List[float]:
    """列数以表体为准：去掉可疑/幽灵槽。

    正常列 ≈ 表头 + 至少一条表体数据。
    「一列只有一个数据、又无表头」非常可疑 → 多分出来的缝列，丢弃。
    """
    if len(slot_centers) < 2:
        return list(slot_centers)
    centers = list(slot_centers)
    for r in rows:
        for n in r.nuclei:
            pref = _slot_anchor(n)
            n.col_id = min(range(len(centers)), key=lambda i: abs(centers[i] - pref))

    body_rows = [
        r for r in rows
        if any(_is_body_data_nucleus(n) for n in r.nuclei)
    ]
    if not body_rows:
        return centers

    keep: List[float] = []
    for i, c in enumerate(centers):
        all_ns = [n for r in rows for n in r.nuclei if n.col_id == i]
        body_ns = [n for r in body_rows for n in r.nuclei if n.col_id == i]
        n_data = sum(1 for n in body_ns if _is_body_data_nucleus(n))
        n_header = sum(1 for n in all_ns if _is_slot_header_nucleus(n))

        # 序号列 / 科目列
        if _col_is_serial_like(all_ns):
            keep.append(c)
            continue
        n_label = sum(
            1 for n in body_ns
            if _looks_like_label_text_nucleus(n) and not _is_cross_column_header_nucleus(n)
        )
        if n_label >= 1:
            keep.append(c)
            continue

        # 正常列：表头 + ≥1 表体
        if n_header >= 1 and n_data >= 1:
            keep.append(c)
            continue
        # 多点表体：即使表头被跨格吃掉也先留
        if n_data >= 2:
            keep.append(c)
            continue
        # 有列码/分档/年列表头、本页表体全空（灰格）——结构列保留
        if n_header >= 1 and n_data == 0 and any(
            is_code_nucleus(n)
            or _PERIOD_COL_HEADER_RE.match(str(n.text or "").strip().replace(" ", ""))
            or _YEAR_COL_HEADER_RE.match(str(n.text or "").strip().replace(" ", ""))
            for n in all_ns
        ):
            keep.append(c)
            continue
        # 单点数据且无表头 → 可疑缝列；无证据碎片 → 丢弃
    return keep if len(keep) >= 2 else centers


def infer_column_slots(
    rows: List[RowCluster],
    *,
    col_gap_factor: float = 0.6,
    max_cols: int = 20,
) -> Tuple[int, List[float]]:
    """返回 (N_cols, 槽锚点列表)。优先用 body 候选行的锚点聚类。"""
    from codes.reconstruct.grid_nucleus.word_segment import filter_rows_for_column_infer

    # 附注长行不参与槽推断，否则会把相邻年列并成一格
    rows_for_slots = filter_rows_for_column_infer(rows)
    # body 候选：含金额 或 块数接近众数
    counts = [len(r.nuclei) for r in rows_for_slots if r.nuclei]
    mode = max(set(counts), key=counts.count) if counts else 0
    body_rows = []
    for r in rows_for_slots:
        n_amt = sum(1 for n in r.nuclei if is_amount_nucleus(n))
        if n_amt >= 1 or (mode and len(r.nuclei) >= max(2, int(mode * 0.7))):
            body_rows.append(r)
    if not body_rows:
        body_rows = list(rows_for_slots)

    amt_anchors: List[float] = []
    text_anchors: List[float] = []
    code_anchors: List[float] = []
    serial_anchors: List[float] = []
    widths: List[float] = []
    text_lefts: List[float] = []
    for r in body_rows:
        for n in r.nuclei:
            if n.width > 0:
                widths.append(n.width)
            # 左侧序号优先于 a/b 列码（否则行号 a 被吃进代码锚）
            if _is_left_serial_candidate(n):
                serial_anchors.append(_slot_anchor(n))
            elif is_code_nucleus(n):
                code_anchors.append(_slot_anchor(n))
            elif is_amount_nucleus(n):
                # 粘连「0.47% 37」拆成两锚，按表体两列聚类
                amt_anchors.extend(_amount_slot_anchors(n))
            elif _is_value_like_nucleus(n):
                # 右对齐「-」常偏出列心（落在 a/b 缝里），不得单独造槽；
                # 仍作表体证据（prune/落列），归入最近已有数据列。
                t_ph = str(n.text or "").strip().replace(" ", "")
                if _VALUE_PLACEHOLDER_RE.match(t_ph):
                    continue
                amt_anchors.append(_slot_anchor(n))
            else:
                # 跨列表头不参与造槽（折算前数值等不得成幽灵列）
                # 注意：宽科目框仍要进 text_anchors，否则会丢掉标签列
                if _is_cross_column_header_nucleus(n):
                    continue
                # 科目造槽用左缘；缩进并列靠 _collapse_adjacent_label_slots
                text_anchors.append(_text_slot_seed(n))
                text_lefts.append(n.x0)
    # 独立成列条件：表头「序号」/ 字母后缀 / ≥2 行「序号+科目」对 / ≥3 个左侧锚
    if serial_anchors:
        leftish = [a for a in serial_anchors if a < _SERIAL_LEFT_X0_MAX]
        has_alpha = any(
            _SERIAL_ALPHA_RE.match(str(n.text or "").strip())
            or _SERIAL_LETTER_RE.match(str(n.text or "").strip())
            for r in body_rows
            for n in r.nuclei
            if _is_left_serial_candidate(n)
        )
        has_ser_header = any(
            _SERIAL_HEADER_RE.match(str(n.text or "").strip())
            for r in body_rows
            for n in r.nuclei
        )
        pair_n = _count_serial_label_pairs(body_rows)
        allow = (
            has_ser_header
            or has_alpha
            or pair_n >= 2
            or len(leftish) >= 3
        )
        if not allow:
            serial_anchors = []
        else:
            serial_anchors = leftish or serial_anchors
    anchors = amt_anchors + text_anchors
    if not anchors and not code_anchors and not serial_anchors:
        return 0, []

    w_med = median(widths) if widths else 40.0
    # 文本左缘很紧（同分列），金额间距才用字宽；gap 不宜跟最长文本宽度走
    if text_lefts:
        gap = max(10.0, min(w_med * float(col_gap_factor), 28.0))
    else:
        gap = max(8.0, w_med * float(col_gap_factor))
    # 金额单独聚类，避免与右侧「代码」列因 gap≈28 并槽
    centers = _cluster_1d(anchors, gap) if anchors else []
    if code_anchors:
        code_gap = max(6.0, min(gap * 0.5, 14.0))
        for c in _cluster_1d(code_anchors, code_gap):
            if not centers or min(abs(c - x) for x in centers) > 12.0:
                centers.append(c)
        centers = sorted(centers)
    # 序号列独立锚点（1/14a），勿与科目文本并成一槽
    if serial_anchors:
        ser_gap = max(6.0, min(gap * 0.45, 12.0))
        for c in _cluster_1d(serial_anchors, ser_gap):
            if not centers or min(abs(c - x) for x in centers) > 10.0:
                centers.append(c)
        centers = sorted(centers)
    if len(centers) > max_cols:
        centers = _cluster_1d(centers, gap * 1.5)[:max_cols]
    # 合并过近的槽心（尤其文本左缘应并成一列）；金额↔代码、序号↔文本保持分离
    if len(centers) >= 2:
        min_sep = max(14.0, min(w_med * 0.45, 36.0))
        code_set = set(_cluster_1d(code_anchors, max(6.0, min(gap * 0.5, 14.0)))) if code_anchors else set()
        ser_set = set(_cluster_1d(serial_anchors, max(6.0, min(gap * 0.45, 12.0)))) if serial_anchors else set()
        merged = [centers[0]]
        for c in centers[1:]:
            prev = merged[-1]
            near_code = any(abs(c - z) <= 8 or abs(prev - z) <= 8 for z in code_set)
            near_amt = any(abs(c - z) <= 8 or abs(prev - z) <= 8 for z in amt_anchors)
            near_ser = any(abs(c - z) <= 8 or abs(prev - z) <= 8 for z in ser_set)
            near_txt = any(abs(c - z) <= 8 or abs(prev - z) <= 8 for z in text_anchors)
            if c - prev < min_sep and not (
                (near_code and near_amt)
                or (near_ser and near_txt)
                or (near_ser and near_amt)
            ):
                merged[-1] = (prev + c) / 2.0
            else:
                merged.append(c)
        centers = merged[:max_cols]

    # 再按左右带重叠合并（大数框与小数框同列）——跳过「金额↔代码」误并
    # 必须用 rows_for_slots：附注长框跨多列会把年列并掉
    centers = _merge_overlapping_slots(rows_for_slots, centers)
    # 居中「指标」短表头 / 孤立长标签并入左侧科目槽
    centers = _absorb_centered_header_slots(rows_for_slots, centers)
    # 科目缩进并回同列：无同行大空隙则取公共边界（凝结核）
    centers = _collapse_adjacent_label_slots(rows_for_slots, centers)
    # 列数以表体为准：去掉无金额/横线占位证据的幽灵空列
    centers = _prune_slots_without_body_evidence(rows_for_slots, centers)
    if len(centers) > max_cols:
        centers = centers[:max_cols]
    n_cols = len(centers)
    return n_cols, centers


def assign_nuclei_to_slots(rows: List[RowCluster], slot_centers: List[float]) -> None:
    """落列：字框 [x0,x1] 与列带双边匹配（重叠 + 左右缘），禁止只认单侧。

    原则：同列上下格的左右范围应一致（表头合并/跨列除外）；宽窄金额不得因中心漂移拆列。
    """
    if not slot_centers:
        return
    centers = list(slot_centers)
    n_cols = len(centers)

    # —— 1) 初筛：用左右缘中点靠近槽心（双侧）；最终以步骤 3 区间分为准 ——
    for r in rows:
        for n in r.nuclei:
            pref = _slot_anchor(n)
            n.col_id = min(range(n_cols), key=lambda i: abs(centers[i] - pref))

    # —— 2) 列带 + 识别代码主导列 / 序号列 ——
    bands = _provisional_bands(rows, centers)
    code_cols: set = set()
    serial_cols: set = set()
    for c in range(n_cols):
        members = [n for r in rows for n in r.nuclei if n.col_id == c]
        if not members:
            continue
        n_code = sum(1 for n in members if is_code_nucleus(n))
        n_amt = sum(1 for n in members if is_amount_nucleus(n))
        if n_code >= 1 and n_code >= n_amt:
            code_cols.add(c)
        # 表头「代码」也钉死该列
        if any(str(n.text).strip() == "代码" for n in members):
            code_cols.add(c)
        if _col_is_serial_like(members):
            serial_cols.add(c)

    # —— 3) 双边重分配：最大化 [x0,x1]∩列带，并同时贴合左右缘 ——
    for r in rows:
        for n in r.nuclei:
            best_i = 0
            best_score = float("-inf")
            for i, (lo, hi) in enumerate(bands):
                score = _score_nucleus_against_band(
                    n, lo, hi, centers[i],
                    col_i=i,
                    code_cols=code_cols,
                    n_cols=n_cols,
                    bands=bands,
                    serial_cols=serial_cols,
                )
                if score > best_score:
                    best_score = score
                    best_i = i
            n.col_id = max(0, min(n_cols - 1, best_i))

    # —— 4) 同列金额同伴约束：右缘接近的金额核归同一列（增减列 0.01 不得并进上年金额列）——
    _snap_amounts_by_peer_right_edge(rows, n_cols)
    # —— 5) 上下行同列左右范围一致（表头合并除外）——
    _snap_vertical_same_column_range(rows, n_cols)
    # —— 6) 序号与科目文本强制分列 ——
    _force_serial_away_from_labels(rows, centers)
    # —— 6b) 中文折行续文不得留在序号列 ——
    _force_labels_out_of_serial_cols(rows, centers, serial_cols)
    # —— 7) 增减折行表头与「下降…百分点」同伴同列 ——
    _snap_change_header_desc_peers(rows, n_cols)
    # —— 8) 凝结核铁律：左右边界实质相同 → 必须同列 ——
    _lock_same_bound_nuclei(rows)


def _lock_same_bound_nuclei(
    rows: List[RowCluster],
    *,
    eps: float = 2.5,
) -> None:
    """左右边界实质相同的核必须同列（如「2023年」与「指标值」）。

    不得因双边打分、幽灵金额槽把同界核拆到两列。
    """
    nuclei = [n for r in rows for n in r.nuclei if float(n.width or 0.0) > 0]
    if len(nuclei) < 2:
        return
    parent = list(range(len(nuclei)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(len(nuclei)):
        ai = nuclei[i]
        for j in range(i + 1, len(nuclei)):
            bj = nuclei[j]
            if (
                abs(float(ai.x0) - float(bj.x0)) <= eps
                and abs(float(ai.x1) - float(bj.x1)) <= eps
            ):
                union(i, j)

    groups: Dict[int, List[Nucleus]] = {}
    for i, n in enumerate(nuclei):
        groups.setdefault(find(i), []).append(n)

    for members in groups.values():
        if len(members) < 2:
            continue
        votes: Dict[int, int] = {}
        for n in members:
            c = int(n.col_id)
            if c < 0:
                continue
            votes[c] = votes.get(c, 0) + 1
        if not votes:
            continue
        amt_cols = {int(n.col_id) for n in members if is_amount_nucleus(n) and n.col_id >= 0}
        # 多数决；同票时优先已有金额的列，再取较左列（稳定）
        best = max(
            votes.keys(),
            key=lambda c: (votes[c], 1 if c in amt_cols else 0, -c),
        )
        for n in members:
            n.col_id = best


def _force_serial_away_from_labels(
    rows: List[RowCluster],
    slot_centers: List[float],
) -> None:
    """同行序号与中文科目不得同列：把右侧文本推到序号右侧最近槽。"""
    n_cols = len(slot_centers)
    if n_cols < 2:
        return
    for r in rows:
        serials = [
            n for n in r.nuclei
            if _is_left_serial_candidate(n) and 0 <= n.col_id < n_cols
        ]
        if not serials:
            continue
        for ser in serials:
            labels = [
                m for m in r.nuclei
                if m is not ser
                and m.col_id == ser.col_id
                and _looks_like_label_text_nucleus(m)
                and float(m.x0) >= float(ser.x0) - 2.0
            ]
            if not labels:
                continue
            cands = [
                i for i in range(n_cols)
                if i != ser.col_id and (
                    slot_centers[i] > slot_centers[ser.col_id] + 6.0
                    or i > ser.col_id
                )
            ]
            if not cands:
                cands = [i for i in range(n_cols) if i != ser.col_id]
            if not cands:
                continue
            for lab in labels:
                lab.col_id = min(
                    cands,
                    key=lambda i: abs(slot_centers[i] - float(lab.cx)),
                )


def _force_labels_out_of_serial_cols(
    rows: List[RowCluster],
    slot_centers: List[float],
    serial_cols: set,
) -> None:
    """折行续文等中文核若仍落在序号列，推到右侧最近非序号槽。"""
    n_cols = len(slot_centers)
    if n_cols < 2 or not serial_cols:
        return
    label_cands = [i for i in range(n_cols) if i not in serial_cols]
    if not label_cands:
        return
    for r in rows:
        for n in r.nuclei:
            if n.col_id not in serial_cols:
                continue
            if is_serial_nucleus(n) or not _looks_like_label_text_nucleus(n):
                continue
            n.col_id = min(
                label_cands,
                key=lambda i: abs(slot_centers[i] - float(n.cx)),
            )


def _is_vertical_column_anchor(n: Nucleus) -> bool:
    """可参与上下对齐的格：排除跨列表头/日期合并格。"""
    if _is_date_header_nucleus(n):
        return False
    if _is_spanning_nucleus(n, 40.0):
        return False
    return True


def _vertical_same_col_score(cur: Nucleus, prev: Nucleus) -> float:
    """上一行格与本格像「同列」的得分；越高越应继承 prev.col_id。"""
    ov = _h_overlap(cur, float(prev.x0), float(prev.x1))
    # 右对齐数值列：右缘贴齐 / 窄框落在宽框水平投影内
    if _is_value_like_nucleus(cur) and _is_value_like_nucleus(prev):
        dx1 = abs(float(cur.x1) - float(prev.x1))
        if dx1 <= 14.0:
            return 200.0 - dx1 + ov
        nested = (
            (float(cur.x0) >= float(prev.x0) - 2.0 and float(cur.x1) <= float(prev.x1) + 2.0)
            or (float(prev.x0) >= float(cur.x0) - 2.0 and float(prev.x1) <= float(cur.x1) + 2.0)
        )
        if nested and ov >= 2.0:
            return 150.0 + ov
    # 文本/科目列：左缘接近且有水平重叠
    dx0 = abs(float(cur.x0) - float(prev.x0))
    if dx0 <= 14.0 and ov >= 4.0:
        return 80.0 - dx0 + ov
    return ov


def _snap_vertical_same_column_range(
    rows: List[RowCluster],
    n_cols: int,
    *,
    min_score: float = 12.0,
) -> None:
    """本格左右范围应与上一行同列格一致（表头合并单元格除外）。

    自上而下：用上一数据行的 [x0,x1] 约束本行落列，避免宽金额与窄金额/短杠因中心漂移拆列。
    """
    if n_cols < 2 or len(rows) < 2:
        return
    prev_anchors: List[Nucleus] = []
    for r in rows:
        usable = [
            n for n in r.nuclei
            if _is_vertical_column_anchor(n) and 0 <= n.col_id < n_cols
        ]
        if prev_anchors and usable:
            # 本行已占用的列（数值格）：避免两个金额抢同一列
            for n in usable:
                best_prev: Optional[Nucleus] = None
                best_sc = float(min_score)
                for p in prev_anchors:
                    if not (0 <= p.col_id < n_cols):
                        continue
                    sc = _vertical_same_col_score(n, p)
                    if sc > best_sc:
                        best_sc = sc
                        best_prev = p
                if best_prev is None or best_prev.col_id == n.col_id:
                    continue
                tgt = best_prev.col_id
                # 同列已有另一数值格且双方都是数值 → 勿强行并（真两列）
                clash = [
                    m for m in usable
                    if m is not n
                    and m.col_id == tgt
                    and _is_value_like_nucleus(m)
                    and _is_value_like_nucleus(n)
                ]
                if clash:
                    continue
                n.col_id = tgt

        # 有数值的行作为下一行的垂直参照；纯表头/叙述不覆盖参照
        vals = [n for n in usable if _is_value_like_nucleus(n)]
        if len(vals) >= 1:
            prev_anchors = usable


def _snap_amounts_by_peer_right_edge(
    rows: List[RowCluster],
    n_cols: int,
    *,
    min_peers: int = 2,
    x1_tol: float = 14.0,
) -> None:
    """用同列已有金额的右缘分布校正落列，避免个别核因近邻槽心误入左列。

    含短整数/括号数/短杠（value_like），与上下行同列约束互补。
    """
    if n_cols < 2:
        return
    col_x1s: List[List[float]] = [[] for _ in range(n_cols)]
    for r in rows:
        for n in r.nuclei:
            if 0 <= n.col_id < n_cols and _is_value_like_nucleus(n):
                col_x1s[n.col_id].append(n.x1)
    medians: List[Optional[float]] = []
    for xs in col_x1s:
        if len(xs) >= min_peers:
            medians.append(float(median(xs)))
        else:
            medians.append(None)
    strong = [i for i, m in enumerate(medians) if m is not None]
    if len(strong) < 2:
        return

    for r in rows:
        for n in r.nuclei:
            if not _is_value_like_nucleus(n):
                continue
            cur = n.col_id
            best = cur
            best_dist = abs(n.x1 - medians[cur]) if (
                0 <= cur < n_cols and medians[cur] is not None
            ) else 1e9
            for i in strong:
                d = abs(n.x1 - float(medians[i]))
                if d <= x1_tol and d + 1e-6 < best_dist:
                    best_dist = d
                    best = i
            # 若当前列金额同伴少、右侧强列右缘更贴：跟同伴走
            if best != cur:
                cur_n = len(col_x1s[cur]) if 0 <= cur < n_cols else 0
                if cur_n < min_peers or best_dist + 2.0 < abs(
                    n.x1 - (medians[cur] or n.x1)
                ):
                    n.col_id = best


def _snap_change_header_desc_peers(
    rows: List[RowCluster],
    n_cols: int,
) -> None:
    """折行增减表头与列内「下降…百分点」拉到同一列（右缘/邻接约束）。"""
    if n_cols < 2:
        return
    hdr_cols = [
        n.col_id
        for r in rows
        for n in r.nuclei
        if 0 <= n.col_id < n_cols and _is_change_col_header_text(n.text)
    ]
    desc_cols = [
        n.col_id
        for r in rows
        for n in r.nuclei
        if 0 <= n.col_id < n_cols and _is_change_desc_text(n.text)
    ]
    if not hdr_cols or not desc_cols:
        return
    # 多数表头所在列 / 多数描述所在列
    from collections import Counter

    h_col = Counter(hdr_cols).most_common(1)[0][0]
    d_col = Counter(desc_cols).most_common(1)[0][0]
    if h_col == d_col:
        return
    # 选「既靠近描述右缘中位、又靠近表头右缘中位」的目标列：优先描述列
    # （描述与金额列分离更稳；表头折行右缘可能偏右）
    target = d_col
    for r in rows:
        for n in r.nuclei:
            if _is_change_col_header_text(n.text) or _is_change_desc_text(n.text):
                # 勿并入纯金额主导列
                peers = [
                    m for rr in rows for m in rr.nuclei
                    if m.col_id == target
                ]
                n_amt = sum(1 for m in peers if is_amount_nucleus(m))
                n_chg = sum(
                    1 for m in peers
                    if _is_change_desc_text(m.text) or _is_change_col_header_text(m.text)
                )
                if n_amt >= 2 and n_chg == 0:
                    continue
                n.col_id = target


def mark_abnormal_rows(
    rows: List[RowCluster],
    n_cols: int,
    *,
    count_ratio: float = 0.5,
    wide_factor: float = 1.8,
    cross_eps: float = 5.0,
) -> None:
    body_widths = [
        n.width for r in rows for n in r.nuclei
        if is_amount_nucleus(n) and n.width > 0
    ]
    w_med = median(body_widths) if body_widths else (
        median([n.width for r in rows for n in r.nuclei if n.width > 0]) if rows else 40.0
    )

    for idx, r in enumerate(rows):
        n_nuc = len(r.nuclei)
        n_amt = sum(1 for n in r.nuclei if is_amount_nucleus(n))
        slots_used = len({n.col_id for n in r.nuclei if n.col_id >= 0})

        # 空单元格豁免：金额够或槽覆盖够
        sparse_ok = (
            n_cols > 0 and (
                n_amt >= max(1, int(n_cols * 0.4))
                or slots_used >= max(1, int(n_cols * 0.6))
            )
        )

        abnormal = False
        role = "unknown"

        if n_cols > 0 and n_nuc < n_cols * count_ratio and not sparse_ok:
            abnormal = True
            role = "abnormal"
        if n_nuc == 1 and r.nuclei[0].width > wide_factor * w_med:
            abnormal = True
            role = "title" if not n_amt else "abnormal"
        if n_amt == 0 and n_nuc <= 2:
            # 可能表头/标题
            joined = "".join(n.text for n in r.nuclei)
            if len(joined) >= 4 and not any(ch.isdigit() for ch in joined[:8]):
                if n_nuc == 1:
                    role = "title"
                    abnormal = True
                else:
                    role = "header"

        # 与下一行交叉
        if idx + 1 < len(rows) and not abnormal:
            nxt = rows[idx + 1]
            for n in r.nuclei:
                if n.col_id < 0:
                    continue
                for m in nxt.nuclei:
                    if m.col_id == n.col_id and n.x1 > m.x0 + cross_eps and n.x0 < m.x0:
                        # 轻微重叠可忽略；明显侵入标异常
                        if n.x1 - m.x0 > cross_eps * 2:
                            abnormal = True
                            role = "abnormal"
                            break
                if abnormal:
                    break

        if not abnormal and n_amt >= 1:
            role = "body"
        elif not abnormal and role == "unknown":
            role = "header" if n_amt == 0 else "body"

        r.is_abnormal = abnormal
        r.role = role if abnormal or role != "unknown" else ("body" if n_amt else "header")


def compute_column_bands(
    rows: List[RowCluster],
    n_cols: int,
) -> List[ColumnBand]:
    bands: List[ColumnBand] = []
    for c in range(n_cols):
        lefts: List[float] = []
        rights: List[float] = []
        amt = 0
        total = 0
        for r in rows:
            if r.is_abnormal or r.role in ("title",):
                continue
            if r.role not in ("body", "header", "unknown"):
                continue
            # 公共边界：优先 body
            if r.role != "body" and any(x.role == "body" for x in rows):
                continue
            for n in r.nuclei:
                if n.col_id != c:
                    continue
                if "span_merge" in n.flags:
                    continue
                lefts.append(n.x0)
                rights.append(n.x1)
                total += 1
                if is_amount_nucleus(n):
                    amt += 1
        weak = len(lefts) < 2
        if not lefts:
            # 占位：用所有行
            for r in rows:
                for n in r.nuclei:
                    if n.col_id == c:
                        lefts.append(n.x0)
                        rights.append(n.x1)
        if not lefts:
            lefts = [float(c) * 50.0]
            rights = [lefts[0] + 40.0]
            weak = True
        align = "right" if total and amt / max(total, 1) >= 0.5 else "left"
        bands.append(ColumnBand(
            col_id=c,
            left=float(median(lefts)),
            right=float(median(rights)),
            align=align,
            weak=weak,
        ))
    return bands


def fix_column_crossings(
    rows: List[RowCluster],
    bands: List[ColumnBand],
    *,
    cross_eps: float = 5.0,
    min_gap: float = 1.0,
) -> int:
    """修复相邻列交叉；返回发生交叉的邻接对数。"""
    crossed = 0
    for i in range(len(bands) - 1):
        if bands[i].right <= bands[i + 1].left + cross_eps:
            continue
        crossed += 1
        # 标记横跨核
        for r in rows:
            if not r.is_abnormal and r.role == "body":
                continue
            for n in r.nuclei:
                if n.col_id in (i, i + 1) and n.x0 < bands[i + 1].left and n.x1 > bands[i].right:
                    n.flags.add("span_merge")
        # 重算这两列（排除 span）
        for c in (i, i + 1):
            lefts, rights = [], []
            for r in rows:
                if r.is_abnormal:
                    continue
                if r.role != "body":
                    continue
                for n in r.nuclei:
                    if n.col_id != c or "span_merge" in n.flags:
                        continue
                    lefts.append(n.x0)
                    rights.append(n.x1)
            if lefts:
                bands[c].left = float(median(lefts))
                bands[c].right = float(median(rights))
        if bands[i].right > bands[i + 1].left - min_gap:
            mid = (bands[i].left + bands[i + 1].right) / 2.0
            bands[i].right = mid - min_gap / 2.0
            bands[i + 1].left = mid + min_gap / 2.0
    return crossed
