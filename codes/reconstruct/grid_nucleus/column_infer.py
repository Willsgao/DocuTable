# -*- coding: utf-8 -*-
"""列槽推断、异常行、公共边界、交叉修复。

**凝结核分列是第一位**（本模块主路径）：
- 同左右边界 → 必须同列
- 同列取最小公共边界（缩进不得拆多列）
- 拆列只认同行大空隙；小空隙用上下列带整体区域约束，不得机械拆列
- 表体优先；跨列表头不得并掉表体两列

**表头约束（拆/并均参考）**：
- 正常表每一列应有独立表头文本（跨列合并单元格除外）
- 两槽若各有不同独立表头 → 禁止合并（如列码 j|k|l）
- 表头空的槽更像拆列缝：超额截断时优先丢掉

跨格标注/表头回挂/质检启发式均为次要，不得为它们放宽/收紧本模块并槽与落列。
"""

from __future__ import annotations

import re
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    r"|发生额|占有关"
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
# CC2 分组标题：左缘在序号带，禁止被赶出序号列
_SECTION_LEAD_KEEP_IN_SERIAL = frozenset({"资产", "负债", "权益", "股东权益"})


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


def _slot_independent_header_labels(ns: Sequence[Nucleus]) -> List[str]:
    """槽内独立表头文本（列码/分档/指标名）；跨列大标题不计。"""
    labels: List[str] = []
    for n in ns:
        if not _is_slot_header_nucleus(n):
            continue
        t = str(n.text or "").strip()
        if t:
            labels.append(t)
    return labels


def _slots_have_distinct_independent_headers(
    left_ns: Sequence[Nucleus],
    right_ns: Sequence[Nucleus],
) -> bool:
    """两槽各有独立表头且文本集合不同 → 视为两列，禁止合并。

    正常表（非合并单元格）每列一头；j|k、风险权|预期损 不得并回一列。
    一侧无表头（空缝 / 仅金额）→ False，允许与邻列互补合并。
    """
    left = set(_slot_independent_header_labels(left_ns))
    right = set(_slot_independent_header_labels(right_ns))
    if not left or not right:
        return False
    return left != right


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
    """金额/比例/指标值等列头：交易余额、指标值、占有关余额比例(%) 等。

    长科目名即便含「金额/资产」子串（如「…应扣除金额」）也不是列头。
    """
    if _is_value_like_nucleus(n) or is_code_nucleus(n):
        return False
    if _is_date_header_nucleus(n):
        return False
    t = str(n.text or "").strip().replace(" ", "")
    if not t:
        return False
    # 折行长指标头：占有关… / …发生额…
    if re.search(r"发生额|占有关|指标值", t):
        return True
    # 其余仅认短列头，避免长科目误中「金额/余额」
    if len(t) > 12:
        return False
    # 科目折行续文：应扣除金额 / 扣除的金额 / 其中：… —— 不是数值列头
    if any(
        k in t
        for k in ("扣除", "其中", "未并表", "可计入", "净额", "总和", "缺口")
    ):
        return False
    return bool(_METRIC_HEADER_RE.search(t))


def _looks_like_value_column_header(n: Nucleus) -> bool:
    """数值列上方的表头：指标值/金额类，或年期「2023年」（与右对齐金额同列）。"""
    if _looks_like_metric_header(n):
        return True
    t = str(n.text or "").strip().replace(" ", "")
    if not t:
        return False
    return bool(_YEAR_COL_HEADER_RE.match(t))


_COMPACT_VALUE_HEADER_RE = re.compile(
    r"(?:(?:交易)?余额|金额|数额|比例|比重|数量|股数|面值|成本|指标值)"
)


def _looks_like_compact_value_header(n: Nucleus) -> bool:
    """短数值列头（数额/金额）可造槽。

    长「占有关同类/交易发生额」仍不造槽（防空白缝）；但表体全是「-」时
    若短列头也不造槽，整列数额会被收成 cols_too_few。
    """
    t = str(n.text or "").strip().replace(" ", "")
    if not t or len(t) > 6:
        return False
    return bool(_COMPACT_VALUE_HEADER_RE.fullmatch(t))


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


