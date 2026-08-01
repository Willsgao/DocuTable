# -*- coding: utf-8
"""数值格判定（自 header_boundary 抄写，无 table_validator 依赖）。"""

from __future__ import annotations

import re
from typing import List

_YEAR_CELL_RE = re.compile(r"^[\s　]*(19|20)\d{2}\s*年?[\s　]*$")
_MONTH_DAY_CELL_RE = re.compile(
    r"^[\s　]*(?:\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}\s*[月日])[\s　]*$"
)
_REPORT_DATE_CELL_RE = re.compile(
    r"^[\s　]*(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月\s*\d{1,2}\s*日)?[\s　]*$"
)
_NUMERIC_DATA_RE = re.compile(
    r"^[\s　]*"
    r"(?:\(-?[\d,，]+\.?\d*\)|"
    r"-?[\d,，]+\.?\d*%?)"
    r"[\s　]*$"
)
_DASH_VALUES = frozenset(("-", "－", "—", "–", "－"))
_LABEL_TRAILING_AMOUNT_RE = re.compile(
    r"^(.+[\u4e00-\u9fff])\s*"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\(\d[\d,.\-]*\)|\d+\.\d+)$",
)
_PERCENT_TRAILING_TEXT_RE = re.compile(
    r"^(-?[\d,，]+\.?\d*%)\s*([\u4e00-\u9fff].+)$",
)
_PERCENT_TRAILING_TEXT_SEARCH_RE = re.compile(
    r"(-?[\d,，]+\.?\d*%)\s*([\u4e00-\u9fff].+)$",
)
# 近三年指标等：「上年数值% + 上升/下降…个百分点」跨列粘连（非变化原因说明）
_PERCENT_POINT_CHANGE_RE = re.compile(
    r"^(-?[\d,，]+\.?\d*%)\s*"
    r"((?:上升|下降|增加|减少|变动|提高|降低|扩大|收窄)"
    r"[\s　]*[\d,，.]+[\s　]*个百分点.*)$"
)
_PERCENT_POINT_PHRASE_RE = re.compile(
    r"(?:上升|下降|增加|减少|变动|提高|降低|扩大|收窄)"
    r"[\s　]*[\d,，.]+[\s　]*个百分点"
)
# 表体「增减幅度」后常见说明用语；纯名词短语（如「风险权重」）多为标签内嵌 %
_CHANGE_REASON_HINTS = (
    "减少", "增加", "变动", "上升", "下降", "计提", "扩大", "收缩", "调整",
    "收益", "损失", "支出", "收入", "补助", "估值", "波动", "反弹", "回落",
    "转股", "转换", "清算", "发行", "回购", "核销",
)
# 标签/表头内嵌倍率（非独立增减幅度列数值）
_LABEL_EMBEDDED_PCT_VALUES = frozenset({
    75, 100, 125, 150, 250, 500, 750, 1000, 1250, 1500,
})
_QUARTER_HEADER_PART_RE = re.compile(
    r"[一二三四]季度(?:[（(][^）)]*[）)])?"
)


def is_quarter_column_header_text(text: str) -> bool:
    """季度列表头：一季度（1-3 月）等。"""
    t = str(text or "").strip()
    if not t or len(t) > 22:
        return False
    if _QUARTER_HEADER_PART_RE.fullmatch(t):
        return True
    return bool(re.match(r"^[一二三四]季度", t))


def split_quarter_header_compound_text(text: str) -> List[str]:
    """OCR 将多列季度表头粘成一格时拆成独立片段。"""
    t = str(text or "").strip()
    if not t:
        return []
    parts = _QUARTER_HEADER_PART_RE.findall(t)
    if len(parts) < 2:
        return []
    out = list(parts)
    if "项目" in t and "项目" not in out:
        out.append("项目")
    return out


def is_quarter_column_header_row(cells: List[str]) -> bool:
    """表头行：≥2 个季度列（可含项目）。"""
    hits = sum(1 for c in cells if is_quarter_column_header_text(str(c or "").strip()))
    return hits >= 2


