# -*- coding: utf-8 -*-
"""字块净化：CJK 单字合并 + 金额文本粘连拆核。"""

from __future__ import annotations

import re
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from codes.reconstruct.grid_nucleus.types import Nucleus

_CJK_RE = re.compile(r"^[\u4e00-\u9fff]$")
_AMOUNT_HINT_RE = re.compile(
    r"[\d,]{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d{4,}(?:\.\d+)?"
    r"|\d+\.\d{2}\b"
)
# 违约概率区间等：左对齐文本标签，不是右对齐金额
_INTERVAL_LABEL_RE = re.compile(
    r"^[\(\[]\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*[\)\]]"
)


def is_interval_label(text: str) -> bool:
    """如 [0.00,0.15) / (0,1] —— 分档标签，应按文本左缘聚列。"""
    t = str(text or "").strip().replace(" ", "")
    if not t:
        return False
    # 会计负数 (454,022) 不是违约概率区间
    if re.match(r"^\(\d{1,3}(?:,\d{3})+(?:\.\d+)?\)$", t):
        return False
    if re.match(r"^\(\d+\)$", t):
        return False
    return bool(_INTERVAL_LABEL_RE.match(t))


def is_code_like_text(text: str) -> bool:
    """列码/「代码」列：窄标签，不得与金额右缘并槽。"""
    t = str(text or "").strip()
    if not t:
        return False
    if t in {"代码", "code", "Code", "CODE"}:
        return True
    # a / b / c / b c 等列码（必须 ASCII，避免「期间」「资产」）
    compact = t.replace(" ", "")
    if 1 <= len(compact) <= 3 and compact.isascii() and compact.isalpha():
        return True
    return False


def _is_amount_like(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if is_interval_label(t):
        return False
    # 年份/日期表头（2024 年、2024年12月31日）不当金额
    if "年" in t or "月" in t:
        return False
    # 会计括号负数
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1].strip()
    return bool(_AMOUNT_HINT_RE.search(t.replace(" ", "")))


def words_to_nuclei(words: Sequence[Dict[str, Any]]) -> List[Nucleus]:
    out: List[Nucleus] = []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        # 上游偶发「75 .21」整段带空格
        text = re.sub(r"(\d)\s+(\.\d)", r"\1\2", text)
        try:
            x0 = float(w.get("x0", 0))
            y0 = float(w.get("y0", 0))
            x1 = float(w.get("x1", x0))
            y1 = float(w.get("y1", y0))
        except (TypeError, ValueError):
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        # 噪声：零面积
        if (x1 - x0) * (y1 - y0) <= 0 and len(text) > 0:
            x1 = x0 + max(2.0, len(text) * 4.0)
            y1 = y0 + 8.0
        out.append(Nucleus(text=text, x0=x0, y0=y0, x1=x1, y1=y1))
    return out


def merge_cjk_singles(nuclei: List[Nucleus], *, y_tol: Optional[float] = None) -> List[Nucleus]:
    if not nuclei:
        return []
    heights = [n.height for n in nuclei if n.height > 0]
    h_med = median(heights) if heights else 10.0
    tol = y_tol if y_tol is not None else max(2.0, h_med * 0.35)
    widths = [n.width for n in nuclei if n.width > 0]
    w_med = median(widths) if widths else 8.0
    gap_max = max(2.0, w_med * 0.45)

    ordered = sorted(nuclei, key=lambda n: (round(n.cy / max(tol, 1e-6)), n.x0))
    merged: List[Nucleus] = []
    buf: Optional[Nucleus] = None

    def flush():
        nonlocal buf
        if buf is not None:
            merged.append(buf)
            buf = None

    for n in ordered:
        if not _CJK_RE.match(n.text):
            flush()
            merged.append(n)
            continue
        if buf is None:
            buf = Nucleus(
                text=n.text, x0=n.x0, y0=n.y0, x1=n.x1, y1=n.y1,
                flags=set(n.flags) | {"cjk_merged"},
            )
            continue
        same_line = abs(buf.cy - n.cy) <= tol
        close = n.x0 - buf.x1 <= gap_max
        if same_line and close and _CJK_RE.match(n.text):
            buf.text += n.text
            buf.x1 = max(buf.x1, n.x1)
            buf.y0 = min(buf.y0, n.y0)
            buf.y1 = max(buf.y1, n.y1)
            buf.flags.add("cjk_merged")
        else:
            flush()
            buf = Nucleus(
                text=n.text, x0=n.x0, y0=n.y0, x1=n.x1, y1=n.y1,
                flags=set(n.flags) | {"cjk_merged"},
            )
    flush()
    return merged


_DECIMAL_SUFFIX_RE = re.compile(r"^\.\d+\D?$")