def _is_complementary_change_column_pair(
    rows: Sequence[RowCluster],
    left_col: int,
    right_col: int,
    left_ns: Sequence[Nucleus],
    right_ns: Sequence[Nucleus],
    bands: Sequence[Tuple[float, float]],
) -> bool:
    """增减列因数值右对齐、描述左对齐而过拆时，识别为同一物理列。"""
    orientations = (
        (left_col, left_ns, right_col, right_ns),
        (right_col, right_ns, left_col, left_ns),
    )
    for desc_col, desc_ns, rate_col, rate_ns in orientations:
        desc_items = [n for n in desc_ns if _is_change_desc_text(n.text)]
        rate_items = [n for n in rate_ns if _is_change_rate_amount(n)]
        headers = [n for n in rate_ns if _is_change_col_header_text(n.text)]
        if not desc_items or len(rate_items) < 2 or not headers:
            continue

        # 同槽若还有年份/日期等独立列头，说明它是相邻数据列，不能借增减表头并入。
        independent = _slot_independent_header_labels(rate_ns)
        if any(not _is_change_col_header_text(text) for text in independent):
            continue

        # 两种表现只能交替出现；同行同时有描述和小数即是两个真实字段。
        desc_rows = {
            ri for ri, row in enumerate(rows)
            if any(n.col_id == desc_col and _is_change_desc_text(n.text) for n in row.nuclei)
        }
        rate_rows = {
            ri for ri, row in enumerate(rows)
            if any(n.col_id == rate_col and _is_change_rate_amount(n) for n in row.nuclei)
        }
        if not desc_rows or not rate_rows or desc_rows & rate_rows:
            continue

        # 表头必须横跨两个候选列的交界，证明两种对齐都受同一表头约束。
        desc_band = bands[desc_col]
        rate_band = bands[rate_col]
        header_covers_both = any(
            _h_overlap(h, desc_band[0], desc_band[1]) >= 2.0
            and _h_overlap(h, rate_band[0], rate_band[1]) >= 2.0
            for h in headers
        )
        if header_covers_both:
            return True
    return False


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
        # 各有不同独立表头 → 禁止并槽（正常表每列一头；合并单元格跨列标题另论）
        if _slots_have_distinct_independent_headers(left_ns, right_ns):
            continue
        if _is_complementary_change_column_pair(
            rows, i, j, left_ns, right_ns, bands,
        ):
            union(i, j)
            continue
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


def _same_row_content_gap(
    rows: Sequence[RowCluster],
    left_col: int,
    right_col: int,
) -> Optional[float]:
    """同行两槽任意非空核之间的最大空隙；从不共现则 None。"""
    best: Optional[float] = None
    for r in rows:
        left_ns = [
            n for n in r.nuclei
            if n.col_id == left_col and str(n.text or "").strip()
        ]
        right_ns = [
            n for n in r.nuclei
            if n.col_id == right_col and str(n.text or "").strip()
        ]
        if not left_ns or not right_ns:
            continue
        gap = float(min(n.x0 for n in right_ns) - max(n.x1 for n in left_ns))
        if best is None or gap > best:
            best = gap
    return best


def _slot_band_envelope(
    rows: Sequence[RowCluster],
    col: int,
) -> Optional[Tuple[float, float]]:
    """槽内所有非空核的水平包络（上下整体区域）。"""
    xs0: List[float] = []
    xs1: List[float] = []
    for r in rows:
        for n in r.nuclei:
            if n.col_id != col or not str(n.text or "").strip():
                continue
            xs0.append(float(n.x0))
            xs1.append(float(n.x1))
    if not xs0:
        return None
    return min(xs0), max(xs1)


def _pair_fits_shared_vertical_band(
    rows: Sequence[RowCluster],
    left_col: int,
    right_col: int,
    *,
    min_overlap_ratio: float = 0.45,
    max_union_slack: float = 18.0,
) -> bool:
    """两槽是否同属一个上下列带（小空隙碎片应收进同列）。"""
    bl = _slot_band_envelope(rows, left_col)
    br = _slot_band_envelope(rows, right_col)
    if bl is None or br is None:
        return False
    wl = max(bl[1] - bl[0], 1.0)
    wr = max(br[1] - br[0], 1.0)
    ov = max(0.0, min(bl[1], br[1]) - max(bl[0], br[0]))
    if ov / max(wl, wr) >= min_overlap_ratio:
        return True
    # 嵌套：窄包络落在宽包络内
    nested = (
        (bl[0] >= br[0] - 2.0 and bl[1] <= br[1] + 2.0)
        or (br[0] >= bl[0] - 2.0 and br[1] <= bl[1] + 2.0)
    )
    if nested and ov >= 2.0:
        return True
    union_w = max(bl[1], br[1]) - min(bl[0], br[0])
    return union_w <= max(wl, wr) + max_union_slack


def _slots_peer_cooccur_count(
    rows: Sequence[RowCluster],
    left_col: int,
    right_col: int,
) -> int:
    """同行两槽同时有内容的行数（真并列列信号）。"""
    both = 0
    for r in rows:
        has_l = any(
            n.col_id == left_col and str(n.text or "").strip() for n in r.nuclei
        )
        has_r = any(
            n.col_id == right_col and str(n.text or "").strip() for n in r.nuclei
        )
        if has_l and has_r:
            both += 1
    return both


def _temp_assign_slots_by_seed(
    rows: Sequence[RowCluster],
    centers: Sequence[float],
) -> None:
    """并槽扫描用：科目认左缘，其余认中点。"""
    if not centers:
        return
    for r in rows:
        for n in r.nuclei:
            if _looks_like_label_text_nucleus(n):
                pref = _text_slot_seed(n)
            else:
                pref = _slot_anchor(n)
            n.col_id = min(
                range(len(centers)),
                key=lambda i: abs(centers[i] - pref),
            )


