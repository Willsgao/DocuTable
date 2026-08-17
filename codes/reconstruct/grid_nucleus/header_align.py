# -*- coding: utf-8 -*-
"""表头列与数据主体列对齐。

硬规则（用户约定）：
  - 数据列与表头列应一一对齐；
  - **至少最底层表头**（body 上一行）与数据列一一对应；
  - 合并单元格（跨多列的单一表头）可以例外；
  - **期限分档并列短表头**（无期限|<6个月|6-12个月|≥1年）凝结核已分列，
    禁止并进一格（会破坏左右阅读序）。

常见误切：长表头左对齐、金额右对齐 → 表头落在空列、金额在右侧邻列。
做法：以金额数据列为真，把「无金额数据的表头列」并入最近金额列（分档表头除外）；
再强制检查最底层表头。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from codes.reconstruct.grid_nucleus.preprocess import is_amount_nucleus
from codes.reconstruct.grid_nucleus.types import Nucleus

_DATE_RE = re.compile(
    r"\d{4}\s*年"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*日?"
    r"|\d{4}\s*/\s*\d{1,2}"
    r"|20\d{2}\.\d{1,2}"
)
# 期限分档并列短表头：凝结核已分列，禁止 header_align 并进一格（会乱序）
_PERIOD_BUCKET_HEADER_RE = re.compile(
    r"^(?:无期限|< ?6个?月|6[-–—]?12个?月|≥ ?1年|≧ ?1年|"
    r"实时偿还|即期|过夜|≤ ?3个?月|≤ ?1年)$"
)
# 注释列：窄列 + (i)/(ii) 脚注标记，不是「无金额的空头列」
_NOTE_COLUMN_HEADERS = frozenset({"注释", "附注"})
_FOOTNOTE_MARKER_RE = re.compile(r"^\([ivxlc]+\)$", re.I)


def _is_period_bucket_header(text: str) -> bool:
    """无期限 / <6个月 / 6-12个月 / ≥1年 等并列分档表头。"""
    t = str(text or "").strip().replace(" ", "")
    if not t:
        return False
    # 去掉跨格标记再判
    t = re.sub(r"⟦[^⟧]*⟧", "", t).strip()
    return bool(_PERIOD_BUCKET_HEADER_RE.match(t))


def _is_note_column_header(text: str) -> bool:
    """「注释」列头：凝结核已独立成列，禁止并进不计息/人民币等金额列。"""
    t = str(text or "").strip()
    if not t:
        return False
    t = re.sub(r"⟦[^⟧]*⟧", "", t).strip()
    return t in _NOTE_COLUMN_HEADERS


def _is_footnote_marker_text(text: str) -> bool:
    return bool(_FOOTNOTE_MARKER_RE.match(str(text or "").strip()))


def _cell(row: Sequence[Any], c: int) -> str:
    if c < 0 or c >= len(row):
        return ""
    return str(row[c] or "").strip()


def _is_amount(text: str) -> bool:
    if not text or text in {"-", "－", "—", "–"}:
        return False
    if "年" in text and ("月" in text or "日" in text):
        return False
    if re.search(r"\d{1,2}\s*月", text or ""):
        return False
    t = text.replace(",", "").strip()
    if len(t) == 4 and t.isdigit() and t.startswith(("19", "20")):
        return False
    # 2025（注）/2025(注) 是年度标签，不是金额
    if re.match(r"^(?:19|20)\d{2}\s*[（(]注[）)]?$", t):
        return False
    if re.match(r"^(?:19|20)\d{2}\s*年?", t) and "注" in t and len(t) <= 12:
        return False
    return is_amount_nucleus(Nucleus(text=text, x0=0, y0=0, x1=1, y1=1))


def _is_date_header(text: str) -> bool:
    return bool(_DATE_RE.search(text or ""))


_ROW_SERIAL_TOKEN_RE = re.compile(r"^(?:\d{1,3}[a-zA-Z]?|[a-zA-Z])$", re.I)


def _is_row_serial_token(text: str) -> bool:
    """第一列序号：纯数字 / 数字+字母 / 纯字母。"""
    t = str(text or "").strip()
    return bool(t and _ROW_SERIAL_TOKEN_RE.match(t))


def _detect_body_start(data: List[List[str]]) -> int:
    """首个数据行：优先足够金额，其次序号（避免「期间」行被当成 body 起点）。"""
    for i, row in enumerate(data):
        if not isinstance(row, list):
            continue
        amts = sum(1 for c in range(len(row)) if _is_amount(_cell(row, c)))
        if amts >= 2:
            return i
    for i, row in enumerate(data):
        if not isinstance(row, list):
            continue
        first = _cell(row, 0)
        # 1 / 14a / a —— 字母序号也须认作表体起点，否则整行被当表头并列
        if _is_row_serial_token(first):
            return i
    return min(2, max(0, len(data) - 1))


def _row_period_hits(row: Sequence[Any], amt_cols: Sequence[int]) -> int:
    return sum(1 for c in amt_cols if _is_date_header(_cell(row, c)))


def _is_letter_code(text: str) -> bool:
    """列码 a/b/c；不含中文短词（资产/负债）。"""
    compact = (text or "").replace(" ", "")
    return 1 <= len(compact) <= 3 and compact.isascii() and compact.isalpha()


def _is_section_title_row(
    row: Sequence[Any],
    amt_cols: Sequence[int],
    label_or_serial: set,
) -> bool:
    """分组标题行：左侧有非期间标签，金额列全空（如「可用资本」「资产」）。"""
    if not amt_cols:
        return False
    if any(_cell(row, c) for c in amt_cols):
        return False
    for c in sorted(label_or_serial):
        txt = _cell(row, c)
        if txt and not _is_date_header(txt) and not _is_letter_code(txt):
            return True
    # 仅一格短分类词（资产/负债）也视为分组行
    filled = [(i, _cell(row, i)) for i in range(len(row)) if _cell(row, i)]
    if len(filled) == 1:
        _, txt = filled[0]
        if (
            1 <= len(txt) <= 8
            and not _is_date_header(txt)
            and not _is_amount(txt)
            and not _is_letter_code(txt)
        ):
            return True
    return False


def _is_fillable_bottom_header(text: str) -> bool:
    """允许向上补到最底表头的内容：期间、列码；禁止抄副标题（资产负债表）。"""
    t = (text or "").strip()
    if not t:
        return False
    if _is_date_header(t):
        return True
    if t in {"代码", "数额"}:
        return True
    if _is_letter_code(t):
        return True
    return False


def _resolve_bottom_header_row(
    out: List[List[str]],
    body_start: int,
    amt_cols: List[int],
    label_or_serial: set,
) -> int:
    """最底层「期间/列码」表头行：跳过分组标题，避免把报告期抄进分组行。"""
    if body_start <= 0:
        return -1
    cand = body_start - 1
    if cand < 0 or cand >= len(out):
        return -1
    if amt_cols and _is_section_title_row(out[cand], amt_cols, label_or_serial):
        for r in range(cand - 1, -1, -1):
            if _row_period_hits(out[r], amt_cols) >= max(1, (len(amt_cols) + 1) // 2):
                return r
            if not _is_section_title_row(out[r], amt_cols, label_or_serial):
                return r
    return cand


def _header_col_code(
    data: List[List[str]],
    col: int,
    body_start: int,
) -> str:
    """表头中的单字母列码（a–l），用于锁定第三支柱宽表列。"""
    for i in range(max(0, body_start)):
        if i >= len(data) or not isinstance(data[i], list):
            continue
        v = _cell(data[i], col)
        if not v:
            continue
        compact = v.replace(" ", "")
        if len(compact) == 1 and compact.isascii() and compact.isalpha():
            return compact.lower()
    return ""


def _body_fill_ok(b_fill: int, n_body: int) -> bool:
    return b_fill >= max(2, int(n_body * 0.25))


def _col_stats(
    data: List[List[str]],
    body_start: int,
) -> List[Dict[str, Any]]:
    n_cols = max((len(r) for r in data if isinstance(r, list)), default=0)
    n_body = max(1, len(data) - body_start)
    stats: List[Dict[str, Any]] = []
    for c in range(n_cols):
        h_fill = 0
        b_fill = 0
        b_amt = 0
        b_placeholder = 0  # "-" 等占位也算有数据列
        body_vals: List[str] = []
        for i, row in enumerate(data):
            if not isinstance(row, list):
                continue
            v = _cell(row, c)
            if not v:
                continue
            if i < body_start:
                h_fill += 1
            else:
                b_fill += 1
                body_vals.append(v)
                if _is_amount(v):
                    b_amt += 1
                elif v in {"-", "－", "—", "–", "n/a", "N/A"}:
                    b_placeholder += 1
        letter = _header_col_code(data, c, body_start)
        note_hdr = any(
            _is_note_column_header(_cell(data[i], c))
            for i in range(0, body_start)
            if isinstance(data[i], list)
        )
        footnote_marks = sum(1 for v in body_vals if _is_footnote_marker_text(v))
        # 代码列：body 多为单字母 c/d/e…
        code_like = [v for v in body_vals if _is_letter_code(v)]
        serial_like = [v for v in body_vals if _is_row_serial_token(v)]
        # 第 0 列：短数字/14a/字母行号 → 序号列（优先于列码，避免 a/b 行号被当代码列）
        is_serial = (
            c == 0
            and b_amt == 0
            and (
                len(serial_like) >= max(2, int(n_body * 0.5))
                or (
                    b_fill >= max(2, int(n_body * 0.5))
                    and all(_is_row_serial_token(v) for v in body_vals)
                )
            )
        )
        is_code = (
            not is_serial
            and (
                bool(letter)
                or (
                    b_amt == 0
                    and len(code_like) >= 2
                    and len(code_like) >= max(2, int(len(body_vals) * 0.6))
                )
            )
        )
        is_label = (
            c <= 2 and b_amt == 0 and not is_code and not is_serial
            and b_fill >= max(2, int(n_body * 0.3))
        )
        is_amt_col = b_amt >= 2
        is_note_col = note_hdr or footnote_marks >= 1
        is_body_col = (
            is_amt_col or is_code or is_note_col
            or _body_fill_ok(b_fill, n_body)
            or b_placeholder >= max(2, int(n_body * 0.25))
        )
        # 有表头列码 / 主体有占位或填充 → 绝不是「空头列」
        is_header_orphan = (
            h_fill > 0 and b_amt == 0 and not is_serial
            and not letter and not is_body_col and not is_code
            and not is_note_col
        )
        stats.append({
            "col": c,
            "header_fill": h_fill,
            "body_fill": b_fill,
            "body_amt": b_amt,
            "body_fill_ratio": b_fill / n_body,
            "header_code": letter,
            "is_serial": is_serial,
            "is_label": is_label and not is_serial,
            "is_code": is_code,
            "is_header_orphan": is_header_orphan,
            "is_amt_col": is_amt_col,
            "is_body_col": is_body_col,
            "is_note_col": is_note_col,
        })
    for s in stats:
        if (
            s["is_label"] or s["is_serial"] or s["is_code"]
            or s["is_amt_col"] or s["is_body_col"] or s.get("is_note_col")
        ):
            s["is_header_orphan"] = False
        if s.get("header_code"):
            s["is_header_orphan"] = False
    return stats


def _is_field_name_header(text: str) -> bool:
    """短字段名表头（指标/项目…），orphan 时应并入左侧科目列而非金额列。"""
    t = str(text or "").strip().replace(" ", "")
    if not t or len(t) > 8:
        return False
    if any(k in t for k in ("指标", "项目", "名称", "内容", "事项", "科目", "摘要")):
        return True
    # 纯 2～4 字中文短词
    if 2 <= len(t) <= 4 and re.fullmatch(r"[\u4e00-\u9fff]+", t):
        return True
    return False


def _nearest_label_col(src: int, stats: Sequence[Dict[str, Any]]) -> Optional[int]:
    """最近的非金额主体/标签列（优先左侧）。"""
    label_cols = [
        int(s["col"])
        for s in stats
        if (
            s.get("is_label")
            or s.get("is_serial")
            or (s.get("is_body_col") and not s.get("is_amt_col"))
        )
        and int(s["col"]) != src
    ]
    if not label_cols:
        return None
    left = [c for c in label_cols if c < src]
    if left:
        return max(left)
    return min(label_cols, key=lambda c: abs(c - src))


def _nearest_amt_col(src: int, amt_cols: List[int]) -> Optional[int]:
    if not amt_cols:
        return None
    right = [b for b in amt_cols if b > src]
    if right:
        return min(right, key=lambda b: b - src)
    left = [b for b in amt_cols if b < src]
    if left:
        return max(left)
    return min(amt_cols, key=lambda b: abs(b - src))


def _merge_cell(dst: str, src: str) -> str:
    ds, ss = (dst or "").strip(), (src or "").strip()
    if not ss:
        return dst or ""
    if not ds:
        return ss
    if ss in ds:
        return ds
    if ds in ss:
        return ss
    return f"{ds} {ss}".strip()


def _pull_amounts_into_letter_cols(
    out: List[List[str]],
    body_start: int,
    stats: List[Dict[str, Any]],
) -> List[str]:
    """列码列无金额时，将右侧紧邻的无列码金额列并入（KM1: a|金额 → 同列）。

    日期等非金额填充不阻止拉取；无列码的普通标签列不拉取（避免「项目」吞金额）。
    """
    actions: List[str] = []
    n_cols = max((len(r) for r in out), default=0)
    for s in stats:
        letter = str(s.get("header_code") or "")
        if not letter:
            continue
        c = int(s["col"])
        if s.get("body_amt", 0) > 0:
            continue
        tgt = c + 1
        if tgt >= n_cols:
            continue
        if _header_col_code(out, tgt, body_start):
            continue
        has_amt = any(
            _is_amount(_cell(out[i], tgt))
            for i in range(body_start, len(out))
            if isinstance(out[i], list)
        )
        if not has_amt:
            continue
        for i in range(len(out)):
            while len(out[i]) < n_cols:
                out[i].append("")
            src = _cell(out[i], tgt)
            if not src:
                continue
            out[i][c] = _merge_cell(_cell(out[i], c), src)
            out[i][tgt] = ""
        actions.append(f"letter_pull:{tgt}->{c}")
    return actions


def _prune_empty(
    out: List[List[str]],
    col_lines: Optional[List[float]],
    n_cols: int,
) -> Tuple[List[List[str]], List[float]]:
    try:
        from codes.reconstruct.grid_nucleus.span_mark import is_span_cover_mark
    except Exception:
        def is_span_cover_mark(text: str) -> bool:  # type: ignore
            return False

    def _real(r: Sequence[Any], c: int) -> bool:
        v = _cell(r, c)
        if not v:
            return False
        if is_span_cover_mark(v):
            return False
        return True

    keep = [c for c in range(n_cols) if any(_real(r, c) for r in out)]
    if len(keep) < 2 or len(keep) == n_cols:
        return out, list(col_lines or [])
    new_data = [[_cell(r, c) for c in keep] for r in out]
    new_lines: List[float] = []
    if col_lines and len(col_lines) >= n_cols + 1:
        new_lines = [col_lines[keep[0]]]
        for c in keep:
            new_lines.append(col_lines[min(c + 1, len(col_lines) - 1)])
        mono = [new_lines[0]]
        for x in new_lines[1:]:
            if x <= mono[-1]:
                x = mono[-1] + 1.0
            mono.append(x)
        new_lines = mono
    return new_data, new_lines


def _is_change_rate_header(text: str) -> bool:
    """增减/同比等独立列，禁止因主体暂空而并入左邻金额列。"""
    t = str(text or "").strip().replace(" ", "")
    if not t:
        return False
    return any(k in t for k in ("增减", "同比", "环比", "变动幅度", "变动比例"))


def _should_keep_as_span(
    orphan: int,
    amt_cols: List[int],
    header_text: str,
) -> bool:
    """合并单元格例外：夹在两金额列之间的**单一跨列标题**。

    注意：期间日期（2024年12月31日）要与金额列一一对应，**不算**合并例外。
    副标题「资产负债表」在两列下各出现一次，也不是跨列合并格。
    """
    joined = (header_text or "").strip()
    if not joined:
        return False
    # 增减(%) 等是独立数据列，即使某次主体落列失败也不得并进左列造成「337,488 0.01」
    if _is_change_rate_header(joined):
        return True
    # 短标签 / 期间日期 → 必须落列
    if len(joined) <= 2 or joined.lower() in {"a", "b", "c", "d", "e", "代码"}:
        return False
    if _is_date_header(joined):
        return False
    # 各金额列下重复的副标题（资产负债表资产负债表）→ 应随列对齐，勿当跨列 span
    if "资产负债表" in joined.replace(" ", ""):
        return False
    left_b = [b for b in amt_cols if b < orphan]
    right_b = [b for b in amt_cols if b > orphan]
    if not (left_b and right_b):
        return False
    if (orphan - max(left_b) != 1) or (min(right_b) - orphan != 1):
        return False
    # 很长的单一跨列标题才保留
    return len(joined) >= 12


def _enforce_bottom_header_one_to_one(
    out: List[List[str]],
    body_start: int,
    amt_cols: List[int],
    stats: List[Dict[str, Any]],
) -> List[str]:
    """强制：最底层表头与金额数据列一一对齐。

    - 金额列在底层表头为空、邻列有字 → 拉过来
    - 非金额/非行列名列在底层有字 → 并入最近金额列
      （夹在两金额列的跨列日期/长标题除外）
    - **有主体数据的非金额列**（年度/转增占位列等）底层表头禁止并走
    - 分组标题行（可用资本…）不是表头，禁止从上抄报告期
    """
    actions: List[str] = []
    if body_start <= 0 or not amt_cols:
        return actions
    label_or_serial = {
        s["col"] for s in stats
        if s.get("is_label") or s.get("is_serial") or s.get("is_code")
    }
    # 主体非金额列（年度、占位「–」列、注释列）：底层单位/标签必须留在本列
    body_keep = {
        s["col"] for s in stats
        if (
            s.get("is_note_col")
            or (
                s.get("is_body_col")
                and not s.get("is_amt_col")
                and int(s.get("body_fill") or 0) >= 2
            )
        )
    }
    bottom = _resolve_bottom_header_row(
        out, body_start, amt_cols, label_or_serial,
    )
    if bottom < 0 or bottom >= len(out):
        return actions
    n_cols = max((len(r) for r in out), default=0)
    while len(out[bottom]) < n_cols:
        out[bottom].append("")

    # 底层表头中，不在金额列上的格子
    for c in range(n_cols):
        if c in amt_cols or c in label_or_serial or c in body_keep:
            continue
        txt = _cell(out[bottom], c)
        if not txt:
            continue
        if _should_keep_as_span(c, amt_cols, txt):
            actions.append(f"bottom_span_keep:{c}")
            continue
        # 单位/括号表头碎片：勿并入金额列（否则「年度」旁单位被吞）
        if _is_unit_header_text(txt):
            actions.append(f"bottom_unit_keep:{c}")
            continue
        # 期限分档并列短表头：禁止并进邻列（否则 ≥1年|<6|6-12 粘成乱序串）
        if _is_period_bucket_header(txt):
            actions.append(f"bottom_period_keep:{c}")
            continue
        # 注释列：禁止并进不计息/人民币等金额列（否则「不计息 注释」且顺序颠倒）
        if _is_note_column_header(txt):
            actions.append(f"bottom_note_keep:{c}")
            continue
        tgt = _nearest_amt_col(c, amt_cols)
        if tgt is None or tgt == c:
            continue
        # 目标格已是分档表头：禁止把另一分档并进去
        if _is_period_bucket_header(_cell(out[bottom], tgt)):
            actions.append(f"bottom_period_tgt_keep:{c}")
            continue
        if _is_note_column_header(_cell(out[bottom], tgt)):
            actions.append(f"bottom_note_tgt_keep:{c}")
            continue
        out[bottom][tgt] = _merge_cell(_cell(out[bottom], tgt), txt)
        out[bottom][c] = ""
        actions.append(f"bottom:{c}->{tgt}")

    # 金额列底层仍空：仅在「真表头行」向上同列补；分组行禁止抄报告期
    if _is_section_title_row(out[bottom], amt_cols, label_or_serial):
        actions.append(f"bottom_skip_section:{bottom}")
        return actions
    for ac in amt_cols:
        if _cell(out[bottom], ac):
            continue
        for r in range(bottom - 1, -1, -1):
            v = _cell(out[r], ac)
            if not v or not _is_fillable_bottom_header(v):
                continue
            # 上方同列已有相同内容 → 再抄会重复（如 a/b/c 列码），底层留空
            if _same_fillable_already_above(out, bottom, ac, v):
                actions.append(f"bottom_skip_dup_above:{ac}")
                break
            out[bottom][ac] = v
            actions.append(f"bottom_fill_from_above:{ac}")
            break
    return actions


def _same_fillable_already_above(
    data: List[List[str]],
    bottom: int,
    col: int,
    value: str,
) -> bool:
    """同列上方表头已出现过相同可填充内容（再抄即重复）。"""
    target = (value or "").strip()
    if not target:
        return False
    for r in range(0, max(0, bottom)):
        if r >= len(data) or not isinstance(data[r], list):
            continue
        if _cell(data[r], col) == target:
            return True
    return False


_UNIT_HEADER_RE = re.compile(
    r"^[（(][^）)]{1,16}[）)]$"
)
_MULTI_UNIT_RE = re.compile(
    r"[（(][^）)]{1,16}[）)]"
)


def _is_unit_header_text(text: str) -> bool:
    """（股）/（人民币元）/(%) 等单位表头。"""
    t = str(text or "").strip()
    if not t:
        return False
    if _UNIT_HEADER_RE.match(t):
        return True
    # 多个单位粘在一格
    parts = _MULTI_UNIT_RE.findall(t)
    if len(parts) >= 2 and not re.sub(r"[（(][^）)]+[）)]|\s+", "", t):
        return True
    return False


def _split_multi_unit_header(text: str) -> Optional[List[str]]:
    """（股） （人民币百万元） （人民币百万元） → 各单位片段。"""
    t = str(text or "").strip()
    if not t:
        return None
    parts = _MULTI_UNIT_RE.findall(t)
    if len(parts) < 2:
        return None
    rest = _MULTI_UNIT_RE.sub("", t).strip()
    if rest:
        return None
    return [p.strip() for p in parts if p.strip()]


def _spill_multi_unit_headers_to_empty_cols(
    data: List[List[str]],
    *,
    body_start: int,
    amt_cols: Sequence[int],
) -> List[str]:
    """底层多单位粘连拆入右侧空列（转增数列 bbox 常只盖第一个（股））。"""
    actions: List[str] = []
    if body_start < 1 or not data:
        return actions
    bottom = body_start - 1
    if bottom < 0 or bottom >= len(data):
        return actions
    row = data[bottom]
    n = len(row)
    amt_set = set(amt_cols)
    for c in range(n):
        parts = _split_multi_unit_header(_cell(row, c))
        if not parts or len(parts) < 2:
            continue
        # 需要右侧连续空列容纳剩余单位
        need = len(parts) - 1
        if c + need >= n:
            continue
        if any(_cell(row, c + i) for i in range(1, need + 1)):
            continue
        # 目标列须有主体（金额或占位），避免拆进纯空列
        ok_targets = True
        for i in range(need + 1):
            tc = c + i
            has_body = any(
                str(
                    (data[r][tc] if tc < len(data[r]) else "") or ""
                ).strip()
                for r in range(body_start, len(data))
                if isinstance(data[r], list)
            )
            if not has_body and tc not in amt_set:
                ok_targets = False
                break
        if not ok_targets:
            continue
        for i, part in enumerate(parts):
            row[c + i] = part
        actions.append(
            f"multi_unit_spill:{c}:" + "|".join(parts)
        )
    return actions


def _split_dual_metric_header(text: str) -> Optional[Tuple[str, str]]:
    """双指标折行表头粘连：贷款金额 + 贷款率%（注）。"""
    try:
        from codes.v2_steps.table_glue_repair import split_glue_cell
    except Exception:
        return None
    parts = split_glue_cell(text)
    if not parts:
        return None
    left, right = parts
    # 仅处理「…金额 + …率%」类，避免误拆地区|营业收入
    if "金额" not in left:
        return None
    if "率" not in right and "比率" not in right:
        return None
    return left, right


def _spill_glued_dual_headers_to_empty_amt_cols(
    data: List[List[str]],
    *,
    body_start: int,
    amt_cols: Sequence[int],
) -> List[str]:
    """底层表头「贷款金额 贷款率%…」粘在左列、右金额列空 → 拆入右列。

    liteparse 常把两列表头粘成一字框且 bbox 只盖左列；主体金额列仍正确，
    导致上一行「不良|不良」与下一行错位（用户所见交叉却拆开）。
    """
    actions: List[str] = []
    if body_start < 1 or not data or not amt_cols:
        return actions
    bottom = body_start - 1
    if bottom < 0 or bottom >= len(data):
        return actions
    row = data[bottom]
    amt_set = set(amt_cols)
    n = len(row)
    for c in range(n - 1):
        cell = _cell(row, c)
        parts = _split_dual_metric_header(cell)
        if not parts:
            continue
        if _cell(row, c + 1):
            continue
        # 右邻须为金额列（或主体确有金额）
        right_amt = c + 1 in amt_set
        if not right_amt:
            right_amt = any(
                _is_amount(_cell(data[r], c + 1))
                for r in range(body_start, len(data))
            )
        if not right_amt:
            continue
        left, right = parts
        row[c] = left
        row[c + 1] = right
        actions.append(f"dual_hdr_spill:{c}->{c + 1}:{left}|{right}")
    return actions


def align_header_to_body_columns(
    data: List[List[str]],
    *,
    col_lines: Optional[List[float]] = None,
) -> Tuple[List[List[str]], List[float], Dict[str, Any]]:
    """数据列真值 → 表头回挂；保证最底层表头与金额列一一对齐。

    次要后处理：不得为对齐表头而合并/删除凝结核已定的数据列
    （orphan 列保留给 span_mark 标注，不再并入邻列）。
    """
    metrics: Dict[str, Any] = {
        "header_align": False,
        "orphans": [],
        "merges": [],
        "bottom_actions": [],
        "rule": "bottom_header_1to1_with_amt_cols",
    }
    if not data or len(data) < 2:
        return data, list(col_lines or []), metrics

    body_start = _detect_body_start(data)
    metrics["body_start"] = body_start
    stats = _col_stats(data, body_start)
    amt_cols = [s["col"] for s in stats if s["is_amt_col"]]
    orphans = [s["col"] for s in stats if s["is_header_orphan"]]
    body_cols = [s["col"] for s in stats if s["is_body_col"]]
    targets = amt_cols if amt_cols else body_cols
    metrics["orphans"] = orphans
    metrics["amt_cols"] = amt_cols
    metrics["body_cols"] = body_cols

    out = [list(r) if isinstance(r, list) else [] for r in data]
    n_cols = max((len(r) for r in out), default=0)
    for r in out:
        while len(r) < n_cols:
            r.append("")

    # 先拆双指标粘连表头，再并 orphan（否则右列空会被误判）
    dual_spills = _spill_glued_dual_headers_to_empty_amt_cols(
        out, body_start=body_start, amt_cols=amt_cols or targets,
    )
    metrics["dual_header_spills"] = dual_spills
    unit_spills = _spill_multi_unit_headers_to_empty_cols(
        out, body_start=body_start, amt_cols=amt_cols or targets,
    )
    metrics["multi_unit_spills"] = unit_spills
    if dual_spills or unit_spills:
        metrics["header_align"] = True
        stats = _col_stats(out, body_start)
        amt_cols = [s["col"] for s in stats if s["is_amt_col"]]
        orphans = [s["col"] for s in stats if s["is_header_orphan"]]
        body_cols = [s["col"] for s in stats if s["is_body_col"]]
        targets = amt_cols if amt_cols else body_cols
        metrics["orphans"] = orphans
        metrics["amt_cols"] = amt_cols

    # 凝结核分列后不再并 orphan 列：跨列表头由 span_mark 左首格标注，勿把列结构拆掉
    keep_span: List[int] = list(orphans) if orphans else []
    merges: List[str] = []
    metrics["span_kept"] = keep_span
    metrics["merges"] = merges

    # 列码列（a–d / a–l）无主体时，把右侧「无列码的金额列」拉回来
    # （第三支柱宽表：列码即列；KM1：列码与金额被拆到相邻槽）
    pull_actions = _pull_amounts_into_letter_cols(out, body_start, stats)
    metrics["letter_pulls"] = pull_actions
    if pull_actions:
        metrics["header_align"] = True
        # 拉回后重算金额列
        stats = _col_stats(out, body_start)
        amt_cols = [s["col"] for s in stats if s["is_amt_col"]]

    # 最底层表头强制 1:1
    # 若刚合并过，用更新后的 stats 不划算；直接用当前 amt_cols
    if not amt_cols:
        # 重算
        stats2 = _col_stats(out, body_start)
        amt_cols = [s["col"] for s in stats2 if s["is_amt_col"]]
        stats = stats2
    bottom_actions = _enforce_bottom_header_one_to_one(
        out, body_start, amt_cols, stats,
    )
    metrics["bottom_actions"] = bottom_actions
    metrics["header_align"] = bool(
        merges or bottom_actions or metrics.get("header_align")
    )

    new_data, new_lines = _prune_empty(out, col_lines, n_cols)
    metrics["cols_before"] = n_cols
    metrics["cols_after"] = max((len(r) for r in new_data), default=0)

    # 校验：最底层「期间」表头 vs 金额列（跳过分组标题行）
    if body_start > 0 and amt_cols and new_data:
        stats_f = _col_stats(new_data, min(body_start, len(new_data) - 1))
        amt_f = [s["col"] for s in stats_f if s["is_amt_col"]]
        label_f = {
            s["col"] for s in stats_f
            if s.get("is_label") or s.get("is_serial") or s.get("is_code")
        }
        bottom = _resolve_bottom_header_row(new_data, body_start, amt_f, label_f)
        missing = [
            c for c in amt_f
            if bottom >= 0 and not _cell(new_data[bottom], c)
        ]
        metrics["bottom_header_row"] = bottom
        metrics["bottom_header_missing_amt_cols"] = missing
        metrics["bottom_header_ok"] = len(missing) == 0
    return new_data, new_lines, metrics