_FULL_REPORT_DATE_PART_RE = re.compile(
    r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月\s*\d{1,2}\s*日)?"
)
_CHANGE_METRIC_HEADER_MARKERS = ("增减幅度", "增减变动", "增减变化")


def is_report_date_header_part_text(text: str) -> bool:
    """报告期列表头片段（含增减幅度列标；不含独立「变化原因」列——避免被当折行尾片）。"""
    t = str(text or "").strip()
    if not t:
        return False
    if is_report_date_cell(t):
        return True
    return t in _CHANGE_METRIC_HEADER_MARKERS or t == "增减"


def split_report_date_header_compound_text(text: str) -> List[str]:
    """OCR 将多列报告期+增减/变化原因粘成一格 → 独立片段。

    同时支持：
    - 双报告期：``2024年 … 2023年 … 增减幅度``
    - 单报告期粘连：``2023 年 增减幅度 变化原因``
    """
    t = str(text or "").strip()
    if not t or len(t) < 8:
        return []
    dates = _FULL_REPORT_DATE_PART_RE.findall(t)
    metrics: List[str] = []
    for marker in _CHANGE_METRIC_HEADER_MARKERS:
        if marker in t:
            metrics.append(marker)
            break
    for extra in ("主要原因", "变化原因"):
        if extra in t and extra not in metrics:
            metrics.append(extra)

    if len(dates) >= 2:
        out = list(dates) + metrics
        return out if len(out) >= 3 else []
    # 单日期 + 至少两个列表头（如 增减幅度 + 变化原因）
    if len(dates) == 1 and len(metrics) >= 2:
        return list(dates) + metrics
    return []


def is_report_date_header_compound_text(text: str) -> bool:
    return len(split_report_date_header_compound_text(text)) >= 3


def _chinese_before_first_digit(text: str) -> bool:
    m = re.search(r"\d", text)
    if not m:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text[: m.start()]))


def _percent_token_looks_like_table_change_metric(pct: str) -> bool:
    """前导 % 是否像表体独立增减幅度（非标签内「1250%风险权重」类修饰）。"""
    if not is_numeric_data_cell(pct):
        return False
    core = pct.rstrip("%").replace(",", "").replace("，", "").strip()
    if core.startswith("(") and core.endswith(")"):
        core = core[1:-1].strip()
    if core.startswith("-"):
        return True
    if "." in core:
        return True
    try:
        v = float(core)
    except ValueError:
        return True
    if v in _LABEL_EMBEDDED_PCT_VALUES:
        return False
    if v == int(v) and v <= 150:
        return False
    return True


def _trailing_text_looks_like_change_reason(text: str) -> bool:
    """后缀是否像「变化原因」说明，而非标签短语延续。"""
    t = str(text or "").strip()
    if len(t) < 2:
        return False
    if any(h in t for h in _CHANGE_REASON_HINTS):
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", t))
    # 变化原因表末列常见短说明（如「可转债转股」）
    if 2 <= cn <= 14 and re.fullmatch(r"[\u4e00-\u9fff]+", t):
        return True
    return cn >= 8 and t.endswith(("。", "；"))


# 误落项目列时用于识别「主要原因」说明（勿用「收益」等易出现在标签内的子串）
_REASON_COLUMN_ACTION_HINTS = (
    "减少", "增加", "变动", "上升", "下降", "计提", "扩大", "收缩", "调整",
    "转股", "转换", "清算", "发行", "回购", "核销", "波动", "反弹", "回落",
    "补助", "估值",
)


# 财务报表行项目标签前缀（含「变动」但属于项目列，非变化原因末列）
_STATEMENT_LINE_LABEL_PREFIXES = (
    "以公允价值", "以摊余成本", "指定为以", "分类为以", "划分为",
)