def _slot_is_field_name_header_only(ns: Sequence[Nucleus]) -> bool:
    """仅「指标/项目」等短字段名、无其它正文 → 应并入科目列，不得单独成列。"""
    if not ns or _col_is_serial_like(ns):
        return False
    if any(is_amount_nucleus(n) or _is_value_like_nucleus(n) for n in ns):
        return False
    texts = [str(n.text or "").strip().replace(" ", "") for n in ns if str(n.text or "").strip()]
    if not texts:
        return False
    allowed = {"指标", "项目", "名称", "内容", "事项", "科目", "摘要"}
    # 必须全部是短字段名；混有「零售/公司」等正文则不是纯表头槽
    return all(t in allowed for t in texts)


def _should_collapse_adjacent_slots(
    rows: Sequence[RowCluster],
    left_col: int,
    right_col: int,
    centers: Sequence[float],
    *,
    small_gap: float = 14.0,
    large_gap: float = 40.0,
) -> bool:
    """相邻槽是否并回同列：大空隙保留拆；小空隙看上下列带。"""
    left_ns = [n for r in rows for n in r.nuclei if n.col_id == left_col]
    right_ns = [n for r in rows for n in r.nuclei if n.col_id == right_col]
    if not left_ns or not right_ns:
        return False

    center_dist = abs(float(centers[left_col]) - float(centers[right_col]))

    # 各有不同独立表头 → 真两列，禁止并（每列一头）
    # 例外：两侧皆科目列且从不共现（缩进/折行）时，折行碎片「金额」「应扣除金额」
    # 会被误当成列头；不得挡并（凝结核：缩进≠新列）。
    # 真并列列头（a|b、风险权|预期损）同行共现，仍走下方 peer/gap 分支禁并。
    if _slots_have_distinct_independent_headers(left_ns, right_ns):
        gap0 = _same_row_content_gap(rows, left_col, right_col)
        peer0 = _slots_peer_cooccur_count(rows, left_col, right_col)
        if not (
            gap0 is None
            and peer0 == 0
            and _slot_is_label_like(left_ns)
            and _slot_is_label_like(right_ns)
            and center_dist <= 48.0
        ):
            return False

    # 序号 ↔ 科目/金额：永不并
    left_ser = _col_is_serial_like(left_ns)
    right_ser = _col_is_serial_like(right_ns)
    if left_ser != right_ser and (left_ser or right_ser):
        return False

    # 纯代码列不与金额并
    left_code = sum(1 for n in left_ns if is_code_nucleus(n))
    right_code = sum(1 for n in right_ns if is_code_nucleus(n))
    left_amt = sum(1 for n in left_ns if is_amount_nucleus(n))
    right_amt = sum(1 for n in right_ns if is_amount_nucleus(n))
    if (left_code and right_amt and left_amt == 0) or (
        right_code and left_amt and right_amt == 0
    ):
        return False

    gap = _same_row_content_gap(rows, left_col, right_col)
    peer = _slots_peer_cooccur_count(rows, left_col, right_col)
    center_dist = abs(float(centers[left_col]) - float(centers[right_col]))

    # 「指标」短表头槽 ↔ 科目列：并回（吸收被同行「指标值」挡住时的兜底）
    if (
        _slot_is_field_name_header_only(left_ns)
        and _slot_is_label_like(right_ns)
        and center_dist <= 220.0
    ):
        return True
    if (
        _slot_is_field_name_header_only(right_ns)
        and _slot_is_label_like(left_ns)
        and center_dist <= 220.0
    ):
        return True

    # 从不同行共现：缩进层级 / 过拆隔列（空|有|空|有）
    if gap is None:
        if center_dist > 70.0:
            return False
        if _slot_is_label_like(left_ns) and _slot_is_label_like(right_ns):
            return True
        # 近距过拆：一侧几乎空、或两槽中心很近
        return center_dist <= 48.0

    # 大空隙 → 真两列
    if gap >= large_gap:
        return False

    # 真并列列（多行同时有字）：仅当极近且同属一列带才并（标识码碎片）
    if peer >= 2:
        if gap > small_gap:
            return False
        return _pair_fits_shared_vertical_band(
            rows, left_col, right_col,
            min_overlap_ratio=0.55,
            max_union_slack=12.0,
        )

    # 小空隙：上下列带约束优先
    if gap <= small_gap:
        if _pair_fits_shared_vertical_band(rows, left_col, right_col):
            return True
        if _slot_is_label_like(left_ns) and _slot_is_label_like(right_ns):
            return True
        return center_dist <= 36.0 and _pair_fits_shared_vertical_band(
            rows, left_col, right_col,
            min_overlap_ratio=0.30,
            max_union_slack=22.0,
        )

    # 中等空隙：须列带明显重合
    return _pair_fits_shared_vertical_band(
        rows, left_col, right_col,
        min_overlap_ratio=0.55,
        max_union_slack=14.0,
    )


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
    small_gap: float = 14.0,
) -> List[float]:
    """相邻过拆槽并回同列：大空隙保留拆；小空隙用上下列带约束。

    - 从不共现（缩进 / 隔列空）：近距并回
    - 同行小空隙：看是否落在同一垂直列带（标识码断框等）
    - 同行大空隙：真两列，不并
    - 真并列多行共现：除非极近且列带重合，否则不并
    """
    if len(slot_centers) < 2:
        return list(slot_centers)
    centers = list(slot_centers)
    large_gap = float(min_same_row_gap)
    guard = 0
    while len(centers) >= 2 and guard < 48:
        guard += 1
        _temp_assign_slots_by_seed(rows, centers)
        merged_any = False
        for i in range(len(centers) - 1):
            if not _should_collapse_adjacent_slots(
                rows, i, i + 1, centers,
                small_gap=small_gap,
                large_gap=large_gap,
            ):
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
    _temp_assign_slots_by_seed(rows, centers)

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
        # 纯「指标」短表头、无表体 → 幽灵缝，丢掉（正文已在邻列）
        if _slot_is_field_name_header_only(all_ns) and n_data == 0:
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
        # 纯列头、无表体数据：
        # - 年/分档/列码可留（灰格结构列）
        # - 折行「占有关同类/交易发生额」等指标头造缝 → 丢，并入旁侧金额列
        if n_header >= 1 and n_data == 0:
            if any(
                is_code_nucleus(n)
                or _PERIOD_COL_HEADER_RE.match(str(n.text or "").strip().replace(" ", ""))
                or _YEAR_COL_HEADER_RE.match(str(n.text or "").strip().replace(" ", ""))
                for n in all_ns
            ):
                keep.append(c)
            continue
        # 单点数据且无表头 → 可疑缝列；无证据碎片 → 丢弃
    return keep if len(keep) >= 2 else centers