def _is_integer_amount_part(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    clean = t.replace(",", "")
    return bool(re.match(r"^-?\d+$", clean))


def merge_split_decimal_nuclei(nuclei: List[Nucleus]) -> List[Nucleus]:
    """合并 PDF 拆开的小数：75 + .21 → 75.21（避免同格空格拼成「75 .21」）。"""
    if len(nuclei) < 2:
        return nuclei

    ordered = sorted(nuclei, key=lambda n: (round(n.cy, 1), n.x0))
    used: set = set()
    pairs: List[Nucleus] = []

    for i, n in enumerate(ordered):
        if i in used:
            continue
        text = str(n.text or "").strip()
        if not _DECIMAL_SUFFIX_RE.match(text):
            continue
        best_j = None
        best_gap = float("inf")
        for j in range(i - 1, -1, -1):
            if j in used:
                continue
            left = ordered[j]
            if abs(left.cy - n.cy) > max(3.0, max(n.height, left.height) * 0.6):
                break
            if not _is_integer_amount_part(left.text):
                continue
            if left.x1 > n.x0 + 2.0:
                continue
            gap = max(n.x0 - left.x1, 0.0)
            if gap > max(left.width * 1.5, 15.0):
                continue
            if gap < best_gap:
                best_gap = gap
                best_j = j
        if best_j is None:
            continue
        left = ordered[best_j]
        used.add(best_j)
        used.add(i)
        pairs.append(
            Nucleus(
                text=str(left.text).strip() + text,
                x0=left.x0,
                y0=min(left.y0, n.y0),
                x1=max(left.x1, n.x1),
                y1=max(left.y1, n.y1),
                flags=set(left.flags) | set(n.flags) | {"decimal_merged"},
            )
        )

    if not used:
        return nuclei
    out = [n for i, n in enumerate(ordered) if i not in used]
    out.extend(pairs)
    out.sort(key=lambda n: (round(n.cy, 1), n.x0))
    return out


def merge_currency_amount_nuclei(nuclei: List[Nucleus]) -> List[Nucleus]:
    """近距「人民币」+金额字框合并为同一凝结核。

    仅当中间无明显空白间隔时合并；大空隙留给分列（可能是两列）。
    """
    if len(nuclei) < 2:
        return nuclei

    try:
        from codes.v2_steps.table_anomaly_rules import _CURRENCY_UNIT_RE
    except Exception:
        return nuclei

    heights = [n.height for n in nuclei if n.height > 0]
    h_med = median(heights) if heights else 10.0
    y_tol = max(2.0, h_med * 0.35)
    widths = [n.width for n in nuclei if n.width > 0]
    w_med = median(widths) if widths else 8.0
    # 连续：间隙不超过约半个字宽；明显列间距不并
    gap_max = max(3.0, w_med * 0.55)

    ordered = sorted(nuclei, key=lambda n: (round(n.cy / max(y_tol, 1e-6)), n.x0))
    out: List[Nucleus] = []
    i = 0
    while i < len(ordered):
        n = ordered[i]
        if i + 1 >= len(ordered):
            out.append(n)
            break
        nxt = ordered[i + 1]
        same_line = abs(n.cy - nxt.cy) <= y_tol
        gap = nxt.x0 - n.x1
        contiguous = same_line and 0 <= gap <= gap_max
        left_t = str(n.text or "").strip()
        right_t = str(nxt.text or "").strip()
        currency_then_amt = (
            contiguous
            and bool(_CURRENCY_UNIT_RE.match(left_t))
            and _is_amount_like(right_t)
            and left_t != "元"  # 单独「元」后缀少见且易误并
        )
        amt_then_currency = (
            contiguous
            and _is_amount_like(left_t)
            and bool(_CURRENCY_UNIT_RE.match(right_t))
        )
        if currency_then_amt or amt_then_currency:
            joined = left_t + right_t
            out.append(
                Nucleus(
                    text=joined,
                    x0=n.x0,
                    y0=min(n.y0, nxt.y0),
                    x1=max(n.x1, nxt.x1),
                    y1=max(n.y1, nxt.y1),
                    flags=set(n.flags) | set(nxt.flags) | {"currency_amount_merged"},
                )
            )
            i += 2
            continue
        out.append(n)
        i += 1
    return out


def split_glue_nuclei(nuclei: List[Nucleus]) -> List[Nucleus]:
    try:
        from codes.v2_steps.table_glue_repair import split_glue_cell
    except Exception:
        split_glue_cell = None  # type: ignore

    out: List[Nucleus] = []
    for n in nuclei:
        if split_glue_cell is None:
            out.append(n)
            continue
        parts = split_glue_cell(n.text)
        if not parts:
            out.append(n)
            continue
        left_t, right_t = parts
        # 启发式：标签在左、金额在右（与 glue 模块一致）
        w = max(n.width, 4.0)
        # 按字符长度比例切
        ln = max(len(left_t), 1)
        rn = max(len(right_t), 1)
        split_x = n.x0 + w * (ln / (ln + rn))
        left = Nucleus(
            text=left_t, x0=n.x0, y0=n.y0, x1=split_x, y1=n.y1,
            flags=set(n.flags) | {"glued_split"},
        )
        right = Nucleus(
            text=right_t, x0=split_x, y0=n.y0, x1=n.x1, y1=n.y1,
            flags=set(n.flags) | {"glued_split"},
        )
        # 若右侧像金额，保持左右；若左侧像金额则对调几何但文本已按 glue 返回顺序
        out.extend([left, right])
    return out


def preprocess_words(words: Sequence[Dict[str, Any]]) -> List[Nucleus]:
    nuclei = words_to_nuclei(words)
    nuclei = merge_cjk_singles(nuclei)
    nuclei = merge_split_decimal_nuclei(nuclei)
    nuclei = merge_currency_amount_nuclei(nuclei)
    nuclei = split_glue_nuclei(nuclei)
    return nuclei


def is_amount_nucleus(n: Nucleus) -> bool:
    return _is_amount_like(n.text)


def is_code_nucleus(n: Nucleus) -> bool:
    return is_code_like_text(n.text)