def looks_like_change_reason_description_not_label(text: str) -> bool:
    """表体「主要原因」说明（须末列），非「项目」列标签。

    须含变化原因常见动词/宾语（增加、计提、清算…），避免把会计科目行项目误判。
    「下降 0.97 个百分点」类增减列文字不属变化原因。
    """
    t = str(text or "").strip()
    if len(t) < 5 or not re.search(r"[\u4e00-\u9fff]", t):
        return False
    if "百分点" in t and _PERCENT_POINT_PHRASE_RE.search(t):
        return False
    if any(t.startswith(p) for p in _STATEMENT_LINE_LABEL_PREFIXES):
        return False
    if "计入" in t and ("综合收益" in t or "当期损益" in t):
        return False
    return any(h in t for h in _REASON_COLUMN_ACTION_HINTS)


def split_percent_point_change_text(text: str) -> tuple[str, str] | None:
    """数值% 与「上升/下降…个百分点」跨列粘连 → (上年数值%, 增减文字)。

    例：「18.78% 下降 0.97 个百分点」。与变化原因「-41.49%代理业务…」分流。
    """
    t = str(text or "").strip()
    if not t or "百分点" not in t:
        return None
    m = _PERCENT_POINT_CHANGE_RE.match(t)
    if not m:
        return None
    pct, change = m.group(1).strip(), m.group(2).strip()
    if not pct.endswith("%"):
        return None
    if not _PERCENT_POINT_PHRASE_RE.search(change):
        return None
    # 末尾若还粘着下一年数值%，只剥到百分点短语为止，留给其它规则
    trail = re.search(
        r"^((?:上升|下降|增加|减少|变动|提高|降低|扩大|收窄)"
        r"[\s　]*[\d,，.]+[\s　]*个百分点)\s+(-?[\d,，]+\.?\d*%)$",
        change,
    )
    if trail:
        change = trail.group(1).strip()
    return pct, change


def is_percent_point_change_glued_text(text: str) -> bool:
    return split_percent_point_change_text(text) is not None


def looks_like_percent_point_change_phrase(text: str) -> bool:
    """是否为「下降 0.97 个百分点」类增减列文字（非变化原因长说明）。"""
    t = str(text or "").strip()
    if not t or "百分点" not in t:
        return False
    return bool(_PERCENT_POINT_PHRASE_RE.search(t))


def is_percent_glued_to_reason_text(text: str) -> bool:
    """百分比与说明粘连、应分列（非标签内合法含 % 文本）。"""
    return split_percent_trailing_text(text) is not None


def split_percent_trailing_text(text: str) -> tuple[str, str] | None:
    """百分比与变化原因粘连（如「-41.49%代理业务支出减少」「30.69% 拆放同业款项增加」）→ (百分比, 说明)。

    不拆：标签内嵌 %（适用1250%风险权重）、叙述句中 %、整数倍率+短名词短语、
    「数值% + …百分点」（见 split_percent_point_change_text）。
    """
    t = str(text or "").strip()
    if not t:
        return None
    # 百分点增减跨列粘连：不得走变化原因落列
    if split_percent_point_change_text(t) or looks_like_percent_point_change_phrase(t):
        return None
    m = _PERCENT_TRAILING_TEXT_RE.match(t)
    if not m:
        candidates = list(_PERCENT_TRAILING_TEXT_SEARCH_RE.finditer(t))
        m = None
        for cand in reversed(candidates):
            if cand.start() > 4:
                continue
            m = cand
            break
    if not m:
        return None
    if m.start() == 0 and _chinese_before_first_digit(t):
        return None
    pct, reason = m.group(1).strip(), m.group(2).strip()
    if len(reason) < 2:
        return None
    if looks_like_percent_point_change_phrase(reason):
        return None
    if not _percent_token_looks_like_table_change_metric(pct):
        return None
    if not _trailing_text_looks_like_change_reason(reason):
        return None
    return pct, reason


