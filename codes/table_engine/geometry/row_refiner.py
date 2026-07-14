# -*- coding: utf-8 -*-
"""
基于 text_item 坐标的 Y 聚类行精修。

策略：先锁定数据行网格（body），表头保持 Y 聚类原行；再对 body 做标签折行修补。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from codes.table_engine.geometry.column_anchors import is_pd_range_cell_text, is_report_period_cell
from codes.table_engine.geometry.numeric import is_month_day_cell, is_numeric_data_cell
from codes.table_engine.geometry.row_dict import build_row_dict as _build_row_dict

_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.IGNORECASE)
_ROW_NUM_LABEL_RE = re.compile(r"^(\d+[a-z]?)\s+(.+)$", re.IGNORECASE)
_HEADER_FRAG_KW = ("准的", "足STC", "其中，满", "其中,满", "传统型", "合成型", "小计")
_ENTITY_SCOPE_LABELS = frozenset({"本集团", "本行"})
_YEAR_ONLY_RE = re.compile(r"^(?:19|20)\d{2}年?$")
_STAGE_LABEL_RE = re.compile(r"^阶段[一二三1-3]$")
_ECL_PERIOD_TOKENS = frozenset({"12个月", "整个存续期"})
_ECL_LOSS_LABEL = "预期信用损失"
_X_BAND_TOL = 15.0
_Y_MERGE_MAX_GAP = 22.0
_LABEL_CONT_MAX_LEN = 8
_FINANCIAL_WRAP_FRAGMENT_RE = re.compile(
    r"^(?:非经常性|损益|净利润|净额|后的净利润|损益净利润|损益后的净利润)"
)
_LABEL_BELOW_VALUE_TOL = 6.0
_CR6_CATEGORY_X_MAX = 72.0
_CR6_PD_X_MIN = 72.0
_SECTION_BAND_ROW_TEXTS = frozenset({
    "现金流出",
    "现金流入",
    "调整后数值",
})

# 短地址续行模式：中文地址中折行后的碎片文本
_ADDR_CONT_SHORT_RE = re.compile(
    r"(?:^\d+[、，,]?\d*号"
    r"|^\d+号及第\d+层"
    r"|^\d+\s*层\s*\d+.*号"
    r"|^[）)\"」』]\s*$"
    r"|^\d{1,3}[、，,]\d{1,3}[、，,]?\d*号$"
    r")"
)
_ADDR_LEAD_MARKERS = frozenset({"栋", "幢", "座", "单元", "楼", "层", "附", "厦", "尚"})


def _is_short_address_continuation(text: str) -> bool:
    """短地址续行碎片（如"尚""、"栋 1 层 1 号"、"26、28、30...号"）。

    规则必须足够保守，避免误伤普通标签文本（如"编号""层级"）。
    """
    t = str(text or "").strip()
    if not t or len(t) < 2:
        return False

    # 正则模式（最精确，优先匹配）
    if _ADDR_CONT_SHORT_RE.search(t):
        return True

    # 以地址标记开头 + 含数字（如"栋 1 层 1 号"）
    # 必须含数字，排除仅中文标签（如"尚"单独不匹配）
    if t[0] in _ADDR_LEAD_MARKERS and re.search(r"\d", t) and len(t) <= 12:
        return True

    # 含地址编号模式（数字分隔符 + 地址后缀）且较短
    if re.search(r"[\d、，,]", t) and re.search(r"[号楼幢层厦]", t) and len(t) <= 20:
        return True

    return False


def _is_address_like_fragment(text: str) -> bool:
    """判断文本是否为地址续行碎片（用于过宽 Y 聚类拆分判断）。

    比 _is_short_address_continuation 更保守：只匹配明确的中文地址模式，
    排除可能误匹配的普通标签文本。
    """
    t = str(text or "").strip()
    if not t or len(t) < 2:
        return False

    # 正则模式（最精确）
    if _ADDR_CONT_SHORT_RE.search(t):
        return True

    # 以地址标志开头 + 含数字（如"栋 1 层 1 号"）
    if t[0] in _ADDR_LEAD_MARKERS and re.search(r"\d", t) and len(t) <= 12:
        return True

    # 含数字分隔符 + 地址后缀 + 较短
    if re.search(r"[\d、，,]", t) and re.search(r"[号楼幢层厦]", t) and len(t) <= 20:
        return True

    # 以数字+号开头（如"15 号"、"26、28 号"）
    if re.match(r"^\d+[、，,]?\d*\s*号", t):
        return True

    return False

_ROW_PHASE_HEADER = "header"
_ROW_PHASE_BODY = "body"


@dataclass
class _LayoutAnchors:
    row_num_x_max: float = 92.0
    label_x_min: float = 95.0
    label_x_max: float = 215.0
    value_x_min: float = 220.0


# 对外暴露，供 column_profile / cell_builder 共用
LayoutAnchors = _LayoutAnchors


def estimate_layout_anchors(rows: List[dict]) -> LayoutAnchors:
    """从聚类行估计序号/标签/数值列 x 带（数据区优先）。"""
    layout = _estimate_layout(rows)
    body_start = _find_body_start_index(rows, layout)
    return _refine_layout_from_body(rows, body_start)


def _xc(it: dict) -> float:
    return (float(it.get("x0", 0)) + float(it.get("x1", 0))) / 2


def _ym(it: dict) -> float:
    if "y_mid" in it:
        return float(it["y_mid"])
    return (float(it.get("y0", 0)) + float(it.get("y1", 0))) / 2


def _row_span(row: dict) -> Tuple[float, float]:
    items = row.get("items") or []
    if not items:
        return (0.0, 0.0)
    return (
        min(float(it.get("y0", 0)) for it in items),
        max(float(it.get("y1", 0)) for it in items),
    )


def _value_items(row: dict, layout: _LayoutAnchors) -> List[dict]:
    return [it for it in row.get("items") or [] if _is_value_item(it, layout)]


def _label_items(row: dict, layout: _LayoutAnchors) -> List[dict]:
    items = row.get("items") or []
    out: List[dict] = []
    for it in items:
        if _is_label_col_item(it, layout) or _is_row_label_item(it, layout):
            out.append(it)
    return out


def _items_span(items: List[dict]) -> Tuple[float, float]:
    if not items:
        return (0.0, 0.0)
    return (
        min(float(it.get("y0", 0)) for it in items),
        max(float(it.get("y1", 0)) for it in items),
    )


def _value_row_span(row: dict, layout: _LayoutAnchors) -> Tuple[float, float]:
    """数据行垂直范围：优先由值列数值锚定下沿。"""
    vals = _value_items(row, layout)
    if vals:
        return _items_span(vals)
    return _row_span(row)


def _label_row_span(row: dict, layout: _LayoutAnchors) -> Tuple[float, float]:
    labels = _label_items(row, layout)
    if labels:
        return _items_span(labels)
    return _row_span(row)


def _y_gap(row_a: dict, row_b: dict) -> float:
    _, y1_a = _row_span(row_a)
    y0_b, _ = _row_span(row_b)
    return y0_b - y1_a


def _y_gap_value_anchored(upper: dict, lower: dict, layout: _LayoutAnchors) -> float:
    """以上方行值列下沿 ↔ 下方行顶沿计算间距。"""
    _, y1_upper = _value_row_span(upper, layout)
    if _row_is_label_only(lower, layout):
        y0_lower, _ = _label_row_span(lower, layout)
    else:
        y0_lower, _ = _row_span(lower)
    return y0_lower - y1_upper


def _label_below_overlaps_value_band(
    data_row: dict,
    label_row: dict,
    layout: _LayoutAnchors,
    *,
    tol: float = _LABEL_BELOW_VALUE_TOL,
) -> bool:
    """下方仅标签行与数据行值列带是否仍属同一逻辑行（允许标签折行略低于数值）。"""
    val_items = _value_items(data_row, layout)
    if not val_items:
        return True
    _, y1_val = _items_span(val_items)
    y0_lbl, _ = _label_row_span(label_row, layout)
    return y0_lbl <= y1_val + tol


def _items_share_x_band(it_a: dict, it_b: dict, tol: float = _X_BAND_TOL) -> bool:
    x0_a = float(it_a.get("x0", 0))
    x0_b = float(it_b.get("x0", 0))
    ta = str(it_a.get("text", "")).strip()
    tb = str(it_b.get("text", "")).strip()
    dx = abs(x0_a - x0_b)
    if dx <= tol:
        if (_ROW_NUMBER_RE.match(ta) and re.search(r"[\u4e00-\u9fff]", tb)) or (
            _ROW_NUMBER_RE.match(tb) and re.search(r"[\u4e00-\u9fff]", ta)
        ):
            return dx <= 4.0
        return True
    return abs(_xc(it_a) - _xc(it_b)) <= tol


def _tag_cluster_rows(rows: List[dict]) -> List[dict]:
    """Y 聚类行打上稳定 cluster_row_id（合并时保留较小 id）。"""
    tagged: List[dict] = []
    for i, row in enumerate(rows):
        out = dict(row)
        out["cluster_row_id"] = row.get("cluster_row_id", i)
        out["row_phase"] = row.get("row_phase", "")
        tagged.append(out)
    return tagged


def _merge_rows_preserve_id(row_a: dict, row_b: dict) -> dict:
    merged = _merge_items_vertical(row_a, row_b)
    merged["cluster_row_id"] = min(
        int(row_a.get("cluster_row_id", 0)),
        int(row_b.get("cluster_row_id", 0)),
    )
    return merged


def _row_plain_texts(row: dict) -> List[str]:
    return [
        str(it.get("text", "")).strip()
        for it in row.get("items") or []
        if str(it.get("text", "")).strip()
    ]


def _is_ecl_stage_label_row(row: dict) -> bool:
    texts = _row_plain_texts(row)
    return sum(1 for t in texts if _STAGE_LABEL_RE.match(t)) >= 2


def _is_ecl_period_kind_row(row: dict) -> bool:
    texts = _row_plain_texts(row)
    return any(t == "12个月" for t in texts) and any(t in _ECL_PERIOD_TOKENS for t in texts)


def _is_ecl_loss_kind_row(row: dict) -> bool:
    texts = _row_plain_texts(row)
    return sum(1 for t in texts if _ECL_LOSS_LABEL in t) >= 2


def _is_ecl_stage_header_hierarchy_pair(row_a: dict, row_b: dict) -> bool:
    """阶段 / 12个月·存续期 / 预期信用损失 为三层表头，禁止纵并。"""
    checks = (
        (_is_ecl_stage_label_row, _is_ecl_period_kind_row),
        (_is_ecl_stage_label_row, _is_ecl_loss_kind_row),
        (_is_ecl_period_kind_row, _is_ecl_loss_kind_row),
    )
    for fn_a, fn_b in checks:
        if (fn_a(row_a) and fn_b(row_b)) or (fn_a(row_b) and fn_b(row_a)):
            return True
    return False


def _is_row_num_item(it: dict, layout: _LayoutAnchors) -> bool:
    t = str(it.get("text", "")).strip()
    return float(it.get("x0", 0)) <= layout.row_num_x_max and bool(
        _ROW_NUMBER_RE.match(t)
    )


def _is_value_item(it: dict, layout: _LayoutAnchors) -> bool:
    t = str(it.get("text", "")).strip()
    if not t:
        return False
    if float(it.get("x0", 0)) < layout.value_x_min - 15:
        return False
    if t in ("-", "—", "–"):
        return True
    if re.search(r"(?:19|20)\d{2}\s*年", t):
        return False
    from codes.table_engine.geometry.numeric import is_month_day_cell, is_year_cell
    if is_year_cell(t) or is_month_day_cell(t):
        return False
    return is_numeric_data_cell(t)


def _is_label_col_item(it: dict, layout: _LayoutAnchors) -> bool:
    x0 = float(it.get("x0", 0))
    t = str(it.get("text", "")).strip()
    if not (layout.label_x_min <= x0 <= layout.label_x_max):
        return False
    if not t or _ROW_NUMBER_RE.match(t):
        return False
    if is_numeric_data_cell(t) or t in ("-", "—", "–"):
        return False
    return True


def _row_number_text(row: dict, layout: _LayoutAnchors) -> Optional[str]:
    for it in row.get("items") or []:
        if _is_row_num_item(it, layout):
            return str(it.get("text", "")).strip()
    return None


def _is_row_label_item(it: dict, layout: _LayoutAnchors) -> bool:
    """表左侧行标签（期间/平行向上/规模），x 在行号列一带。"""
    t = str(it.get("text", "")).strip()
    if not t or is_numeric_data_cell(t):
        return False
    x0 = float(it.get("x0", 0))
    if x0 > layout.row_num_x_max + 30:
        return False
    if _ROW_NUMBER_RE.match(t):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", t))


def _row_has_body_signature(row: dict, layout: _LayoutAnchors) -> bool:
    """数据行：行号+数额；或左标签+指标值；或二级指标列+指标值。"""
    items = row.get("items") or []
    has_num = any(_is_row_num_item(it, layout) for it in items)
    val_count = sum(1 for it in items if _is_value_item(it, layout))
    has_label = any(_is_label_col_item(it, layout) for it in items)
    has_left = any(_is_row_label_item(it, layout) for it in items)
    if has_num and (val_count >= 1 or has_label or has_left):
        return True
    if val_count >= 1 and (has_label or has_left):
        return True
    return False


def _row_is_locked_data_row(row: dict, layout: _LayoutAnchors) -> bool:
    """已锁定的数据行：含指标值，不可与另一数据行纵并。"""
    if not _row_has_body_signature(row, layout):
        return False
    return any(_is_value_item(it, layout) for it in row.get("items") or [])


def _both_locked_data_rows(row_a: dict, row_b: dict, layout: _LayoutAnchors) -> bool:
    return _row_is_locked_data_row(row_a, layout) and _row_is_locked_data_row(row_b, layout)


def _row_is_label_only(row: dict, layout: _LayoutAnchors) -> bool:
    items = row.get("items") or []
    if not items:
        return False
    if any(_is_row_num_item(it, layout) for it in items):
        return False
    if any(_is_value_item(it, layout) for it in items):
        return False
    return all(
        _is_label_col_item(it, layout) or _is_row_label_item(it, layout)
        for it in items
    )


def _row_is_year_only_band(row: dict) -> bool:
    items = row.get("items") or []
    texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
    return bool(texts) and all(_YEAR_ONLY_RE.match(t) for t in texts)


def _row_is_period_band_row(row: dict) -> bool:
    """数据列上的报告期带（如 2024年 | 2023年）。"""
    items = row.get("items") or []
    texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
    return bool(texts) and all(is_report_period_cell(t) for t in texts)


def _row_is_month_day_band_row(row: dict) -> bool:
    """下层日期列标（如 12月31日 | 12月31日）。"""
    items = row.get("items") or []
    texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
    return bool(texts) and all(is_month_day_cell(t) for t in texts)


def _row_is_entity_scope_header_row(row: dict) -> bool:
    """双主体列标（本集团 / 本行）。"""
    items = row.get("items") or []
    texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
    if len(texts) < 2:
        return False
    return all(t in _ENTITY_SCOPE_LABELS for t in texts)


def _row_is_identical_subheader_band(row: dict) -> bool:
    """下层表头：多列相同短标签（如两列均为「占同类交易」）。"""
    items = row.get("items") or []
    texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
    if len(texts) < 2 or len(set(texts)) != 1:
        return False
    t = texts[0]
    return len(t) <= 12 and not is_report_period_cell(t)


def _row_is_rate_metric_banner(row: dict) -> bool:
    joined = "".join(str(it.get("text", "")).strip() for it in row.get("items") or [])
    return any(k in joined for k in ("利息收入", "平均收益率", "利息支出", "平均成本率"))


def _row_is_preserved_intra_table_label(row: dict, layout: _LayoutAnchors) -> bool:
    """表内小节标题（仅标签、无数值）须保留独立行，不可并入下一数据行。"""
    if not _row_is_label_only(row, layout):
        return False
    if _row_is_rate_metric_banner(row):
        return True
    items = sorted(row.get("items") or [], key=lambda it: float(it.get("x0", 0)))
    texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
    if not texts:
        return False
    joined = "".join(texts)
    from codes.table_engine.split.boundary_overlap import row_is_label_suffix_tail

    if row_is_label_suffix_tail([joined]):
        return False
    if _FINANCIAL_WRAP_FRAGMENT_RE.search(joined):
        return False
    from codes.table_engine.split.row_classify import row_is_intra_table_label_row

    row_cells = list(texts)
    while len(row_cells) < 4:
        row_cells.append("")
    return row_is_intra_table_label_row(row_cells)


def _row_is_metric_column_header_row(row: dict) -> bool:
    texts = [str(it.get("text", "")).strip() for it in row.get("items") or []]
    hits = sum(1 for t in texts if any(m in t for m in ("平均余额", "支出", "成本率")))
    return hits >= 2


def _row_is_header_fragment(row: dict, layout: _LayoutAnchors) -> bool:
    items = row.get("items") or []
    if len(items) < 2:
        return False
    if any(_is_row_num_item(it, layout) for it in items):
        return False
    if any(_is_value_item(it, layout) for it in items):
        return False
    texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
    if not texts:
        return False
    if len(set(texts)) == 1 and len(texts[0]) <= 8:
        return True
    if all(any(k in t for k in _HEADER_FRAG_KW) for t in texts):
        return True
    return False


def _label_items_share_x_band(
    row_a: dict,
    row_b: dict,
    layout: _LayoutAnchors,
    *,
    tol: float = 14.0,
) -> bool:
    """两行标签 item 的 x0 落在同一标签列带。"""
    a = [it for it in row_a.get("items") or [] if _is_label_col_item(it, layout)]
    b = [it for it in row_b.get("items") or [] if _is_label_col_item(it, layout)]
    if not a or not b:
        return False
    for ia in a:
        xa = float(ia.get("x0", 0))
        for ib in b:
            if abs(xa - float(ib.get("x0", 0))) <= tol:
                return True
    return False


def _row_is_continuation_fragment(row: dict, layout: _LayoutAnchors) -> bool:
    items = row.get("items") or []
    if not items:
        return False
    if _row_is_preserved_intra_table_label(row, layout):
        return False
    if any(_is_row_num_item(it, layout) for it in items):
        return False
    if any(_is_value_item(it, layout) for it in items):
        return False
    if not all(
        _is_label_col_item(it, layout) or _is_row_label_item(it, layout)
        for it in items
    ):
        return False
    text = "".join(str(it.get("text", "")).strip() for it in items)
    if not text:
        return False
    if text in _SECTION_BAND_ROW_TEXTS:
        return False
    if len(text) <= _LABEL_CONT_MAX_LEN:
        return True
    if _FINANCIAL_WRAP_FRAGMENT_RE.search(text) and len(text) <= 16:
        return True
    if text.endswith((")", "）")):
        return True
    return False


def _row_is_mid_column_wrap_fragment(row: dict, layout: _LayoutAnchors) -> bool:
    """机构地址等宽文本列折行：无值列、标签列文本；支持短地址续行碎片。"""
    from codes.table_engine.split.row_classify import text_looks_like_wrapped_address

    items = [it for it in row.get("items") or [] if str(it.get("text", "")).strip()]
    if not items:
        return False
    # 排除含行号或数值的 item
    label_items: List[dict] = []
    for it in items:
        if _is_row_num_item(it, layout) or _is_value_item(it, layout):
            return False
        if _is_label_col_item(it, layout):
            label_items.append(it)
    if not label_items:
        return False

    # 单 item：标准路径
    if len(label_items) == 1:
        it = label_items[0]
        text = str(it.get("text", "")).strip()
        if text_looks_like_wrapped_address(text):
            return True
        if _is_short_address_continuation(text):
            return True
        if _row_is_preserved_intra_table_label(row, layout):
            return False
        if len(text) <= _LABEL_CONT_MAX_LEN:
            return False
        from codes.table_engine.split.boundary_overlap import row_is_wrapped_label_continuation_tail
        if row_is_wrapped_label_continuation_tail([text]):
            return False
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    # 多 item：全部在标签列且至少一个是地址续行 → 仍视为折行碎片
    if len(label_items) == len(items):
        for it in label_items:
            text = str(it.get("text", "")).strip()
            if text_looks_like_wrapped_address(text) or _is_short_address_continuation(text):
                return True

    return False


def _estimate_layout(rows: List[dict]) -> _LayoutAnchors:
    layout = _LayoutAnchors()
    row_num_x: List[float] = []
    label_x: List[float] = []
    value_x: List[float] = []

    for row in rows:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            x0 = float(it.get("x0", 0))
            if _ROW_NUMBER_RE.match(t) and x0 < 100:
                row_num_x.append(float(it.get("x1", x0)))
            elif is_numeric_data_cell(t) or t in ("-", "—"):
                value_x.append(x0)
            elif re.search(r"[\u4e00-\u9fff]", t) and not is_numeric_data_cell(t):
                if x0 < 100 or 90 < x0 < 220:
                    label_x.append(x0)

    if row_num_x:
        layout.row_num_x_max = max(row_num_x) + 8
    if label_x:
        layout.label_x_min = min(label_x) - 10
        layout.label_x_max = max(label_x) + 80
    if value_x:
        layout.value_x_min = min(value_x) - 15
        if label_x:
            layout.label_x_max = min(layout.label_x_max, layout.value_x_min - 20)
    return layout


def _refine_layout_from_body(rows: List[dict], body_start: int) -> _LayoutAnchors:
    """用数据区反推列锚点（标签列/数值列 X），表头投影更稳。"""
    body = rows[body_start:] if body_start < len(rows) else rows
    if not body:
        return _estimate_layout(rows)

    layout = _estimate_layout(body)
    if layout.value_x_min < 220.0 or layout.label_x_min > 95.0:
        full = _estimate_layout(rows)
        if layout.value_x_min >= 220.0:
            layout.value_x_min = full.value_x_min
        if layout.label_x_min <= 95.0:
            layout.label_x_min = full.label_x_min
            layout.label_x_max = full.label_x_max
        if layout.row_num_x_max == 92.0 and full.row_num_x_max != 92.0:
            layout.row_num_x_max = full.row_num_x_max
    return layout


def _find_body_start_index(rows: List[dict], layout: _LayoutAnchors) -> int:
    for i, row in enumerate(rows):
        if _row_has_body_signature(row, layout):
            return i
    return len(rows)


def _mark_row_phases(rows: List[dict], layout: _LayoutAnchors, body_start: int) -> None:
    for i, row in enumerate(rows):
        if i >= body_start and _row_has_body_signature(row, layout):
            row["row_phase"] = _ROW_PHASE_BODY
        else:
            row["row_phase"] = _ROW_PHASE_HEADER


def _item_source_ids(it: dict) -> List[str]:
    ids = [str(x) for x in (it.get("_source_item_ids") or []) if x]
    idx = str(it.get("item_index", ""))
    if idx and idx not in ids:
        ids.append(idx)
    return ids


def _merge_item_source_ids(target: dict, other: dict) -> None:
    merged: List[str] = []
    seen: set = set()
    for sid in _item_source_ids(target) + _item_source_ids(other):
        if sid and sid not in seen:
            seen.add(sid)
            merged.append(sid)
    if merged:
        target["_source_item_ids"] = merged


def _merge_items_vertical(
    row_a: dict,
    row_b: dict,
    *,
    prepend_from_b: bool = False,
) -> dict:
    items_a = [dict(it) for it in (row_a.get("items") or [])]
    items_b = list(row_b.get("items") or [])
    used_b: set = set()

    for it_a in items_a:
        for j, it_b in enumerate(items_b):
            if j in used_b:
                continue
            if not _items_share_x_band(it_a, it_b):
                continue
            ta = str(it_a.get("text", "")).strip()
            tb = str(it_b.get("text", "")).strip()
            if ta and tb and ta != tb:
                if is_pd_range_cell_text(ta) and is_pd_range_cell_text(tb):
                    continue
                it_a["text"] = (tb + ta) if prepend_from_b else (ta + tb)
            elif tb:
                it_a["text"] = tb
            it_a["y1"] = max(float(it_a.get("y1", 0)), float(it_b.get("y1", 0)))
            _merge_item_source_ids(it_a, it_b)
            used_b.add(j)
            break

    for j, it_b in enumerate(items_b):
        if j not in used_b:
            items_a.append(it_b)

    return _build_row_dict(items_a)


def _merge_label_into_numbered(label_row: dict, numbered_row: dict) -> dict:
    merged = _merge_items_vertical(numbered_row, label_row, prepend_from_b=True)
    merged["cluster_row_id"] = numbered_row.get("cluster_row_id", 0)
    merged["row_phase"] = _ROW_PHASE_BODY
    return merged


def _split_combined_row_num_label_items(row: dict, layout: _LayoutAnchors) -> dict:
    new_items = []
    changed = False
    for it in row.get("items") or []:
        t = str(it.get("text", "")).strip()
        m = _ROW_NUM_LABEL_RE.match(t)
        if m and float(it.get("x0", 0)) <= layout.row_num_x_max:
            if is_report_period_cell(t) or _YEAR_ONLY_RE.match(t.replace(" ", "")):
                new_items.append(it)
                continue
            changed = True
            x0 = float(it.get("x0", 0))
            x1 = float(it.get("x1", 0))
            mid_x = x0 + (x1 - x0) * 0.22
            num_it = dict(it)
            num_it["text"] = m.group(1)
            num_it["x1"] = mid_x
            lbl_it = dict(it)
            lbl_it["text"] = m.group(2).strip()
            lbl_it["x0"] = mid_x + 1
            new_items.extend([num_it, lbl_it])
        else:
            new_items.append(it)
    if not changed:
        return row
    out = _build_row_dict(new_items)
    out["cluster_row_id"] = row.get("cluster_row_id", 0)
    out["row_phase"] = row.get("row_phase", "")
    return out


def _label_text_incomplete(text: str) -> bool:
    """折行标签尚未结束（可向上/向下续并）。"""
    t = str(text or "").strip()
    if not t:
        return True
    if t.endswith(("(", "（", "当期", "计入", "变动", "且其")):
        return True
    if ("（" in t or "(" in t) and not t.endswith((")", "）")):
        return True
    if t.endswith(("扣除", "现金流量")) and 8 <= len(re.findall(r"[\u4e00-\u9fff]", t)) <= 22:
        return True
    return False


def _numbered_row_lacks_label(row: dict, layout: _LayoutAnchors) -> bool:
    items = row.get("items") or []
    if not any(_is_row_num_item(it, layout) for it in items):
        return False
    return not any(_is_label_col_item(it, layout) for it in items)


def _row_is_substantial_header(row: dict, layout: _LayoutAnchors) -> bool:
    items = row.get("items") or []
    if len(items) >= 6:
        return True
    texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
    if len(texts) >= 4 and len(set(texts)) >= 3:
        return True
    return False


def _row_is_period_date_row(row: dict) -> bool:
    items = row.get("items") or []
    has_period = any(str(it.get("text", "")).strip() == "期间" for it in items)
    has_date = any(
        is_report_period_cell(str(it.get("text", "")).strip()) for it in items
    )
    return has_period and has_date


def _row_is_measure_title_row(row: dict) -> bool:
    """仅数据列上的指标标题行（无左侧「期间」等行标签）。"""
    items = row.get("items") or []
    if not items:
        return False
    if any(float(it.get("x0", 0)) < 150 for it in items):
        return False
    joined = "".join(str(it.get("text", "")).strip() for it in items)
    return any(k in joined for k in ("变动", "经济价值", "净利息", "收入", "资本"))


def _can_merge_header_vertical_pair(
    row_a: dict,
    row_b: dict,
    layout: _LayoutAnchors,
) -> bool:
    """仅表头带：纵并碎片，禁止触及数据行。"""
    if _both_locked_data_rows(row_a, row_b, layout):
        return False
    if _row_is_locked_data_row(row_a, layout) or _row_is_locked_data_row(row_b, layout):
        return False

    if _row_is_period_date_row(row_a) and _row_is_measure_title_row(row_b):
        return False
    if _row_is_period_date_row(row_b) and _row_is_measure_title_row(row_a):
        return False
    if _row_is_year_only_band(row_a) and _row_is_rate_metric_banner(row_b):
        return False
    if _row_is_year_only_band(row_b) and _row_is_rate_metric_banner(row_a):
        return False
    if _row_is_period_band_row(row_a) and _row_is_identical_subheader_band(row_b):
        return False
    if _row_is_period_band_row(row_b) and _row_is_identical_subheader_band(row_a):
        return False
    if _row_is_period_band_row(row_a) and _row_is_month_day_band_row(row_b):
        return False
    if _row_is_period_band_row(row_b) and _row_is_month_day_band_row(row_a):
        return False
    if _row_is_entity_scope_header_row(row_a) and (
        _row_is_period_band_row(row_b)
        or _row_is_month_day_band_row(row_b)
    ):
        return False
    if _row_is_entity_scope_header_row(row_b) and (
        _row_is_period_band_row(row_a)
        or _row_is_month_day_band_row(row_a)
    ):
        return False
    if _row_is_metric_column_header_row(row_a) or _row_is_metric_column_header_row(row_b):
        if _row_is_year_only_band(row_a) or _row_is_year_only_band(row_b):
            return False
        if _row_is_rate_metric_banner(row_a) or _row_is_rate_metric_banner(row_b):
            return False
    if _is_ecl_stage_header_hierarchy_pair(row_a, row_b):
        return False

    gap = _y_gap(row_a, row_b)
    if gap < -3 or gap > _Y_MERGE_MAX_GAP:
        return False

    num_a = _row_number_text(row_a, layout)
    num_b = _row_number_text(row_b, layout)
    if num_a and num_b:
        return False

    if _row_is_substantial_header(row_b, layout):
        return False

    if _row_is_header_fragment(row_b, layout):
        return True

    if _row_is_header_fragment(row_b, layout):
        for it_b in row_b.get("items") or []:
            for it_a in row_a.get("items") or []:
                if _items_share_x_band(it_a, it_b):
                    return True

    if not num_a and not num_b:
        if len(row_a.get("items") or []) <= 4:
            aligned = 0
            for it_a in row_a.get("items") or []:
                for it_b in row_b.get("items") or []:
                    if _items_share_x_band(it_a, it_b):
                        aligned += 1
                        break
            if aligned >= 2 and not any(
                _is_value_item(it, layout) for it in row_b.get("items") or []
            ):
                return True

    return False


def _can_merge_label_with_numbered(
    label_row: dict,
    numbered_row: dict,
    layout: _LayoutAnchors,
) -> bool:
    """上方仅标签行 → 并入下方数据行（允许往上并，不往下并）。"""
    if _row_is_preserved_intra_table_label(label_row, layout):
        return False
    if not _row_is_label_only(label_row, layout):
        return False
    if not _row_has_body_signature(numbered_row, layout):
        return False
    label_text = "".join(
        str(it.get("text", "")).strip()
        for it in label_row.get("items") or []
    )
    from codes.table_engine.split.boundary_overlap import row_is_label_suffix_tail

    if row_is_label_suffix_tail([label_text]) or _FINANCIAL_WRAP_FRAGMENT_RE.search(label_text):
        return False
    if label_text.endswith((")", "）")):
        return False
    if not _numbered_row_lacks_label(numbered_row, layout):
        if not _label_items_share_x_band(label_row, numbered_row, layout):
            return False
        frag = "".join(
            str(it.get("text", "")).strip()
            for it in label_row.get("items") or []
        )
        existing = "".join(
            str(it.get("text", "")).strip()
            for it in numbered_row.get("items") or []
            if _is_label_col_item(it, layout)
        )
        if (
            existing
            and not _label_text_incomplete(existing)
            and frag
            and not _label_text_incomplete(frag)
        ):
            return False
    _, y1_lbl = _label_row_span(label_row, layout)
    val_items = _value_items(numbered_row, layout)
    if val_items:
        y0_data = min(float(it.get("y0", 0)) for it in val_items)
    else:
        y0_data, _ = _row_span(numbered_row)
    return (y0_data - y1_lbl) <= _Y_MERGE_MAX_GAP


def _collapse_intra_row_same_x_items(row: dict, tol: float = 10.0) -> dict:
    items = list(row.get("items") or [])
    if len(items) < 2:
        return row

    items = sorted(items, key=lambda it: (_ym(it), float(it.get("x0", 0))))
    merged: List[dict] = []
    for it in items:
        placed = False
        for m in merged:
            if _items_share_x_band(m, it, tol=tol):
                ta = str(m.get("text", "")).strip()
                tb = str(it.get("text", "")).strip()
                if ta and tb and ta != tb:
                    if is_report_period_cell(ta) != is_report_period_cell(tb):
                        continue
                    if is_pd_range_cell_text(ta) and is_pd_range_cell_text(tb):
                        continue
                    m["text"] = ta + tb
                elif tb:
                    m["text"] = tb
                m["y0"] = min(float(m.get("y0", 0)), float(it.get("y0", 0)))
                m["y1"] = max(float(m.get("y1", 0)), float(it.get("y1", 0)))
                placed = True
                break
        if not placed:
            merged.append(dict(it))
    out = _build_row_dict(merged)
    out["cluster_row_id"] = row.get("cluster_row_id", 0)
    out["row_phase"] = row.get("row_phase", "")
    return out


def _split_row_multiple_pd_ranges(row: dict) -> List[dict]:
    """同一 Y 行内多个违约概率区间 → 拆成多行（数值/类别按纵坐标就近归属）。"""
    items = list(row.get("items") or [])
    pd_items = [
        it for it in items
        if is_pd_range_cell_text(str(it.get("text", "")).strip())
        and float(it.get("x0", 0)) < 165
    ]
    if len(pd_items) < 2:
        return [row]

    pd_items = sorted(pd_items, key=lambda it: float(it.get("y0", 0)))
    others = [it for it in items if it not in pd_items]
    out_rows: List[dict] = []
    for pd_it in pd_items:
        py = _ym(pd_it)
        row_items = [pd_it]
        for it in others:
            iy = _ym(it)
            x0 = float(it.get("x0", 0))
            nearest_pd = min(pd_items, key=lambda p: abs(_ym(p) - iy))
            if nearest_pd is not pd_it:
                continue
            if x0 >= 165 and abs(iy - py) > 10:
                continue
            row_items.append(it)
        new_row = _build_row_dict(row_items)
        new_row["cluster_row_id"] = row.get("cluster_row_id", 0)
        new_row["row_phase"] = row.get("row_phase", "")
        out_rows.append(new_row)
    return out_rows


_HEADER_CATEGORY_SKIP = frozenset({"风险", "暴露", "类别", "违约"})


def _rollup_exposure_category_blocks(rows: List[dict]) -> List[dict]:
    """CR6：暴露类别折行（金融+机构）合并到区块首行，模拟纵向合并单元格。"""
    body_start: int | None = None
    for idx, row in enumerate(rows):
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if (
                is_pd_range_cell_text(t)
                and t.startswith("[")
                and float(it.get("x0", 0)) >= _CR6_PD_X_MIN - 8
            ):
                body_start = idx
                break
        else:
            continue
        break

    if body_start is None:
        return rows

    i = body_start
    while i < len(rows):
        block_start = i
        frags: List[str] = []
        j = i
        while j < len(rows):
            row = rows[j]
            items = row.get("items") or []
            pd_texts = [
                str(it.get("text", "")).strip()
                for it in items
                if is_pd_range_cell_text(str(it.get("text", "")).strip())
            ]
            has_body_value = any(
                float(it.get("x0", 0)) >= 165
                and is_numeric_data_cell(str(it.get("text", "")).strip())
                for it in items
            )
            for it in items:
                t = str(it.get("text", "")).strip()
                x0 = float(it.get("x0", 0))
                if (
                    has_body_value
                    and x0 < _CR6_CATEGORY_X_MAX
                    and t
                    and t not in _HEADER_CATEGORY_SKIP
                    and not is_pd_range_cell_text(t)
                    and not is_numeric_data_cell(t)
                    and len(t) <= 12
                    and re.search(r"[\u4e00-\u9fff]", t)
                    and t not in frags
                ):
                    frags.append(t)
            if any(t == "小计" for t in pd_texts):
                j += 1
                break
            j += 1

        if len(frags) >= 2 and j > block_start:
            combined = "".join(frags)
            cat_source_items: List[dict] = []
            for k in range(block_start, j):
                for it in rows[k].get("items") or []:
                    t = str(it.get("text", "")).strip()
                    x0 = float(it.get("x0", 0))
                    if (
                        x0 < _CR6_CATEGORY_X_MAX
                        and t
                        and t not in _HEADER_CATEGORY_SKIP
                        and not is_pd_range_cell_text(t)
                        and not is_numeric_data_cell(t)
                    ):
                        cat_source_items.append(it)
            anchor = cat_source_items[0] if cat_source_items else None
            for k in range(block_start, j):
                kept = [
                    it
                    for it in rows[k].get("items") or []
                    if not (
                        float(it.get("x0", 0)) < _CR6_CATEGORY_X_MAX
                        and str(it.get("text", "")).strip()
                        and not is_pd_range_cell_text(str(it.get("text", "")).strip())
                        and not is_numeric_data_cell(str(it.get("text", "")).strip())
                    )
                ]
                rows[k] = dict(rows[k])
                rows[k]["items"] = kept
            if anchor:
                cat_it = dict(anchor)
                cat_it["text"] = combined
                for src_it in cat_source_items[1:]:
                    _merge_item_source_ids(cat_it, src_it)
                rows[block_start]["items"] = [cat_it] + list(rows[block_start].get("items") or [])
        i = max(j, i + 1)
    return rows


_CHANGE_METRIC_WRAP_SUFFIX = frozenset({"增减", "末增减", "期增减"})


def _row_cells_from_items(row: dict) -> List[str]:
    return [
        str(it.get("text", "")).strip()
        for it in row.get("items") or []
        if str(it.get("text", "")).strip()
    ]


def _row_is_annual_header_band_row(row: dict) -> bool:
    from codes.table_engine.scope.header_scope import is_annual_report_column_header_row

    cells = _row_cells_from_items(row)
    return bool(cells) and is_annual_report_column_header_row(cells)


def _row_is_annual_header_subrow(row: dict, layout: _LayoutAnchors) -> bool:
    """表头次行：仅月日/增减尾片，无数值列金额。"""
    items = row.get("items") or []
    texts = _row_cells_from_items(row)
    if not texts:
        return False
    if any(_is_value_item(it, layout) for it in items):
        return False
    if any(_is_row_num_item(it, layout) for it in items):
        return False
    for t in texts:
        if is_month_day_cell(t) or t in _CHANGE_METRIC_WRAP_SUFFIX:
            continue
        if t in ("比上年同期",):
            continue
        return False
    return True


def _merge_header_subrow_into_band(row_a: dict, row_b: dict) -> dict:
    """双层表头次行按 x 并入上一行同列（保留原文顺序）。"""
    items_a = [dict(it) for it in row_a.get("items") or []]
    for it_b in row_b.get("items") or []:
        tb = str(it_b.get("text", "")).strip()
        if not tb:
            continue
        bx = float(it_b.get("x0", 0))
        best: Optional[dict] = None
        best_d = 1e9
        for it_a in items_a:
            ta = str(it_a.get("text", "")).strip()
            if not ta:
                continue
            d = abs(float(it_a.get("x0", 0)) - bx)
            if d < best_d:
                best_d = d
                best = it_a
        if best is not None and best_d <= 45.0:
            base = str(best.get("text", "")).strip()
            best["text"] = f"{base}{tb}" if base else tb
            _merge_item_source_ids(best, it_b)
        else:
            items_a.append(dict(it_b))
    out = _build_row_dict(
        sorted(items_a, key=lambda it: float(it.get("x0", 0))),
    )
    out["cluster_row_id"] = row_a.get("cluster_row_id", 0)
    out["row_phase"] = row_a.get("row_phase", "")
    return out


def _refine_header_band(
    rows: List[dict],
    layout: _LayoutAnchors,
    body_start: int,
) -> List[dict]:
    """表头带：合并折行次行（增减/月日）到上一行同列，保持列标完整。"""
    end = body_start if body_start > 0 else len(rows)
    i = 0
    while i < min(end, len(rows)) - 1:
        if (
            _row_is_annual_header_band_row(rows[i])
            and _row_is_annual_header_subrow(rows[i + 1], layout)
        ):
            rows[i] = _merge_header_subrow_into_band(rows[i], rows[i + 1])
            del rows[i + 1]
            end -= 1
            continue
        if (
            _row_is_annual_header_band_row(rows[i])
            and len(_row_cells_from_items(rows[i + 1])) == 1
            and _row_cells_from_items(rows[i + 1])[0] in _CHANGE_METRIC_WRAP_SUFFIX
        ):
            rows[i] = _merge_header_subrow_into_band(rows[i], rows[i + 1])
            del rows[i + 1]
            end -= 1
            continue
        i += 1
    return rows


def _split_wrap_head_intruding_value_row(
    row: dict,
    layout: _LayoutAnchors,
) -> List[dict]:
    """Y 聚类过宽时：数据行误吞下一行折行标签首片 → 拆出独立行（保留坐标顺序）。"""
    from codes.table_engine.geometry.data_column_assign import is_data_value_item

    items = list(row.get("items") or [])
    val_items = [
        it for it in items
        if is_data_value_item(str(it.get("text", "")).strip())
    ]
    if not val_items:
        return [row]
    val_y = sum(_ym(it) for it in val_items) / len(val_items)
    keep: List[dict] = []
    intruders: List[dict] = []
    for it in items:
        t = str(it.get("text", "")).strip()
        if not t:
            continue
        is_label = _is_label_col_item(it, layout) or _is_row_label_item(it, layout)
        if (
            is_label
            and not is_data_value_item(t)
            and _ym(it) > val_y + 4.0
            and (
                t.startswith(("以", "入", "和", "及", "其", "指"))
                or t.endswith(("计", "入", "的", "综合"))
            )
        ):
            intruders.append(it)
        else:
            keep.append(it)
    if not intruders:
        return [row]
    base = dict(row)
    base["items"] = keep
    frag = dict(row)
    frag["items"] = intruders
    return [base, frag]


def _split_overclustered_rows(rows: List[dict], layout: _LayoutAnchors) -> List[dict]:
    """拆分 Y 聚类过宽的行（地址续行碎片 + 下一行数据被错误聚在一起）。

    Case B 拆分出的碎片行必须向上并入前一行，而非作为独立行留给后续
    _refine_body_band（后者会错误地把碎片向下并入下一数据行，破坏数据归属）。
    """
    out: List[dict] = []
    for row in rows:
        split = _maybe_split_overclustered_row(row, layout)
        if len(split) == 2:
            upper, lower = split[0], split[1]
            has_row_num_in_upper = any(
                _is_row_num_item(it, layout) for it in upper.get("items") or []
            )
            if has_row_num_in_upper:
                # Case A（2+ 行号）：两行各自独立
                out.append(upper)
                out.append(lower)
            else:
                # Case B（上行地址碎片 + 下行数据）：
                # 碎片向上并入前一行（addr continuation → previous logical row）
                if out:
                    merged_prev = _merge_label_into_numbered(upper, out[-1])
                    out[-1] = merged_prev
                out.append(lower)
        else:
            out.append(row)
    return out


def _maybe_split_overclustered_row(row: dict, layout: _LayoutAnchors) -> List[dict]:
    """检测一行中是否混入了两个逻辑行的 item，按 Y 坐标拆分。

    两种情况：
    A. 一行含 2+ 行号（如 "27" 和 "28"）→ 按第二个行号 Y 拆为两行
    B. 一行含 1 行号 + 数值，但有标签列 item 的 Y 明显高于行号
       （这是上行地址续行碎片）→ 拆出高 Y 的标签 item 作为独立碎片行
    """
    items = row.get("items") or []
    if not items or len(items) < 3:
        return [row]

    # 找所有行号 item
    row_nums = [it for it in items if _is_row_num_item(it, layout)]
    has_values = any(_is_value_item(it, layout) for it in items)

    # Case A: 多个行号
    if len(row_nums) >= 2:
        row_nums_sorted = sorted(row_nums, key=_ym)
        split_y = _ym(row_nums_sorted[1]) - 2.0
        upper = [it for it in items if _ym(it) < split_y]
        lower = [it for it in items if _ym(it) >= split_y]
        if len(upper) >= 1 and len(lower) >= 2:
            return _build_two_rows(upper, lower, row)
        return [row]

    # Case B: 单行号 + 数值 + 高 Y 标签碎片
    if len(row_nums) == 1 and has_values:
        row_num = row_nums[0]
        row_num_y = _ym(row_num)

        # 计算 label 列 item 的 Y 中位数，用于判断碎片是否显著偏上
        label_ys = [
            _ym(it) for it in items
            if _is_label_col_item(it, layout) and str(it.get("text", "")).strip()
        ]
        label_y_median = sorted(label_ys)[len(label_ys) // 2] if label_ys else row_num_y

        upper_frags: List[dict] = []
        lower_items: List[dict] = []
        upper_x0_set: List[float] = []

        for it in items:
            if it is row_num:
                lower_items.append(it)
                continue
            t = str(it.get("text", "")).strip()
            if not t:
                continue

            if _is_label_col_item(it, layout):
                it_x0 = float(it.get("x0", 0))
                it_y = _ym(it)
                # 只拆分 Y 显著偏上的标签 item（高于 label 列 Y 中位数一段距离）
                # 且 X 必须在 label 列带内（不在 value 列）
                if (
                    it_y < label_y_median - 2.0
                    and it_x0 < layout.value_x_min - 5
                    and _is_address_like_fragment(t)
                ):
                    upper_frags.append(it)
                    upper_x0_set.append(it_x0)
                    continue

            lower_items.append(it)

        # X 一致性校验：所有拆出的碎片必须在同一窄列带内
        x_consistent = True
        if len(upper_x0_set) >= 2:
            x_span = max(upper_x0_set) - min(upper_x0_set)
            if x_span > 30.0:  # 碎片散布太宽 → 不是同一列地址续行
                x_consistent = False

        if upper_frags and len(lower_items) >= 2 and x_consistent:
            return _build_two_rows(upper_frags, lower_items, row)

    return [row]


def _build_two_rows(
    upper_items: List[dict],
    lower_items: List[dict],
    source_row: dict,
) -> List[dict]:
    """将两组 item 分别构建为两个行 dict。

    upper 继承原 cluster_row_id，lower 也继承原 ID（不 +1，避免与后续行冲突）。
    调用方需根据 Case A/B 自行决定是否将 upper 向上并入。
    """
    cid = int(source_row.get("cluster_row_id", 0))
    phase = source_row.get("row_phase", "")
    upper = _build_row_dict(upper_items)
    upper["cluster_row_id"] = cid
    upper["row_phase"] = phase
    lower = _build_row_dict(lower_items)
    lower["cluster_row_id"] = cid
    lower["row_phase"] = phase
    return [upper, lower]


def _refine_body_band(rows: List[dict], layout: _LayoutAnchors) -> List[dict]:
    """数据区：先拆 Y 聚类过宽行，再把折行标签/续片并入相邻数据行，禁止数据行互并。"""

    # Step 0: 拆分 Y 聚类过宽的行（地址续行 + 下一行数据被聚在一起）
    rows = _split_overclustered_rows(rows, layout)

    # Step 1: 标签在上 → 并入下方数据行
    i = 0
    while i < len(rows) - 1:
        if _can_merge_label_with_numbered(rows[i], rows[i + 1], layout):
            rows[i + 1] = _merge_label_into_numbered(rows[i], rows[i + 1])
            del rows[i]
            continue
        i += 1

    i = 0
    while i < len(rows) - 1:
        if (
            _row_has_body_signature(rows[i], layout)
            and _row_is_continuation_fragment(rows[i + 1], layout)
            and _label_below_overlaps_value_band(rows[i], rows[i + 1], layout)
            and _y_gap_value_anchored(rows[i], rows[i + 1], layout) <= _Y_MERGE_MAX_GAP
        ):
            rows[i] = _merge_rows_preserve_id(rows[i], rows[i + 1])
            rows[i]["row_phase"] = _ROW_PHASE_BODY
            del rows[i + 1]
            continue
        if (
            _row_has_body_signature(rows[i], layout)
            and _row_is_mid_column_wrap_fragment(rows[i + 1], layout)
            and _label_items_share_x_band(rows[i], rows[i + 1], layout)
            and _y_gap_value_anchored(rows[i], rows[i + 1], layout) <= _Y_MERGE_MAX_GAP
        ):
            rows[i] = _merge_rows_preserve_id(rows[i], rows[i + 1])
            rows[i]["row_phase"] = _ROW_PHASE_BODY
            del rows[i + 1]
            continue
        i += 1

    return rows


def refine_clustered_rows(rows: List[dict]) -> List[dict]:
    """Y 聚类后：先锁数据行，再修表头带，最后修补 body 折行。"""
    if len(rows) < 2:
        return rows

    rows = _tag_cluster_rows(rows)

    layout_rough = _estimate_layout(rows)
    body_start = _find_body_start_index(rows, layout_rough)
    layout = _refine_layout_from_body(rows, body_start)

    rows = [_split_combined_row_num_label_items(r, layout) for r in rows]
    body_start = _find_body_start_index(rows, layout)
    _mark_row_phases(rows, layout, body_start)

    rows = _refine_header_band(rows, layout, body_start)
    body_start = _find_body_start_index(rows, layout)
    _mark_row_phases(rows, layout, body_start)

    split_rows: List[dict] = []
    for row in rows:
        split_rows.extend(_split_wrap_head_intruding_value_row(row, layout))
    rows = split_rows

    rows = _refine_body_band(rows, layout)

    expanded: List[dict] = []
    for row in rows:
        expanded.extend(_split_row_multiple_pd_ranges(row))
    rows = expanded

    rows = _rollup_exposure_category_blocks(rows)

    rows = [_collapse_intra_row_same_x_items(r) for r in rows]
    return rows