def _cap_column_slots(
    rows: Sequence[RowCluster],
    slot_centers: Sequence[float],
    *,
    max_cols: int,
) -> List[float]:
    """槽过多时：优先丢掉「无列身份文本」的缝列；有表头/科目/列码的列一律保留。

    硬截断 ``[:max_cols]`` 会砍掉最右侧真列（如 j/k/l）。
    判定「有内容」须覆盖折行碎片（风险暴/露类别），不能只用严格
    ``_is_slot_header_nucleus``——否则左侧类别|区间|余额会被误判空头丢掉。
    """
    centers = [float(c) for c in slot_centers]
    if max_cols < 2 or len(centers) <= max_cols:
        return centers
    if not rows:
        return centers[:max_cols]

    _temp_assign_slots_by_seed(rows, centers)
    headed: List[float] = []
    empty_header: List[float] = []
    for i, c in enumerate(centers):
        ns = [n for r in rows for n in r.nuclei if n.col_id == i]
        if _slot_has_column_identity_text(ns):
            headed.append(c)
        else:
            empty_header.append(c)

    if not headed:
        return centers[:max_cols]
    # 超额：丢掉纯金额/空缝；有列身份文本的列全部保留
    if len(headed) <= max_cols:
        return sorted(headed)
    return sorted(headed)