def peel_trailing_percent_reason(text: str) -> tuple[str, str, str] | None:
    """从超级格尾部剥离「百分比 + 说明」，返回 (前缀, 百分比, 说明)。"""
    t = str(text or "").strip()
    if not t:
        return None
    # 整串是百分点跨列粘连时，不按变化原因剥离
    if split_percent_point_change_text(t):
        return None
    candidates = list(_PERCENT_TRAILING_TEXT_SEARCH_RE.finditer(t))
    for cand in reversed(candidates):
        pct, reason = cand.group(1).strip(), cand.group(2).strip()
        if len(reason) < 2:
            continue
        if looks_like_percent_point_change_phrase(reason):
            continue
        if not _percent_token_looks_like_table_change_metric(pct):
            continue
        if not _trailing_text_looks_like_change_reason(reason):
            continue
        prefix = t[: cand.start()].strip()
        return prefix, pct, reason
    return None


def has_percent_glued_to_chinese_text(text: str) -> bool:
    """数值(%) 与中文说明粘在同一串（含 % 后有空格）。"""
    t = str(text or "").strip()
    if not t or "%" not in t or not re.search(r"[\u4e00-\u9fff]", t):
        return False
    if split_percent_point_change_text(t):
        return True
    if split_percent_trailing_text(t):
        return True
    return bool(_PERCENT_TRAILING_TEXT_SEARCH_RE.search(t))


def is_percent_text_merged_cell(text: str) -> bool:
    return is_percent_glued_to_reason_text(text)


_AMOUNT_PCT_REASON_CELL_RE = re.compile(
    r"^([\d,，]+)\s+(-?[\d,，]+\.?\d*%)\s*([\u4e00-\u9fff].+)$",
)
_PERCENT_AMOUNT_REASON_CELL_RE = re.compile(
    r"^(-?[\d,，]+\.?\d*%)\s+([\d,，]+)\s+([\u4e00-\u9fff].+)$",
)
_AMOUNT_PCT_PAIR_RE = re.compile(
    r"^([\d,，]+)\s+(-?[\d,，]+\.?\d*%)$",
)
_VALUE_TRAILING_TEXT_LABEL_RE = re.compile(
    r"^((?:\(-?[\d,，]+\)|-?[\d,，]+)|[-－—])\s+([\u4e00-\u9fff].+)$",
)
_ACCOUNTING_SUBJECT_MARKERS = (
    "资产", "负债", "投资", "权益", "收入", "费用", "准备", "公积", "股本",
    "存款", "借款", "款项", "损益", "科目", "工具", "衍生",
)


def _text_looks_like_trailing_column_label(text: str) -> bool:
    """末列文本标签（会计科目等），非变化原因说明。"""
    t = str(text or "").strip()
    if len(t) < 2 or "%" in t:
        return False
    if looks_like_change_reason_description_not_label(t):
        return False
    if split_percent_trailing_text(t):
        return False
    if not re.search(r"[\u4e00-\u9fff]", t):
        return False
    return any(m in t for m in _ACCOUNTING_SUBJECT_MARKERS)


def split_value_trailing_text_label(text: str) -> tuple[str, str] | None:
    """末列数值+文本粘连（如「5,780 交易性金融资产」「- 长期股权投资」）→ (数值, 文本)。"""
    t = str(text or "").strip()
    if not t:
        return None
    m = _VALUE_TRAILING_TEXT_LABEL_RE.match(t)
    if not m:
        return None
    val, label = m.group(1).strip(), m.group(2).strip()
    if not _text_looks_like_trailing_column_label(label):
        return None
    if val not in _DASH_VALUES and not is_numeric_data_cell(val):
        return None
    if "%" in val:
        return None
    return val, label


def is_value_text_merged_cell(text: str) -> bool:
    return split_value_trailing_text_label(text) is not None


