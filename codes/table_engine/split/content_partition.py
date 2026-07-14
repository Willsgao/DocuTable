# -*- coding: utf-8
"""页内内容划分：TABLE/TEXT 互斥、不丢字（转移语义下的去重）。"""

from __future__ import annotations

import copy
import re
from typing import List, Optional, Set, Tuple

from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.models import DocumentEntry, PageSource, TextBlock
from codes.table_engine.split.boundary_overlap import (
    _LABEL_SUFFIX_WORDS,
    row_value_fingerprint,
)
from codes.table_engine.table_access import dense_rows

_MIN_DEDUP_CHARS = 4
_PAGE_CHROME_MARKERS = ("年度报告", "财务报表附注", "第三支柱", "信息披露报告")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _table_cell_keys(table) -> Tuple[Set[str], Set[str], Set[str]]:
    """归一化全文片段 + 数值 token + 整行拼接（用于 TEXT 去重）。"""
    exact: Set[str] = set()
    numeric: Set[str] = set()
    row_lines: Set[str] = set()
    for row in dense_rows(table):
        row_parts: List[str] = []
        for cell in row:
            t = str(cell or "").strip()
            if not t:
                continue
            norm = _normalize_text(t)
            if len(norm) >= 2:
                exact.add(norm)
                row_parts.append(norm)
            if is_numeric_data_cell(t):
                numeric.add(norm)
        if len(row_parts) >= 2:
            row_lines.add("".join(row_parts))
    return exact, numeric, row_lines


def _table_source_item_ids(table) -> Set[str]:
    ids: Set[str] = set()
    for row in table.rows:
        for cell in row:
            if cell is None:
                continue
            for idx in cell.source_items or []:
                ids.add(str(idx))
    return ids


def _normalize_value_token(text: str) -> str:
    return str(text or "").strip().replace(",", "").replace(" ", "")


def _text_line_value_fingerprint(line: str) -> Tuple[str, ...]:
    """从 narrative 单行提取与表行一致的数值指纹（折行标签合并后仍可匹配）。"""
    parts = [p for p in re.split(r"\s+", line.strip()) if p.strip()]
    if not parts:
        return ()
    start = 0
    first = _normalize_value_token(parts[0])
    if (
        not is_numeric_data_cell(parts[0])
        and not first.endswith("%")
        and not re.match(r"^-?\d", first)
    ):
        start = 1
    vals: List[str] = []
    for part in parts[start:]:
        token = _normalize_value_token(part)
        if not token:
            continue
        if (
            is_numeric_data_cell(token)
            or token.endswith("%")
            or re.match(r"^-?\d", token)
        ):
            vals.append(token)
    return tuple(vals)


def _line_is_absorbed_label_suffix(line: str, exact: Set[str]) -> bool:
    norm = _normalize_text(line)
    if norm in _LABEL_SUFFIX_WORDS:
        for cell in exact:
            if cell.endswith(norm) and len(cell) > len(norm) + 3:
                return True
        return False
    if len(norm) <= 22:
        for cell in exact:
            if cell.endswith(norm) and len(cell) > len(norm) + 2:
                return True
    return False


def _collect_table_exclusivity(
    entries: List[DocumentEntry],
) -> Tuple[Set[str], Set[str], Set[str], Set[str], Set[Tuple[str, ...]]]:
    exact: Set[str] = set()
    numeric: Set[str] = set()
    row_lines: Set[str] = set()
    source_ids: Set[str] = set()
    value_fps: Set[Tuple[str, ...]] = set()
    for entry in entries:
        if entry.kind != "table" or entry.table is None:
            continue
        e_keys, n_keys, r_keys = _table_cell_keys(entry.table)
        exact |= e_keys
        numeric |= n_keys
        row_lines |= r_keys
        source_ids |= _table_source_item_ids(entry.table)
        for row in dense_rows(entry.table):
            fp = row_value_fingerprint(row)
            if len(fp) >= 2:
                value_fps.add(fp)
    return exact, numeric, row_lines, source_ids, value_fps


def _line_duplicates_table(
    line: str,
    exact: Set[str],
    numeric: Set[str],
    row_lines: Optional[Set[str]] = None,
    value_fps: Optional[Set[Tuple[str, ...]]] = None,
) -> bool:
    if _line_is_absorbed_label_suffix(line, exact):
        return True
    norm = _normalize_text(line)
    if len(norm) < _MIN_DEDUP_CHARS:
        return False
    if row_lines and norm in row_lines:
        return True
    if norm in exact:
        return True
    if norm in numeric:
        return True
    if value_fps:
        line_fp = _text_line_value_fingerprint(line)
        if len(line_fp) >= 2 and line_fp in value_fps:
            return True
    # 行内多个 token 均已出现在表中
    parts = [p for p in re.split(r"\s+", line.strip()) if p.strip()]
    if len(parts) >= 2:
        part_norms = [_normalize_text(p) for p in parts]
        if all(pn in exact or pn in numeric for pn in part_norms if len(pn) >= 2):
            return True
    return False


def _is_page_chrome_text(text: str) -> bool:
    joined = str(text or "")
    return any(m in joined for m in _PAGE_CHROME_MARKERS)