def _slot_has_column_identity_text(ns: Sequence[Nucleus]) -> bool:
    """列身份文本：表头/列码/序号/科目折行碎片/区间标签（不含纯金额）。"""
    from codes.reconstruct.grid_nucleus.preprocess import is_interval_label

    for n in ns:
        if _is_slot_header_nucleus(n) or is_code_nucleus(n):
            return True
        if _is_left_serial_candidate(n) or is_serial_nucleus(n):
            return True
        t = str(n.text or "").strip()
        if not t:
            continue
        if is_interval_label(t):
            return True
        if is_amount_nucleus(n):
            continue
        if _is_value_like_nucleus(n):
            continue
        # 中文折行碎片（风险暴/露类别）、短标签等
        return True
    return False


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
    # 单独一行的短列头「数额」常不在 body（无金额核）；仍须参与造槽
    for r in rows_for_slots:
        if any(_looks_like_compact_value_header(n) for n in r.nuclei) and r not in body_rows:
            body_rows.append(r)

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
                if _is_cross_column_header_nucleus(n):
                    continue
                # 短列头「数额/金额」可造槽（表体常为「-」占位，无数字锚）
                if _looks_like_compact_value_header(n):
                    amt_anchors.append(_slot_anchor(n))
                    continue
                # 长指标列头（占有关同类/交易发生额）不造槽：
                # 列由下方金额凝结核定；表头后按交叉/右缘吸附，避免左缘插空白缝
                if _looks_like_metric_header(n) or _looks_like_value_column_header(n):
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
        centers = _cap_column_slots(rows_for_slots, centers, max_cols=max_cols)
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
        centers = _cap_column_slots(rows_for_slots, merged, max_cols=max_cols)

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
        centers = _cap_column_slots(rows_for_slots, centers, max_cols=max_cols)
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

    # —— 1) 初筛：一律中点落槽；科目左缘只用于造槽（infer），避免宽表头
    # 左缘偏左把整段核塞进左列、撑破临时列带，导致步骤 3 双边分失效 ——
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
    # —— 4a) 有 a/b/c… 列码时：右对齐数值/短杠归入「列码中心≤右缘」的最近列码列 ——
    # 避免短杠中心飘到邻列，再被垂直 snap 把整列金额吸走（page_023 列 b 变空）
    _snap_values_by_letter_code_right_align(rows, n_cols)
    # —— 4b) 数值列表头按右缘贴齐下方金额列（财务并表口径下 ↔ 左金额）——
    _snap_headers_to_amount_cols_by_right_edge(rows, n_cols)
    # —— 5) 上下行同列左右范围一致（表头合并除外；纯表头行也作参照）——
    _snap_vertical_same_column_range(rows, n_cols)
    # —— 5b) 折行续文「的…」强制跟上一行左缘接近的表头同列 ——
    _snap_wrap_continuation_to_header_above(rows, n_cols)
    # —— 6) 序号与科目文本强制分列 ——
    _force_serial_away_from_labels(rows, centers)
    # —— 6b) 中文折行续文不得留在序号列 ——
    _force_labels_out_of_serial_cols(rows, centers, serial_cols)
    # —— 7) 增减折行表头与「下降…百分点」同伴同列 ——
    _snap_change_header_desc_peers(rows, n_cols)
    # —— 8) 凝结核铁律：左右边界实质相同 → 必须同列 ——
    _lock_same_bound_nuclei(rows)
    # —— 9) 左侧短分组标题的字框仍完整落在序号带时，恢复序号列 ——
    _restore_section_leads_to_serial_cols(rows, centers, serial_cols)


def _restore_section_leads_to_serial_cols(
    rows: Sequence[RowCluster],
    slot_centers: Sequence[float],
    serial_cols: set,
) -> None:
    """同界/垂直吸附不得把几何上属于序号带的分组标题拖到科目列。"""
    if not serial_cols or len(slot_centers) < 2:
        return
    centers = [float(x) for x in slot_centers]
    for row in rows:
        for nucleus in row.nuclei:
            if str(nucleus.text or "").strip() not in _SECTION_LEAD_KEEP_IN_SERIAL:
                continue
            target = min(serial_cols, key=lambda c: abs(float(nucleus.cx) - centers[c]))
            right_centers = [centers[c] for c in range(len(centers)) if c > target]
            if not right_centers:
                continue
            boundary = (centers[target] + min(right_centers)) / 2.0
            # 右缘只允许字形毛刺越界；真正伸入科目列的文本不强拉。
            if (
                abs(float(nucleus.cx) - centers[target]) <= 24.0
                and float(nucleus.x1) <= boundary + 6.0
            ):
                nucleus.col_id = int(target)


def absorb_header_only_slots_into_body(
    rows: List[RowCluster],
    slot_centers: Sequence[float],
) -> List[Dict[str, Any]]:
    """把无表体证据的跨列表头槽吸附到最近真实数据列。

    列槽由表体决定。完整日期、跨列标题或指标头若独占一槽，而该槽在
    表体中全空，它只是表头左缘/中心造成的缝，不应成为输出列。列码、
    期限分档等即使当前页表体为空也有独立列身份，必须保留。
    """
    centers = [float(x) for x in slot_centers]
    n_cols = len(centers)
    if n_cols < 2 or not rows:
        return []

    members: Dict[int, List[Nucleus]] = {
        c: [n for r in rows for n in r.nuclei if int(n.col_id) == c]
        for c in range(n_cols)
    }
    body_cols = {
        c for c, ns in members.items()
        if any(_is_body_data_nucleus(n) for n in ns)
    }
    if not body_cols:
        return []

    def _has_independent_empty_identity(ns: Sequence[Nucleus]) -> bool:
        for n in ns:
            t = str(n.text or "").strip().replace(" ", "")
            if is_code_nucleus(n) or _SERIAL_HEADER_RE.match(t):
                return True
            if _PERIOD_COL_HEADER_RE.match(t):
                return True
        return False

    def _is_absorbable_header(n: Nucleus) -> bool:
        return bool(
            _is_cross_column_header_nucleus(n)
            or _is_date_header_nucleus(n)
            or _looks_like_metric_header(n)
            or _looks_like_value_column_header(n)
        )

    actions: List[Dict[str, Any]] = []
    for c in range(n_cols):
        ns = members.get(c) or []
        if c in body_cols or not ns or _has_independent_empty_identity(ns):
            continue
        if not all(_is_absorbable_header(n) for n in ns):
            continue

        for n in ns:
            # 指标头优先按右缘贴金额列；跨列日期按中心贴最近表体列，
            # 后续 span_mark 再根据原始核宽标注真实 colspan。
            if _looks_like_metric_header(n):
                target = min(
                    body_cols,
                    key=lambda k: (
                        abs(float(n.x1) - centers[k]),
                        abs(float(n.cx) - centers[k]),
                    ),
                )
            else:
                target = min(body_cols, key=lambda k: abs(float(n.cx) - centers[k]))
            n.col_id = int(target)
            actions.append({
                "from": c,
                "to": int(target),
                "text": str(n.text or "")[:40],
                "reason": "header_only_without_body",
            })
    return actions