def split_amount_percent_text(text: str) -> tuple[str, str] | None:
    """变化原因表：「2,150,203 31.90%」→ (上年金额, 增减幅度)。"""
    t = str(text or "").strip()
    if not t:
        return None
    m = _AMOUNT_PCT_PAIR_RE.match(t)
    if not m:
        return None
    amt, pct = m.group(1).strip(), m.group(2).strip()
    if not re.search(r"\d", amt):
        return None
    if not _percent_token_looks_like_table_change_metric(pct):
        return None
    return amt, pct


def split_amount_percent_reason_text(text: str) -> tuple[str, str, str] | None:
    """变化原因表：「2,150,203 31.90%待清算款项增加」→ (金额, 百分比, 说明)。"""
    t = str(text or "").strip()
    if not t:
        return None
    m = _AMOUNT_PCT_REASON_CELL_RE.match(t)
    if not m:
        return None
    amt, pct, reason = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    if len(reason) < 2:
        return None
    if not _percent_token_looks_like_table_change_metric(pct):
        return None
    if not re.search(r"\d", amt):
        return None
    return amt, pct, reason


def split_percent_amount_reason_text(text: str) -> tuple[str, str, str] | None:
    """变化原因表：「30.69% 68,823,341 拆放同业款项增加」→ (百分比, 金额, 说明)。"""
    t = str(text or "").strip()
    if not t:
        return None
    m = _PERCENT_AMOUNT_REASON_CELL_RE.match(t)
    if not m:
        return None
    pct, amt, reason = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    if len(reason) < 2:
        return None
    if not _percent_token_looks_like_table_change_metric(pct):
        return None
    if not _trailing_text_looks_like_change_reason(reason):
        return None
    if not re.search(r"\d", amt):
        return None
    return pct, amt, reason


def is_change_table_mixed_cell(text: str) -> bool:
    """单格混入金额 + 百分比 + 中文说明（或金额 + 百分比说明粘连）。"""
    t = str(text or "").strip()
    if not t:
        return False
    if split_amount_percent_reason_text(t):
        return True
    if split_percent_amount_reason_text(t):
        return True
    if split_amount_percent_text(t):
        return True
    if re.search(r"[\u4e00-\u9fff]", t) and "%" in t and re.search(r"[\d,，]{3,}", t):
        if split_percent_trailing_text(t):
            return True
    return False


def split_label_trailing_amount(text: str) -> tuple[str, str] | None:
    """长标签末尾粘连金额（如「电力…供应业 1,600,664」）→ (标签, 金额)。"""
    t = str(text or "").strip()
    if not t or not re.search(r"[\u4e00-\u9fff]", t):
        return None
    m = _LABEL_TRAILING_AMOUNT_RE.match(t)
    if not m:
        return None
    label, amount = m.group(1).strip(), m.group(2).strip()
    if len(label) < 4 or not is_numeric_data_cell(amount):
        return None
    return label, amount


def _strip_numeric_wrapper(text: str) -> str:
    t = str(text or "").strip().replace("，", ",")
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1].strip()
    if t.endswith("%"):
        t = t[:-1].strip()
    if t.startswith("-"):
        t = t[1:].strip()
    return t


def has_valid_thousand_separators(text: str) -> bool:
    """含千分位逗号时：除最左段外每段须恰为 3 位数字；无逗号则合法。"""
    t = _strip_numeric_wrapper(text)
    if not t or "," not in t:
        return True
    int_part = t.split(".", 1)[0]
    groups = [g for g in int_part.split(",") if g]
    if len(groups) < 2:
        return True
    if not groups[0].isdigit() or not (1 <= len(groups[0]) <= 3):
        return False
    return all(len(g) == 3 and g.isdigit() for g in groups[1:])


def split_numeric_tokens(text: str) -> List[str]:
    """格内疑似数值 token（按空白切开）。"""
    t = str(text or "").strip()
    if not t:
        return []
    out: List[str] = []
    for part in re.split(r"\s+", t):
        p = part.strip()
        if not p or re.search(r"[\u4e00-\u9fff]", p):
            continue
        if re.search(r"\d", p):
            out.append(p)
    return out