def _dedupe_text_block(
    block: TextBlock,
    exact: Set[str],
    numeric: Set[str],
    table_source_ids: Set[str],
    row_lines: Optional[Set[str]] = None,
    value_fps: Optional[Set[Tuple[str, ...]]] = None,
) -> TextBlock | None:
    lines = [ln.strip() for ln in block.text.split("\n") if ln.strip()]
    if not lines:
        return None

    kept: List[str] = []
    for line in lines:
        if _line_duplicates_table(
            line, exact, numeric, row_lines, value_fps,
        ):
            continue
        kept.append(line)

    if not kept:
        return None

    src = list(block.source_items or [])
    if table_source_ids and src:
        src = [s for s in src if str(s) not in table_source_ids]
        if not src and not any(
            not _line_duplicates_table(
                l, exact, numeric, row_lines, value_fps,
            )
            for l in lines
        ):
            return None

    if kept == lines and src == list(block.source_items or []):
        return block

    out = copy.copy(block)
    out.text = "\n".join(kept)
    out.source_items = src
    return out


def description_already_in_table(table, desc: str) -> bool:
    if not desc or not table:
        return False
    exact, _, row_lines = _table_cell_keys(table)
    table_blob = "".join(sorted(exact))
    for line in desc.split("\n"):
        norm = _normalize_text(line)
        if len(norm) >= _MIN_DEDUP_CHARS and (
            norm in table_blob or norm in row_lines
        ):
            return True
    return False


def apply_content_partition(
    entries: List[DocumentEntry],
    page: PageSource | None = None,
    *,
    warnings: List[str] | None = None,
) -> List[DocumentEntry]:
    """TABLE 与 TEXT 互斥：已在表内的片段从 TEXT 移除（非静默删除）。"""
    exact, numeric, row_lines, table_src, value_fps = _collect_table_exclusivity(entries)
    if not exact and not numeric:
        return entries

    out: List[DocumentEntry] = []
    for entry in entries:
        if entry.kind != "text" or entry.text_block is None:
            out.append(entry)
            continue

        block = _dedupe_text_block(
            entry.text_block, exact, numeric, table_src, row_lines, value_fps,
        )
        if block is None:
            lines = [
                ln.strip()
                for ln in entry.text_block.text.split("\n")
                if ln.strip()
            ]
            all_lines_in_table = bool(lines) and all(
                _line_duplicates_table(
                    ln, exact, numeric, row_lines, value_fps,
                )
                for ln in lines
            )
            if all_lines_in_table:
                continue
            src = [str(s) for s in (entry.text_block.source_items or [])]
            if src and all(s in table_src for s in src):
                continue
            if warnings is not None and page is not None:
                warnings.append(
                    f"P{page.page_number}: text dedup skipped (would drop content) "
                    f"y={entry.y0:.0f}"
                )
            out.append(entry)
            continue

        if block.text != entry.text_block.text:
            new_entry = copy.copy(entry)
            new_entry.text_block = block
            out.append(new_entry)
        else:
            out.append(entry)

    if page is not None and warnings is not None:
        _audit_source_coverage(out, page, warnings)

    return out


def _audit_source_coverage(
    entries: List[DocumentEntry],
    page: PageSource,
    warnings: List[str],
) -> None:
    """页内 item 未被任何 entry 引用 → 告警（不自动删）。"""
    covered: Set[str] = set()
    for entry in entries:
        if entry.kind == "text" and entry.text_block:
            covered.update(str(s) for s in entry.text_block.source_items or [])
        if entry.kind == "table" and entry.table:
            covered |= _table_source_item_ids(entry.table)

    if not covered:
        return

    page_ids = {it.item_index for it in page.items if it.text.strip()}
    missing = page_ids - covered
    if missing and len(missing) > len(page_ids) * 0.15:
        warnings.append(
            f"P{page.page_number}: {len(missing)} source items not in any entry"
        )


def _all_table_source_ids(tables: List) -> Set[str]:
    ids: Set[str] = set()
    for table in tables:
        ids |= _table_source_item_ids(table)
    return ids


def filter_description_captions(
    tables: List,
    gap_texts: List[TextBlock],
) -> List[TextBlock]:
    """scope description 已在表内首行 → 不再作为 gap TEXT。"""
    if not tables:
        return gap_texts

    desc_norms: Set[str] = set()
    for table in tables:
        desc = str(getattr(table, "description_text", "") or "").strip()
        if not desc:
            continue
        for line in desc.split("\n"):
            n = _normalize_text(line)
            if len(n) >= _MIN_DEDUP_CHARS:
                desc_norms.add(n)
        if description_already_in_table(table, desc):
            for line in desc.split("\n"):
                n = _normalize_text(line)
                if n:
                    desc_norms.add(n)

    if not desc_norms:
        return gap_texts

    filtered: List[TextBlock] = []
    for block in gap_texts:
        lines = [ln.strip() for ln in block.text.split("\n") if ln.strip()]
        kept = [ln for ln in lines if _normalize_text(ln) not in desc_norms]
        if not kept:
            src = list(block.source_items or [])
            if src and all(str(s) in _all_table_source_ids(tables) for s in src):
                continue
            filtered.append(block)
            continue
        if kept == lines:
            filtered.append(block)
            continue
        nb = copy.copy(block)
        nb.text = "\n".join(kept)
        filtered.append(nb)
    return filtered