def _metric_header_amount_same_column(
    a: Nucleus,
    b: Nucleus,
    *,
    center_slack: float = 6.0,
) -> bool:
    """指标列头与金额核是否同列带（水平交叉，或金额中心落在表头核内）。

    例：占有关同类 / 比例(%) 与 6.38 字框相交 → 同列。
    不用过大右扩，以免把邻年金额（5,000）误锁进本年比例列。
    """
    if _looks_like_metric_header(a) and is_amount_nucleus(b):
        hdr, amt = a, b
    elif _looks_like_metric_header(b) and is_amount_nucleus(a):
        hdr, amt = b, a
    else:
        return False
    ov = _h_overlap(hdr, float(amt.x0), float(amt.x1))
    if ov > 0:
        return True
    # 金额中心落在表头核宽内（含少量容差）
    return (
        float(hdr.x0) - center_slack
        <= float(amt.cx)
        <= float(hdr.x1) + center_slack
    )


def _lock_same_bound_nuclei(
    rows: List[RowCluster],
    *,
    eps: float = 2.5,
    nest_slack: float = 4.0,
    min_overlap_ratio: float = 0.72,
) -> None:
    """左右边界实质相同（或高度嵌套/重叠）的核必须同列。

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

    def _same_bound_or_nested(ai: Nucleus, bj: Nucleus) -> bool:
        if (
            abs(float(ai.x0) - float(bj.x0)) <= eps
            and abs(float(ai.x1) - float(bj.x1)) <= eps
        ):
            return True
        # 嵌套：窄核落在宽核内（年头 ↔ 指标值 宽窄不一）
        nested = (
            (
                float(ai.x0) >= float(bj.x0) - nest_slack
                and float(ai.x1) <= float(bj.x1) + nest_slack
            )
            or (
                float(bj.x0) >= float(ai.x0) - nest_slack
                and float(bj.x1) <= float(ai.x1) + nest_slack
            )
        )
        if nested:
            ov = _h_overlap(ai, float(bj.x0), float(bj.x1))
            w = max(float(ai.width), float(bj.width), 1.0)
            if ov / w >= min_overlap_ratio:
                return True
        # 指标列头 ↔ 右对齐金额：核带交叉或金额落在表头列廊内 → 同列
        # （占有关同类 / 比例(%) 与 6.38；不得在中间插空白列）
        return _metric_header_amount_same_column(ai, bj)

    for i in range(len(nuclei)):
        ai = nuclei[i]
        for j in range(i + 1, len(nuclei)):
            bj = nuclei[j]
            ai_section = str(ai.text or "").strip() in _SECTION_LEAD_KEEP_IN_SERIAL
            bj_section = str(bj.text or "").strip() in _SECTION_LEAD_KEEP_IN_SERIAL
            # CC2 短分组标题的几何带属于序号列；不得与普通科目核组队后
            # 被科目列多数票拖走。「资产管理计划」等不在精确集合内。
            if ai_section != bj_section:
                continue
            # 真两列金额：中心分叉且右缘不同 → 不锁
            if is_amount_nucleus(ai) and is_amount_nucleus(bj):
                if abs(float(ai.cx) - float(bj.cx)) > 18.0 and abs(
                    float(ai.x1) - float(bj.x1)
                ) > 10.0:
                    continue
            if _same_bound_or_nested(ai, bj):
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
        # 例外：组内全是科目文本时，忌选金额主导列（SEC1：其中：… 被锁进 a 列）
        if not amt_cols:
            def _label_col_score(c: int) -> Tuple[int, int, int]:
                col_ns = [x for r in rows for x in r.nuclei if x.col_id == c]
                n_amt = sum(
                    1
                    for x in col_ns
                    if is_amount_nucleus(x) or _is_value_like_nucleus(x)
                )
                n_lab = sum(1 for x in col_ns if _looks_like_label_text_nucleus(x))
                # 科目多、金额少优先；再比票数；再偏左
                return (n_lab - n_amt, votes[c], -c)

            best = max(votes.keys(), key=_label_col_score)
        else:
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
                # 科目用左缘贴槽，避免 cx 偏右选进金额列
                seed = _text_slot_seed(lab)
                # 排除金额主导槽：宁可回序号右侧最近科目槽
                def _cand_ok(i: int) -> bool:
                    col_ns = [x for rr in rows for x in rr.nuclei if x.col_id == i]
                    if not col_ns:
                        return True
                    n_amt = sum(1 for x in col_ns if is_amount_nucleus(x))
                    n_lab = sum(1 for x in col_ns if _looks_like_label_text_nucleus(x))
                    return not (n_amt >= 2 and n_amt > n_lab)

                good = [i for i in cands if _cand_ok(i)]
                use = good or cands
                lab.col_id = min(use, key=lambda i: abs(slot_centers[i] - seed))


def _force_labels_out_of_serial_cols(
    rows: List[RowCluster],
    slot_centers: List[float],
    serial_cols: set,
) -> None:
    """折行续文等中文核若仍落在序号列，推到右侧最近非序号槽。

    例外：CC2「资产」「负债」等分组标题左缘本在序号带，须留在序号列
    （否则会变成空|资产，与 PDF 左对齐序号列不符）。
    """
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
            t = str(n.text or "").strip()
            if t in _SECTION_LEAD_KEEP_IN_SERIAL:
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
    # 文本/科目列：左缘接近或落在上行公共包络内（缩进≠新列）
    dx0 = abs(float(cur.x0) - float(prev.x0))
    if dx0 <= 36.0 and ov >= 4.0:
        return 80.0 - min(dx0, 28.0) + ov
    # 缩进更深但仍在上行列带内
    if (
        float(cur.x0) >= float(prev.x0) - 4.0
        and float(cur.x0) <= float(prev.x1) - 8.0
        and ov >= 6.0
    ):
        return 55.0 + ov
    return ov


def _letter_code_col_centers(rows: List[RowCluster]) -> Dict[int, float]:
    """单字母列码 a/b/c… 所在列 → 列码中心 x。"""
    by_col: Dict[int, List[float]] = {}
    for r in rows:
        for n in r.nuclei:
            if n.col_id < 0 or not is_code_nucleus(n):
                continue
            t = str(n.text or "").strip().replace(" ", "")
            if len(t) == 1 and t.isascii() and t.isalpha():
                by_col.setdefault(int(n.col_id), []).append(float(n.cx))
    return {c: float(median(xs)) for c, xs in by_col.items()}


def _best_letter_code_col_for_value(
    n: Nucleus,
    letter_cx_by_col: Dict[int, float],
    *,
    x1_slack: float = 6.0,
) -> Optional[int]:
    """右对齐数值归列：取「列码中心 ≤ 数值右缘」中最靠右的列码列。

    短杠「-」中心常贴列右缘、几何上更靠近邻列中心；列码约束可把它拉回本列。
    """
    if not letter_cx_by_col:
        return None
    x1 = float(n.x1)
    cands = [
        (c, cx) for c, cx in letter_cx_by_col.items() if cx <= x1 + x1_slack
    ]
    if not cands:
        return None
    return max(cands, key=lambda it: it[1])[0]


def _snap_values_by_letter_code_right_align(
    rows: List[RowCluster],
    n_cols: int,
    *,
    min_letter_cols: int = 2,
) -> None:
    """有 ≥2 个单字母列码时，把金额/短杠按右缘贴到对应列码列。"""
    if n_cols < 2:
        return
    letter_cx = _letter_code_col_centers(rows)
    if len(letter_cx) < min_letter_cols:
        return
    for r in rows:
        for n in r.nuclei:
            if not _is_value_like_nucleus(n):
                continue
            if not (0 <= n.col_id < n_cols):
                continue
            pref = _best_letter_code_col_for_value(n, letter_cx)
            if pref is None or pref == n.col_id:
                continue
            # 同列已有另一数值且双方都是宽金额 → 勿抢真两列
            clash = [
                m
                for m in r.nuclei
                if m is not n
                and m.col_id == pref
                and _is_value_like_nucleus(m)
                and float(m.width) >= 18.0
                and float(n.width) >= 18.0
            ]
            if clash:
                continue
            n.col_id = pref


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
    letter_cx = _letter_code_col_centers(rows)
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
                # 左侧序号不得被上行科目/粘连「6 公司类合计」拽进科目列
                if _is_left_serial_candidate(n) and tgt != n.col_id:
                    continue
                # 列码右对齐归属列：勿被邻列短杠右缘贴齐吸走
                if letter_cx and _is_value_like_nucleus(n):
                    pref = _best_letter_code_col_for_value(n, letter_cx)
                    if pref is not None and pref == n.col_id and tgt != pref:
                        continue
                n.col_id = tgt

        # 有数值的行作为下一行的垂直参照。
        # 纯表头不写入 prev_anchors：小节标题会把下行序号拽进科目列；
        # 折行续文改由 _snap_wrap_continuation_to_header_above 处理。
        vals = [n for n in usable if _is_value_like_nucleus(n)]
        if len(vals) >= 1:
            prev_anchors = usable


def _snap_headers_to_amount_cols_by_right_edge(
    rows: List[RowCluster],
    n_cols: int,
    *,
    x1_tol: float = 18.0,
    min_amt_peers: int = 2,
) -> None:
    """数值列上方中文表头：右缘贴齐该列金额（财务并表口径下 ↔ 左金额列）。

    避免表头因左缘偏左落入幽灵空列，与下方金额错位。
    """
    if n_cols < 2 or len(rows) < 2:
        return
    col_x1s: List[List[float]] = [[] for _ in range(n_cols)]
    for r in rows:
        for n in r.nuclei:
            if 0 <= n.col_id < n_cols and is_amount_nucleus(n):
                col_x1s[n.col_id].append(float(n.x1))
    medians: List[Optional[float]] = []
    for xs in col_x1s:
        if len(xs) >= min_amt_peers:
            medians.append(float(median(xs)))
        else:
            medians.append(None)
    strong = [i for i, m in enumerate(medians) if m is not None]
    if not strong:
        return

    for r in rows:
        # 跳过已有金额的主体行：只校正表头/折行碎片
        if any(is_amount_nucleus(n) for n in r.nuclei):
            continue
        for n in r.nuclei:
            if not _looks_like_label_text_nucleus(n):
                continue
            if is_amount_nucleus(n) or is_code_nucleus(n) or is_serial_nucleus(n):
                continue
            cur = int(n.col_id)
            best = cur
            best_dist = abs(float(n.x1) - float(medians[cur])) if (
                0 <= cur < n_cols and medians[cur] is not None
            ) else 1e9
            for i in strong:
                d = abs(float(n.x1) - float(medians[i]))
                if d <= x1_tol and d + 1e-6 < best_dist:
                    best_dist = d
                    best = i
            if best != cur and best_dist <= x1_tol:
                # 目标列若已有另一表头且双方都是宽表头，允许并排（真两列）
                peers = [
                    m for m in r.nuclei
                    if m is not n
                    and m.col_id == best
                    and _looks_like_label_text_nucleus(m)
                ]
                if peers and abs(float(n.x0) - float(peers[0].x0)) > 40.0:
                    # 另一宽表头已占该列且左缘相距大 → 仍可落入（并列表头）
                    pass
                n.col_id = best


def _snap_wrap_continuation_to_header_above(
    rows: List[RowCluster],
    n_cols: int,
    *,
    max_dx0: float = 22.0,
) -> None:
    """折行续文（的资产负债表）继承上一行左缘接近的表头列。

    凝结核：同左缘同列；不以中点漂到右邻空列。
    """
    if n_cols < 2 or len(rows) < 2:
        return
    for i in range(1, len(rows)):
        prev = rows[i - 1]
        cur = rows[i]
        prev_hdrs = [
            p for p in prev.nuclei
            if 0 <= p.col_id < n_cols
            and _looks_like_label_text_nucleus(p)
            and not is_amount_nucleus(p)
        ]
        if not prev_hdrs:
            continue
        for n in cur.nuclei:
            t = str(n.text or "").strip()
            if not t:
                continue
            # 折行续写口吻，或与上行表头同列宽窄嵌套的短续文
            if not (
                t.startswith(("的", "及", "与", "和", "或", "等", "）", ")"))
                or (len(t) <= 12 and "资产负债表" in t)
            ):
                continue
            if is_amount_nucleus(n):
                continue
            best: Optional[Nucleus] = None
            best_sc = -1e9
            for p in prev_hdrs:
                dx0 = abs(float(n.x0) - float(p.x0))
                if dx0 > max_dx0:
                    continue
                ov = _h_overlap(n, float(p.x0), float(p.x1))
                sc = 80.0 - dx0 + ov
                if sc > best_sc:
                    best_sc = sc
                    best = p
            if best is None:
                continue
            tgt = int(best.col_id)
            # 同列已有另一续文/表头且左缘相距大 → 可能是并列表头，勿抢列
            clash = [
                m for m in cur.nuclei
                if m is not n
                and m.col_id == tgt
                and _looks_like_label_text_nucleus(m)
                and abs(float(m.x0) - float(n.x0)) > 36.0
            ]
            if clash:
                continue
            n.col_id = tgt


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


def compact_unused_column_ids(
    rows: List[RowCluster],
    n_cols: int,
) -> int:
    """吸附后去掉全程无核的幽灵列，并把 col_id 压成连续 0..k-1。

    典型场景：表头左缘造出空槽，表头被右缘贴到金额列后该槽变空；
    若不压缩，prune 会把旧分割线拼成 1pt 缝。
    """
    if n_cols < 2 or not rows:
        return n_cols
    used = sorted({
        int(n.col_id)
        for r in rows
        for n in r.nuclei
        if 0 <= int(n.col_id) < n_cols
    })
    if len(used) < 2 or len(used) == n_cols:
        return n_cols
    remap = {old: new for new, old in enumerate(used)}
    for r in rows:
        for n in r.nuclei:
            cid = int(n.col_id)
            if cid in remap:
                n.col_id = remap[cid]
            elif cid >= n_cols:
                n.col_id = max(0, len(used) - 1)
    return len(used)


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
