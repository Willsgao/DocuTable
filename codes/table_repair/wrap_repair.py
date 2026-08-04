# -*- coding: utf-8 -*-
"""折行处理：真·标签续写可合并；层级父项与分项禁止合并，已粘连则拆开。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

_AMOUNT_RE = re.compile(r"[\d,]{3,}|\d+\.\d+|\(\s*[\d,]+\s*\)")
_NEW_SECTION_RE = re.compile(
    r"^[(（]?\d+[)）.．、]|^[一二三四五六七八九十]+[、.．]"
    r"|^\d{4}\s*年|^(注[：:]|合计|小计|总计)"
)
# 分项/叶子行前缀
_LEAF_PREFIX_RE = re.compile(r"^[\-－—–]\s*")
# 父项+「 - 叶子」粘连
_DASH_LEAF_RE = re.compile(
    r"^(?P<head>.+?)\s*[-－—–]\s*(?P<leaf>.+)$"
)

# 公允价值分层等常见完整科目（长→短），用于拆开无连字符的粘连
_CATEGORY_PHRASES: Tuple[str, ...] = tuple(
    sorted(
        (
            "以公允价值计量且其变动计入当期损益的金融资产",
            "以公允价值计量且其变动计入其他综合收益的金融资产",
            "其他以公允价值计量且其变动计入当期损益的金融资产",
            "持有作交易用途的金融资产",
            "指定为以公允价值计量且其变动计入其他综合收益的权益工具",
            "以公允价值计量且其变动计入当期损益的金融负债",
            "持续的公允价值计量",
            "非持续的公允价值计量",
            "衍生金融资产",
            "衍生金融负债",
            "发放贷款和垫款",
        ),
        key=len,
        reverse=True,
    )
)


def _row_amount_count(row: Sequence[Any]) -> int:
    return sum(1 for c in row if _AMOUNT_RE.search(str(c or "")))


def _looks_like_hierarchy_child(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if _LEAF_PREFIX_RE.match(t):
        return True
    # 完整子类目标题（非半句续写）
    if t in _CATEGORY_PHRASES:
        return True
    if t.endswith(("的金融资产", "的金融负债", "贷款和垫款")) and len(t) >= 8:
        return True
    return False


def _is_label_continuation(prev_label: str, cur_label: str) -> bool:
    """仅允许「同一标签被拆成两行」的续写，禁止层级子项并入父项。"""
    cur = str(cur_label or "").strip()
    prev = str(prev_label or "").strip()
    if not cur or len(cur) > 40:
        return False
    if _NEW_SECTION_RE.match(cur):
        return False
    if _AMOUNT_RE.search(cur):
        return False
    if _looks_like_hierarchy_child(cur):
        return False
    # 上行已是完整科目、下行又是另一完整科目 → 层级，不合并
    if any(p in prev for p in _CATEGORY_PHRASES) and _looks_like_hierarchy_child(cur):
        return False
    # 真续写：下行很短，且不像独立科目名
    if len(cur) <= 12 and not cur.endswith(("金融资产", "金融负债", "资产", "负债")):
        return True
    # 「的/及/与」等粘连前缀续写
    if cur.startswith(("的", "及", "与", "和", "或", "等")) and len(cur) <= 24:
        return True
    return False


def _split_head_into_categories(head: str) -> List[str]:
    """把无连字符粘在一起的多层科目标题拆开。"""
    s = str(head or "").strip()
    if not s:
        return []
    parts: List[str] = []
    rest = s
    while rest:
        matched = False
        for phrase in _CATEGORY_PHRASES:
            if rest.startswith(phrase):
                parts.append(phrase)
                rest = rest[len(phrase) :].strip()
                matched = True
                break
            # 短语在中间（前缀是已识别父级残留）
            idx = rest.find(phrase)
            if idx > 0:
                before = rest[:idx].strip()
                if before:
                    parts.append(before)
                parts.append(phrase)
                rest = rest[idx + len(phrase) :].strip()
                matched = True
                break
        if not matched:
            parts.append(rest)
            break
    # 去空、合并过短碎片到前一项
    cleaned: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if cleaned and len(p) <= 2 and not _LEAF_PREFIX_RE.match(p):
            cleaned[-1] = cleaned[-1] + p
        else:
            cleaned.append(p)
    return cleaned or [s]


def split_glued_hierarchy_label(label: str) -> Optional[List[str]]:
    """若标签把父级与首个分项粘在一起，返回拆开后的多级标签列表（末级为叶子）。"""
    s = str(label or "").strip()
    if not s or len(s) < 12:
        return None

    leaf = ""
    head = s
    m = _DASH_LEAF_RE.match(s)
    if m:
        head = m.group("head").strip()
        leaf = m.group("leaf").strip()
        # 「持有…金融资产 - 债券」才拆；避免误拆日期等
        if len(leaf) > 24 and "债券" not in leaf and "权益" not in leaf and "债权" not in leaf:
            return None
        if not _LEAF_PREFIX_RE.match(leaf):
            leaf = "－" + leaf

    cats = _split_head_into_categories(head)
    if leaf:
        # 至少拆出「父 + 叶子」才有意义
        if len(cats) + 1 < 2:
            return None
        # 单段 head 且未识别到已知科目时，仍允许 head + leaf（有 dash 证据）
        if len(cats) == 1 and cats[0] == head and head not in _CATEGORY_PHRASES:
            # 仅当 head 很长且像科目
            if len(head) < 10:
                return None
        return cats + [leaf]

    # 无 dash：仅当识别出 ≥2 个已知科目粘连
    if len(cats) >= 2:
        return cats
    return None


def _row_starts_with_leaf(row: Sequence[Any], label_col: int = 0) -> bool:
    if not row:
        return False
    lab = str(row[label_col] if label_col < len(row) else row[0] or "").strip()
    return bool(_LEAF_PREFIX_RE.match(lab))


def _label_of(row: Sequence[Any], label_col: int) -> str:
    if not row:
        return ""
    if label_col < len(row):
        return str(row[label_col] or "").strip()
    return str(row[0] or "").strip()


def split_hierarchy_glued_rows(
    data: List[List[Any]],
    *,
    label_col: int = 0,
) -> Tuple[List[List[Any]], List[str]]:
    """拆开「父级+首个子项」粘在同一行的标签；数值留在叶子行。"""
    if not data:
        return data, []

    working = [list(r) if isinstance(r, list) else [] for r in data]
    notes: List[str] = []
    out: List[List[Any]] = []
    i = 0
    while i < len(working):
        row = working[i]
        label = _label_of(row, label_col)
        parts = split_glued_hierarchy_label(label)
        neighbor_leaf = False
        if i + 1 < len(working) and _row_starts_with_leaf(working[i + 1], label_col):
            neighbor_leaf = True
        if i > 0 and _row_starts_with_leaf(working[i - 1], label_col):
            neighbor_leaf = True

        if parts and len(parts) >= 2 and (
            neighbor_leaf
            or len(parts) >= 3
            or any(p in _CATEGORY_PHRASES for p in parts[:-1])
        ):
            n_cols = max(len(row), label_col + 1, 1)
            for p in parts[:-1]:
                new_row = [""] * n_cols
                # 保留序号列等非标签列
                for j in range(n_cols):
                    if j == label_col:
                        new_row[j] = p
                    elif j < len(row) and j != label_col:
                        # 父行不带金额：数值列置空
                        if j > label_col:
                            new_row[j] = ""
                        else:
                            new_row[j] = row[j]
                out.append(new_row)
            leaf_row = list(row) if len(row) >= n_cols else list(row) + [""] * (n_cols - len(row))
            while len(leaf_row) < n_cols:
                leaf_row.append("")
            leaf_row[label_col] = parts[-1]
            out.append(leaf_row)
            notes.append(
                f"层级拆分 r{i}(col{label_col}): "
                f"{' / '.join(parts[:3])}{'…' if len(parts) > 3 else ''}"
            )
            i += 1
            continue

        out.append(row)
        i += 1

    return out, notes


def merge_wrapped_label_rows(
    data: List[List[Any]],
    *,
    label_col: int = 0,
) -> Tuple[List[List[Any]], List[str]]:
    """合并真·标签折行（作用在 label_col，非死盯第 0 列）。"""
    if not data or len(data) < 2:
        return data, []

    working = [list(r) if isinstance(r, list) else [] for r in data]
    notes: List[str] = []
    i = 0
    while i < len(working) - 1:
        a = working[i]
        b = working[i + 1]
        if not a or not b:
            i += 1
            continue
        if _row_amount_count(a) < 1 or _row_amount_count(b) > 0:
            i += 1
            continue
        # 除标签列外，下行应无其它非空
        extra = 0
        for j, c in enumerate(b):
            if j == label_col:
                continue
            if str(c or "").strip():
                extra += 1
        if extra > 0:
            i += 1
            continue
        a0 = _label_of(a, label_col)
        b0 = _label_of(b, label_col)
        if not _is_label_continuation(a0, b0):
            i += 1
            continue
        merged = f"{a0}{b0}".strip()
        while len(working[i]) <= label_col:
            working[i].append("")
        working[i][label_col] = merged
        del working[i + 1]
        notes.append(f"折行合并 r{i}+r{i+1}(col{label_col}) → {merged[:36]}")
    return working, notes


def repair_table_wrap_split(
    table: Dict[str, Any],
    *,
    label_col: Optional[int] = None,
) -> List[str]:
    """先拆层级粘连，再合并真折行。"""
    if not table or table.get("type") in ("text", "paragraph"):
        return []
    data = table.get("data")
    if not isinstance(data, list) or len(data) < 2:
        return []

    if label_col is None:
        try:
            from codes.table_repair.column_roles import infer_column_roles

            label_col = int(infer_column_roles(data).primary_label_col or 0)
        except Exception:
            label_col = 0

    working = [list(r) if isinstance(r, list) else [] for r in data]
    notes: List[str] = []

    split_data, split_notes = split_hierarchy_glued_rows(
        working, label_col=label_col
    )
    if split_notes:
        working = split_data
        notes.extend(split_notes)
        table["_hierarchy_split"] = True

    merged_data, merge_notes = merge_wrapped_label_rows(
        working, label_col=label_col
    )
    if merge_notes:
        working = merged_data
        notes.extend(merge_notes)
        table["_wrap_repaired"] = True

    if not notes:
        return []

    table["data"] = working
    table["rows"] = len(working)
    if working and isinstance(working[0], list):
        table["cols"] = len(working[0])
    table.pop("_cell_source_items", None)
    return [f"P{table.get('page', '?')} {n}" for n in notes]
