# -*- coding: utf-8 -*-
"""LLM/规则修复结果校验：禁止补造数值、结构合理性。"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, List, Optional, Sequence, Set, Tuple

_AMOUNT_TOKEN_RE = re.compile(
    r"[\(\uff08\-]?[\d,]{1,3}(?:,\d{3})+(?:\.\d+)?[%％]?[\)\uff09]?"
    r"|[\(\uff08\-]?\d+\.\d+[%％]?[\)\uff09]?"
    r"|[\(\uff08\-]?\d{4,}[%％]?[\)\uff09]?"
)

# 允许的「非新增」占位
_PLACEHOLDERS = frozenset({"", "-", "－", "—", "–", "N/A", "n/a", "NA", "— —"})


# 章节号（目录 4.1 / 6.2.1）不当金额；真金额通常有千分位或整数部分更长
_SECTION_TOKEN_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2}){1,3}$")
# 孤立年份（表头「2024」）不当金额，避免网格重切时误报补造
_YEAR_TOKEN_RE = re.compile(r"^(?:19|20)\d{2}$")


def _is_amount_like_token(tok: str) -> bool:
    """过滤章节号等假金额，避免目录修复误报「补造」。"""
    t = (tok or "").strip().replace(",", "")
    if not t:
        return False
    if _SECTION_TOKEN_RE.match(t):
        return False
    if _YEAR_TOKEN_RE.match(t):
        return False
    return True


def extract_amount_tokens(data: Sequence[Sequence[Any]]) -> Counter:
    """从表网格提取金额类 token 多重集合。"""
    counts: Counter = Counter()
    for row in data or []:
        for cell in row or []:
            text = str(cell or "").strip()
            if not text or text in _PLACEHOLDERS:
                continue
            for m in _AMOUNT_TOKEN_RE.finditer(text):
                tok = m.group(0).replace(" ", "")
                # 规范化：去千分位逗号便于比较，但保留括号与负号
                key = tok.replace(",", "")
                if not _is_amount_like_token(key):
                    continue
                counts[key] += 1
    return counts


def extract_amount_tokens_from_texts(texts: Sequence[Any]) -> Counter:
    """从一维文本（如 liteparse 字框）提取金额 token。"""
    counts: Counter = Counter()
    for cell in texts or []:
        text = str(cell or "").strip()
        if not text or text in _PLACEHOLDERS:
            continue
        for m in _AMOUNT_TOKEN_RE.finditer(text):
            key = m.group(0).replace(" ", "").replace(",", "")
            if not _is_amount_like_token(key):
                continue
            counts[key] += 1
    return counts


def amounts_invented(
    before: Sequence[Sequence[Any]],
    after: Sequence[Sequence[Any]],
) -> List[str]:
    """返回 after 相对 before 多出来的金额 token（疑似补造）。"""
    b = extract_amount_tokens(before)
    a = extract_amount_tokens(after)
    invented: List[str] = []
    for tok, cnt in a.items():
        if cnt > b.get(tok, 0):
            invented.append(tok)
    return invented


def amounts_not_in_source(
    after: Sequence[Sequence[Any]],
    source_texts: Sequence[Any],
) -> List[str]:
    """after 中相对字框源多出的金额（网格恢复应用此门禁，勿对照破损旧表）。"""
    src = extract_amount_tokens_from_texts(source_texts)
    a = extract_amount_tokens(after)
    missing: List[str] = []
    for tok, cnt in a.items():
        if cnt > src.get(tok, 0):
            missing.append(tok)
    return missing


def validate_repair(
    before: Sequence[Sequence[Any]],
    after: Sequence[Sequence[Any]],
    *,
    min_confidence: float = 0.55,
    confidence: Optional[float] = None,
    allow_empty: bool = False,
) -> Tuple[bool, List[str]]:
    """校验修复结果。返回 (通过, 失败原因列表)。"""
    reasons: List[str] = []
    if not after:
        if allow_empty:
            return True, []
        return False, ["修复结果为空表"]

    if not isinstance(after[0], (list, tuple)):
        return False, ["修复结果不是二维表"]

    n_cols = max((len(r) for r in after), default=0)
    if n_cols < 1:
        return False, ["修复后无有效列"]

    # 行数异常膨胀（疑似胡编）
    if before and len(after) > max(len(before) * 3, len(before) + 30):
        reasons.append(
            f"行数异常膨胀 {len(before)}→{len(after)}"
        )

    invented = amounts_invented(before, after)
    if invented:
        sample = ", ".join(invented[:8])
        reasons.append(f"疑似补造数值: {sample}")

    if confidence is not None and confidence < min_confidence:
        reasons.append(f"置信度过低 {confidence:.2f} < {min_confidence}")

    return (len(reasons) == 0), reasons


def normalize_grid(data: Sequence[Sequence[Any]]) -> List[List[str]]:
    out: List[List[str]] = []
    for row in data or []:
        out.append([
            "" if c is None else str(c).strip()
            for c in (row if isinstance(row, (list, tuple)) else [row])
        ])
    return out