def _numeric_decimal_point_count(text: str) -> int:
    """财报表数值：逗号为千分位、点为小数点；统计格内小数点总数。"""
    tokens = split_numeric_tokens(text)
    if tokens:
        return sum(tok.replace("，", ",").count(".") for tok in tokens)
    blob = str(text or "").strip().replace("，", ",")
    if not blob or not re.search(r"\d", blob):
        return 0
    return blob.count(".")


def cell_has_dash_and_numeric(text: str) -> bool:
    """同格短横与数值共存 → 疑似两列合并。"""
    t = str(text or "").strip()
    if not t or t in _DASH_VALUES:
        return False
    if re.search(r"[\u4e00-\u9fff]", t):
        return False
    parts = [p.strip() for p in re.split(r"\s+", t) if p.strip()]
    if len(parts) < 2:
        return False
    has_dash = any(p in _DASH_VALUES for p in parts)
    has_num = any(
        p not in _DASH_VALUES and re.search(r"\d", p)
        for p in parts
    )
    return has_dash and has_num


def is_merged_numeric_cell(text: str) -> bool:
    """单格多值、千分位非法、≥2 小数点、短横+数值 → 疑似相邻列被合并。"""
    t = str(text or "").strip()
    if not t or t in _DASH_VALUES:
        return False
    if is_report_date_cell(t) or is_year_cell(t) or is_month_day_cell(t):
        return False
    if re.search(r"[\u4e00-\u9fff]", t):
        return False
    if cell_has_dash_and_numeric(t):
        return True
    tokens = split_numeric_tokens(t)
    if len(tokens) >= 2:
        return True
    if _numeric_decimal_point_count(t) >= 2:
        return True
    if len(tokens) == 1:
        tok = tokens[0].replace("，", ",")
        if "," in tok and not has_valid_thousand_separators(tok):
            return True
    return False


def is_year_cell(text: str) -> bool:
    return bool(_YEAR_CELL_RE.match(str(text or "").strip()))


def is_month_day_cell(text: str) -> bool:
    return bool(_MONTH_DAY_CELL_RE.match(str(text or "").strip()))


def is_report_date_cell(text: str) -> bool:
    """报告期日期格：2024年、2024年12月31日、9月30日等；不属于数值。"""
    t = str(text or "").strip()
    if not t:
        return False
    if is_year_cell(t) or is_month_day_cell(t):
        return True
    if _REPORT_DATE_CELL_RE.match(t):
        return True
    if re.match(r"^[\s　]*(?:19|20)\d{2}\s*年", t) and "月" in t:
        return True
    return False


def contains_numeric_data(text: str) -> bool:
    """格内是否含表体数值（含疑似合并格，用于行检测）。"""
    t = str(text or "").strip()
    if not t:
        return False
    if t in _DASH_VALUES:
        return True
    if is_report_date_cell(t) or is_year_cell(t) or is_month_day_cell(t):
        return False
    if re.search(r"[\u4e00-\u9fff]", t):
        return False
    if is_numeric_data_cell(t):
        return True
    return bool(split_numeric_tokens(t))


def is_numeric_data_cell(text: str) -> bool:
    """表体值列：单格单值、千分位合法；括号负数、百分数、短横。"""
    t = str(text or "").strip()
    if not t:
        return False
    if t in _DASH_VALUES:
        return True
    if is_report_date_cell(t) or is_year_cell(t) or is_month_day_cell(t):
        return False
    if re.search(r"[\u4e00-\u9fff]", t):
        return False
    if not re.search(r"\d", t):
        return False
    if is_merged_numeric_cell(t):
        return False
    normalized = t.replace("，", ",").replace(" ", "")
    if _NUMERIC_DATA_RE.match(normalized):
        return has_valid_thousand_separators(normalized)
    compact = re.sub(r"\s+", "", t)
    if _NUMERIC_DATA_RE.match(compact.replace("，", ",")):
        return has_valid_thousand_separators(compact)
    return False
