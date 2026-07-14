# -*- coding: utf-8 -*-
"""
表头 / 表尾边界判定 — 共享逻辑

设计原则（非对称）：
  - **向上（表头）**：日期/年份类行视为多级表头，不因列数少、列不对齐而拆出。
  - **向下（表尾）**：数据区列指纹稳定后，下方行列数或数值列模式偏离 → 停止扩展 / 应拆分。

供 Table Engine / header 扩展共用。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from codes.table_validator.cell_differ import _cluster_items_by_y

# 纯年号单元格：2024年 / 2023
_YEAR_CELL_RE = re.compile(r"^[\s　]*(19|20)\d{2}年?[\s　]*$")
# 月日单元格：12月31日；或 PDF 拆开的 12 + 月 + 31 + 日
_MONTH_DAY_CELL_RE = re.compile(
    r"^[\s　]*(?:\d{1,2}月\d{1,2}日|\d{1,2}|[月日])[\s　]*$"
)
# 行内年份/日期片段（用于块级判定）
_YEAR_IN_TEXT_RE = re.compile(r"(?:19|20)\d{2}年?")
_MONTH_DAY_IN_TEXT_RE = re.compile(r"\d{1,2}月\d{1,2}日")

# 行标签列：明显不是表头日期（如 折现率、本集团）
_ROW_LABEL_KW = (
    "率", "增长", "寿命", "假设", "项目", "注释", "注", "合计", "总计",
    "本集团", "本行", "阶段", "精算", "折现", "医疗", "死亡",
)


def _normalize_cell_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def is_year_cell(text: str) -> bool:
    return bool(_YEAR_CELL_RE.match(_normalize_cell_text(text)))


def is_month_day_cell(text: str) -> bool:
    t = _normalize_cell_text(text)
    if _MONTH_DAY_CELL_RE.match(t):
        return True
    return bool(_MONTH_DAY_IN_TEXT_RE.search(t))


def is_row_label_cell(text: str) -> bool:
    """左侧行标签（非日期表头）。"""
    t = str(text or "").strip()
    if not t or is_year_cell(t) or is_month_day_cell(t):
        return False
    if _YEAR_IN_TEXT_RE.search(t) and len(t) <= 8:
        return False
    if any(kw in t for kw in _ROW_LABEL_KW):
        return True
    # 含中文且较长 → 行标签
    cn = len(re.findall(r"[\u4e00-\u9fff]", t))
    return cn >= 2 and not re.search(r"[\d.,()%]", t)


# 脚注编号单元格：(1) （1） 1. 注： 等（结构，非科目词）
_FOOTNOTE_MARKER_CELL_RE = re.compile(
    r"^[\s　]*(?:"
    r"\(\d+\)|（\d+）"
    r"|\d+[\.\．\、\)\)]"
    r"|注[：:]"
    r"|\*"
    r"|[①②③④⑤⑥⑦⑧⑨⑩]"
    r")"
)


def is_footnote_marker_cell(text: str) -> bool:
    """单元格是否为脚注/注释编号标记（(1)、1.、注：等）。"""
    return bool(_FOOTNOTE_MARKER_CELL_RE.match(str(text or "").strip()))


def is_numeric_data_cell(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if is_year_cell(t) or is_month_day_cell(t):
        return False
    if not re.search(r"\d", t):
        return False
    # 长中文说明（如表尾注释落在非首列）不是数值格
    if len(re.findall(r"[\u4e00-\u9fff]", t)) >= 8:
        return False
    return bool(re.search(r"[\d.,()%\-（）()]", t))


def row_texts_from_items(row_items: List[dict]) -> List[str]:
    return [it.get("text", "").strip() for it in row_items if it.get("text", "").strip()]


def _ensure_y_mid(items: List[dict]) -> List[dict]:
    out = []
    for it in items:
        if "y_mid" not in it:
            it = dict(it)
            it["y_mid"] = (it.get("y0", 0) + it.get("y1", 0)) / 2
        out.append(it)
    return out


def is_date_only_header_row_items(row_items: List[dict]) -> bool:
    """多级表头中的日期/年份行（无行标签、无主体数值列）。"""
    texts = row_texts_from_items(row_items)
    if not texts:
        return False
    if any(is_row_label_cell(t) for t in texts):
        return False
    date_like = sum(
        1 for t in texts
        if is_year_cell(t) or is_month_day_cell(t) or _YEAR_IN_TEXT_RE.search(t)
    )
    numeric_data = sum(1 for t in texts if is_numeric_data_cell(t))
    return date_like >= 1 and numeric_data == 0 and date_like >= len(texts) * 0.5


def is_date_only_header_block(block: dict) -> bool:
    """间隙块是否仅为日期类表头（应挂到下方主表，而非独立成表）。"""
    items = block.get("text_items", [])
    full = block.get("full_text", "")
    if full:
        # 含附注章节号 / 长说明句 → 不是纯日期表头块（勿整段挂 _pre_header）
        if re.search(r"(?:^|\n)\d{1,2}\s+[\u4e00-\u9fff]", full):
            return False
        if re.search(r"[（(]\d+[)）]\s*[\u4e00-\u9fff]", full):
            return False
    if not items:
        if not full:
            return False
        has_year = bool(_YEAR_IN_TEXT_RE.search(full))
        has_md = bool(_MONTH_DAY_IN_TEXT_RE.search(full))
        if not (has_year or has_md):
            return False
        # 无参数型数值（排除 1.75% 等）
        return not re.search(r"\d+\.\d+%", full)

    rows = _cluster_items_by_y(_ensure_y_mid(items))
    if not rows:
        return False

    for row in rows:
        texts = row_texts_from_items(row.get("items", []))
        if texts and re.match(r"^\d{1,2}$", texts[0].strip()):
            return False
        if texts and re.match(r"^[（(]\d+[)）]$", texts[0].strip()):
            return False

    body_numeric_rows = 0
    header_rows = 0
    for row in rows:
        row_items = row.get("items", [])
        if is_date_only_header_row_items(row_items):
            header_rows += 1
        else:
            texts = row_texts_from_items(row_items)
            num_cells = sum(1 for t in texts if is_numeric_data_cell(t))
            if num_cells >= 2:
                body_numeric_rows += 1

    # 全是日期表头行，或日期表头 + 至多 1 行非数值
    return header_rows >= 1 and body_numeric_rows == 0


def is_stage_column_header_block(block: dict) -> bool:
    """阶段一/二/三列名 + 年份 → 预期信用损失等续表的间隙表头，挂下方 region。"""
    items = block.get("text_items", [])
    full = block.get("full_text", "")
    text = full if full else " ".join(
        it.get("text", "") for it in items
    )
    if not text:
        return False
    if not all(k in text for k in ("阶段一", "阶段二", "阶段三")):
        return False
    if "合计" not in text:
        return False
    return bool(_YEAR_IN_TEXT_RE.search(text))


def is_period_year_only_block(block: dict) -> bool:
    """间隙块仅含单个报告期年份（如「2024年」）→ 挂下方表作多级表头首行。"""
    items = block.get("text_items", [])
    full = str(block.get("full_text", "")).strip()
    lines = [ln.strip() for ln in full.split("\n") if ln.strip()]
    if len(lines) == 1 and is_year_cell(lines[0]):
        return True
    if items and len(items) <= 3:
        texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
        if len(texts) == 1 and is_year_cell(texts[0]):
            return True
    return False


def is_stage_migration_label_row(row: list) -> bool:
    """表内「转移：」等阶段迁移小节行（留在表中，不拆 text）。"""
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells or len(cells) > 2:
        return False
    joined = "".join(cells)
    if re.match(r"^转移\s*[:：]?\s*$", joined):
        return True
    return False


def count_numeric_data_cells_in_row(row_items: List[dict]) -> int:
    return sum(
        1 for it in row_items
        if is_numeric_data_cell(it.get("text", ""))
    )


def has_left_label_column(row_items: List[dict], data_x_threshold: float) -> bool:
    """行左侧是否有行标签列（x 明显靠左）。"""
    for it in row_items:
        t = it.get("text", "")
        if is_row_label_cell(t):
            cx = (it.get("x0", 0) + it.get("x1", 0)) / 2
            if cx < data_x_threshold:
                return True
    return False


def compute_body_fingerprint(items: List[dict]) -> Optional[Dict]:
    """从已纳入表格的 items 估计数据区列指纹。"""
    if not items:
        return None

    rows = _cluster_items_by_y(_ensure_y_mid(items))
    data_rows = []
    for row in rows:
        row_items = row.get("items", [])
        if count_numeric_data_cells_in_row(row_items) >= 2:
            data_rows.append(row_items)

    if not data_rows:
        return None

    # 数值列 X 中心
    num_xs: List[float] = []
    label_xs: List[float] = []
    for row_items in data_rows:
        for it in row_items:
            t = it.get("text", "")
            cx = (it.get("x0", 0) + it.get("x1", 0)) / 2
            if is_numeric_data_cell(t):
                num_xs.append(cx)
            elif is_row_label_cell(t):
                label_xs.append(cx)

    if len(num_xs) < 2:
        return None

    num_xs.sort()
    has_label = len(label_xs) > 0 and (not num_xs or min(label_xs) < num_xs[0] - 30)
    return {
        "numeric_col_count": len(num_xs) // max(len(data_rows), 1),
        "numeric_x_centers": num_xs,
        "has_row_label_col": has_label,
        "data_row_count": len(data_rows),
    }


_DASH_PLACEHOLDERS = frozenset({"－", "-", "—", "–", "N/A", "NA", "n/a"})

# 表内小节标题（完整短语）；勿用「利息成本」等子串，避免误匹配表下注释句
_TABLE_SECTION_PHRASES = (
    "计入当期损益",
    "计入其他综合收益",
    "其他变动",
    "流动性覆盖率",
    "净稳定资金比例",
    "可用资本（数额）",
    "风险加权资产（数额）",
    "其他各级资本要求",
)


def is_value_like_cell(text: str) -> bool:
    """数值或横杠占位（表数据区合法内容）。"""
    t = str(text or "").strip()
    if not t:
        return False
    if t in _DASH_PLACEHOLDERS:
        return True
    if is_year_cell(t) or is_month_day_cell(t):
        return False
    return is_numeric_data_cell(t)


def _pad_row_to_width(row: list, width: int) -> List[str]:
    cells = [str(c).strip() for c in row]
    if len(cells) < width:
        cells.extend([""] * (width - len(cells)))
    return cells[:width]


def count_value_cells_in_row(row: list, col_count: int, start_col: int = 1) -> int:
    """非首列（或指定列起）的数值/占位格数量。"""
    cells = _pad_row_to_width(row, col_count)
    return sum(1 for c in cells[start_col:] if is_value_like_cell(c))


def is_in_table_section_label_row(row: list) -> bool:
    """表内小节标题（无数值、应留在表中），非表尾说明句。"""
    cells = _pad_row_to_width(row, max(len(row), 1))
    non_empty = [c for c in cells if c]
    if not non_empty or len(non_empty) > 3:
        return False
    joined = "".join(non_empty)
    if is_stage_migration_label_row(row):
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    if sum(1 for c in non_empty if is_value_like_cell(c)) > 0:
        return False
    if cn < 4 or cn > 30:
        return False
    # 完整说明句（表尾注释）不是小节标题
    if joined.endswith(("。", "；")) and cn > 12:
        return False
    if any(kw in joined for kw in _TABLE_SECTION_PHRASES):
        return True
    if joined.startswith(("－", "—", "-")):
        return True
    return _is_short_section_label_structural(cells, joined)


def _is_short_section_label_structural(cells: List[str], joined: str) -> bool:
    """短中文表内小节（无行号、无有效数值），通用结构判定。"""
    import re as _re
    if not cells or len(cells) > 3:
        return False
    if cells[0] and _re.match(r"^\d+[a-z]?$", cells[0], _re.IGNORECASE):
        return False
    if sum(
        1 for c in cells
        if is_value_like_cell(c) and str(c).strip() not in _DASH_PLACEHOLDERS
    ) > 0:
        return False
    cn = len(_re.findall(r"[\u4e00-\u9fff]", joined))
    if cn < 3 or cn > 28:
        return False
    if joined.endswith(("。", "；")) and cn > 12:
        return False
    return True


def is_table_tail_annotation_row(row: list, col_count: int) -> bool:
    """表尾候选行：列宽与表一致，但无有效数值列 / 尾部连续空列 + 首列长说明。

    用于自底向上剥离表下注释，不用于表中间的小节标题行。
    """
    if col_count <= 0:
        return False

    if len(row) > col_count:
        return False

    # 行列数短于表宽 → 可能是碎片；脚注编号行除外（常少一列）
    if 0 < len(row) < col_count:
        first_peek = str(row[0] or "").strip() if row else ""
        if not is_footnote_marker_cell(first_peek):
            return False

    cells = _pad_row_to_width(row, col_count)

    joined_all = "".join(c for c in cells if c)
    # 列名表头（期数/金额/占比重复）不是表尾注释
    if (
        count_value_cells_in_row(cells, col_count, start_col=0) == 0
        and joined_all.count("期数") >= 1
        and joined_all.count("金额") >= 1
        and (
            joined_all.count("期数") >= 2
            or "百万元" in joined_all
            or "占比" in joined_all
        )
    ):
        return False

    non_empty_idx = [i for i, c in enumerate(cells) if c]
    if not non_empty_idx:
        return True

    first = cells[non_empty_idx[0]]

    # 脚注编号行：首格为 (1)/1. 等且同行无数值列（排除排名表 1. 工行 500）
    if is_footnote_marker_cell(first):
        if re.match(r"^注[：:]", first):
            return True
        if count_value_cells_in_row(cells, col_count, start_col=1) >= 1:
            return False
        return True

    # 条件1：数值列应有数值类数据；有则视为数据行
    if count_value_cells_in_row(cells, col_count, start_col=1) >= 1:
        return False

    # 表内小节标题（仅首列有字、其余空）——表中间合法，表尾极少见；带句号长句优先当注释
    if is_in_table_section_label_row(cells):
        return False

    # 编号/注：类脚注前缀（兼容旧模式）
    if re.match(
        r"^\d+[\.\、\)\)]|^注[：:]|^\*|^[①②③④⑤⑥⑦⑧⑨⑩]|^来源[：:]|^数据来源[：:]",
        first,
    ):
        return True

    # 条件2：列宽一致；首列有内容且后续连续空列
    if non_empty_idx == [0]:
        cn = len(re.findall(r"[\u4e00-\u9fff]", first))
        tail_empty = all(not cells[i] for i in range(1, col_count))
        if tail_empty and cn >= 6:
            return True
        if tail_empty and cn >= 4 and first.endswith(("。", "；", ".", ";")):
            return True

    # 仅首列有长中文、后部全空
    if len(non_empty_idx) == 1 and non_empty_idx[0] == 0:
        cn = len(re.findall(r"[\u4e00-\u9fff]", first))
        if cn >= 10 and all(not cells[i] for i in range(1, col_count)):
            return True

    # 整行无数值格、长中文说明（说明落在非首列，如 col2 长段落）
    if count_value_cells_in_row(cells, col_count, start_col=0) == 0:
        joined = "".join(c for c in cells if c)
        cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
        if cn >= 12:
            return True

    return False


def _last_body_row_index(data: List[list], col_count: int) -> int:
    """最后一行主体数据（≥2 个数值/占位格，自第 1 列起）。"""
    last = -1
    for i, row in enumerate(data):
        cells = _pad_row_to_width(row, col_count)
        if count_value_cells_in_row(cells, col_count, start_col=1) >= 2:
            last = i
        elif count_value_cells_in_row(cells, col_count, start_col=0) >= 2:
            last = i
    return last


def _strip_contiguous_annotations_after_body(
    data: List[list],
    width: int,
) -> Tuple[List[list], List[str]]:
    """剥离紧接在最后主体数据行之后的连续注释行（如 合计 后的 (1) 说明）。"""
    body_idx = _last_body_row_index(data, width)
    if body_idx < 0 or body_idx >= len(data) - 1:
        return data, []

    remove_idx: List[int] = []
    for i in range(body_idx + 1, len(data)):
        if is_table_tail_annotation_row(data[i], width):
            remove_idx.append(i)
        else:
            break

    if not remove_idx:
        return data, []

    texts: List[str] = []
    for i in remove_idx:
        row = _pad_row_to_width(data[i], width)
        text = " ".join(c for c in row if c).strip()
        if text:
            texts.append(text)

    new_data = [row for j, row in enumerate(data) if j not in remove_idx]
    return new_data, texts


def strip_tail_annotation_rows_from_data(
    data: List[list],
    col_count: Optional[int] = None,
    max_scan: int = 5,
    min_keep_rows: int = 2,
) -> Tuple[List[list], List[str]]:
    """自底向上剥离表尾注释行。遇首个合格数据行即停止。"""
    if len(data) < min_keep_rows:
        return data, []

    width = col_count or max((len(r) for r in data), default=0)
    if width <= 0:
        return data, []

    limit = min(max_scan, len(data) - min_keep_rows)
    if limit < 1:
        return data, []

    stripped: List[str] = []
    remove = 0

    for i in range(len(data) - 1, len(data) - 1 - limit, -1):
        row = data[i]
        if is_table_tail_annotation_row(row, width):
            text = " ".join(c for c in _pad_row_to_width(row, width) if c).strip()
            if text:
                stripped.insert(0, text)
            remove += 1
        else:
            break

    if remove > 0:
        data = data[: len(data) - remove]

    data, body_texts = _strip_contiguous_annotations_after_body(data, width)
    if body_texts:
        stripped.extend(body_texts)

    if remove > 0 or body_texts:
        return data, stripped
    return data, []


def _cell_nonempty(cell) -> bool:
    return bool(str(cell or "").strip())


def remove_fully_empty_rows(data: List[list]) -> List[list]:
    """删除整行全空的行。"""
    return [list(r) for r in data if any(_cell_nonempty(c) for c in r)]


def remove_fully_empty_columns(
    data: List[list],
    preserve_col_indices: Optional[Set[int]] = None,
) -> Tuple[List[list], int]:
    """删除整列全空的列（所有行该列均无内容）。

    preserve_col_indices: 即使全空也保留的列（如 CC1 的「代码」列）。
    """
    if not data:
        return data, 0
    width = max((len(r) for r in data), default=0)
    if width <= 1:
        return data, 0

    preserve = preserve_col_indices or set()
    empty_cols = [
        c
        for c in range(width)
        if c not in preserve
        and all(
            not _cell_nonempty(row[c] if c < len(row) else "")
            for row in data
        )
    ]
    if not empty_cols:
        return data, 0

    keep = [c for c in range(width) if c not in empty_cols]
    rebuilt = []
    for row in data:
        cells = _pad_row_to_width(row, width)
        rebuilt.append([cells[c] for c in keep])
    return rebuilt, len(empty_cols)


def select_data_row_indices(data: List[list]) -> List[int]:
    """含数值列的数据行索引。"""
    if not data:
        return []
    width = max((len(r) for r in data), default=0)
    return [
        i
        for i, row in enumerate(data)
        if count_value_cells_in_row(_pad_row_to_width(row, width), width, start_col=1) >= 1
    ]


def select_data_rows_for_column_prune(data: List[list]) -> List[list]:
    """含数值列的数据行，供互补列合并使用。"""
    width = max((len(r) for r in data), default=0)
    return [
        _pad_row_to_width(data[i], width)
        for i in select_data_row_indices(data)
    ]


def _year_in_cell(text: str) -> Optional[int]:
    m = re.search(r"(20\d{2})", str(text or ""))
    return int(m.group(1)) if m else None


def _pair_has_distinct_year_headers(data: List[list], j: int, width: int) -> bool:
    """相邻列若分别为不同年份表头（如 2024年 | 2023年），禁止合并。"""
    years_l: List[int] = []
    years_r: List[int] = []
    for row in data[:5]:
        cells = _pad_row_to_width(row, width)
        yl = _year_in_cell(cells[j])
        yr = _year_in_cell(cells[j + 1])
        if yl is not None:
            years_l.append(yl)
        if yr is not None:
            years_r.append(yr)
    if not years_l or not years_r:
        return False
    return min(years_l) != min(years_r)


def _pair_has_stage_column_headers(
    data: List[list], left_col: int, width: int,
) -> bool:
    """相邻列若含阶段一/二/三表头 → 禁止互补列合并。"""
    markers = ("阶段一", "阶段二", "阶段三")
    for row in data[:5]:
        cells = _pad_row_to_width(row, width)
        for ci in (left_col, left_col + 1):
            if ci < len(cells) and any(m in str(cells[ci]) for m in markers):
                return True
    return False


def merge_complementary_column_pairs(data: List[list]) -> List[list]:
    """合并相邻互补列：数据行中极少同时有值的列对（同一逻辑列的 PDF 偏移）。

    约束：只删列、只把右侧值填入左侧空位，不改变列序（左列始终在前）；
    禁止合并带不同年份表头的相邻列（如 2024年 | 2023年）。
    """
    if not data or len(data) < 2:
        return data

    normalized = [
        _pad_row_to_width(r, max((len(x) for x in data), default=0))
        for r in data
    ]
    data_indices = select_data_row_indices(normalized)
    if len(data_indices) < 2:
        return normalized

    merged_any = True
    while merged_any:
        merged_any = False
        width = len(normalized[0])
        for j in range(1, width - 1):
            overlap = fill_l = fill_r = 0
            for ri in data_indices:
                cells = _pad_row_to_width(normalized[ri], width)
                hl = _cell_nonempty(cells[j])
                hr = _cell_nonempty(cells[j + 1])
                if hl and hr:
                    overlap += 1
                if hl:
                    fill_l += 1
                if hr:
                    fill_r += 1

            if overlap > 1 or fill_l < 1 or fill_r < 1:
                continue
            if _pair_has_distinct_year_headers(normalized, j, width):
                continue
            if _pair_has_stage_column_headers(normalized, j, width):
                continue

            new_width = width - 1
            rebuilt = []
            for row in normalized:
                cells = _pad_row_to_width(row, width)
                # 物理左列优先：仅当左空才取右，绝不把右侧值挪到更左的列之外
                left_v = cells[j]
                right_v = cells[j + 1]
                if _cell_nonempty(left_v):
                    merged_val = left_v
                elif _cell_nonempty(right_v):
                    merged_val = right_v
                else:
                    merged_val = ""
                new_row = cells[:j] + [merged_val] + cells[j + 2 :]
                rebuilt.append(_pad_row_to_width(new_row, new_width))
            normalized = rebuilt
            data_indices = select_data_row_indices(normalized)
            merged_any = True
            break

    return normalized


def _code_column_index_from_header(data: List[list]) -> Optional[int]:
    """表头含「代码」时返回其列索引，用于保留空代码列。"""
    width = max((len(r) for r in data), default=0)
    for row in data[:6]:
        cells = _pad_row_to_width(row, width)
        for j, c in enumerate(cells):
            if str(c).strip() == "代码":
                return j
    return None


def compact_table_spacer_rows_and_columns(
    data: List[list],
    *,
    preserve_code_column: bool = False,
) -> List[list]:
    """删除全空行/列，并合并 PDF 间隔造成的互补偏移列。

    行序、列序严格保持 PDF 自上而下、从左到右；仅删除空列，不交换列位。
    preserve_code_column: CC1 a/b 表保留「代码」列，即使多为空白。
    """
    if not data or len(data) < 2:
        return data

    data = remove_fully_empty_rows(data)
    if not data:
        return data

    preserve: Set[int] = set()
    if preserve_code_column:
        code_idx = _code_column_index_from_header(data)
        if code_idx is not None:
            preserve.add(code_idx)
        elif max((len(r) for r in data), default=0) >= 4:
            preserve.add(3)

    data, _ = remove_fully_empty_columns(data, preserve_col_indices=preserve)
    if not preserve_code_column:
        data = merge_complementary_column_pairs(data)
    return data


def row_body_mismatch_with_fingerprint(
    row_items: List[dict],
    fingerprint: Dict,
    tolerance: float = 25.0,
) -> bool:
    """下方行是否与主体数据区列指纹明显不一致（应停止向下扩展）。"""
    if not fingerprint:
        return False

    num_count = count_numeric_data_cells_in_row(row_items)
    texts = row_texts_from_items(row_items)

    # 纯日期子表头 → 不算表尾异类
    if is_date_only_header_row_items(row_items):
        return False

    # 脚注/说明：无足够数值列
    if num_count < 2:
        long_cn = sum(len(re.findall(r"[\u4e00-\u9fff]", t)) for t in texts)
        if long_cn >= 15:
            return True
        return False

    # 数值列数偏离（允许 ±1）
    body_num_cols = fingerprint.get("numeric_col_count", 0)
    if body_num_cols > 0 and abs(num_count - body_num_cols) >= 2:
        return True

    # 数值列 X 位置整体偏移（新表结构）
    row_num_xs = sorted(
        (it.get("x0", 0) + it.get("x1", 0)) / 2
        for it in row_items
        if is_numeric_data_cell(it.get("text", ""))
    )
    body_xs = fingerprint.get("numeric_x_centers", [])
    if row_num_xs and body_xs:
        body_med = body_xs[len(body_xs) // 2]
        row_med = row_num_xs[len(row_num_xs) // 2]
        if abs(row_med - body_med) > 80:
            return True

    # 主体有行标签列，但本行数值列满行无标签且列数不同 → 另一张表
    if fingerprint.get("has_row_label_col") and not has_left_label_column(
        row_items, (body_xs[0] - 40) if body_xs else 200
    ):
        if num_count >= 2 and body_num_cols > 0 and num_count != body_num_cols:
            return True

    return False


def _item_in_column_ranges(
    cx: float,
    col_ranges: List[Tuple[float, float]],
    tolerance: float = 15.0,
) -> bool:
    for x0, x1 in col_ranges:
        if x0 - tolerance <= cx <= x1 + tolerance:
            return True
    return False


def _is_coord_header_row(
    row_items: List[dict],
    col_ranges: List[Tuple[float, float]],
    col_tolerance: float = 15.0,
) -> bool:
    """判断一行 items 是否为多列表头行（坐标域）。"""
    if len(row_items) < 2:
        return False
    in_col = []
    for it in row_items:
        cx = (it["x0"] + it["x1"]) / 2
        if _item_in_column_ranges(cx, col_ranges, tolerance=col_tolerance):
            in_col.append(it)
    if len(in_col) < 2:
        return False
    numeric_count = sum(
        1 for it in in_col if re.search(r"[\d.,()%]", it.get("text", ""))
    )
    total = len(in_col)
    label_count = total - numeric_count
    if label_count >= max(2, total // 2):
        cols_hit = set()
        for it in in_col:
            cx = (it["x0"] + it["x1"]) / 2
            for ci, (rx0, rx1) in enumerate(col_ranges):
                if rx0 - col_tolerance <= cx <= rx1 + col_tolerance:
                    cols_hit.add(ci)
                    break
        if len(cols_hit) >= 2:
            return True
    return False


def is_new_table_header_row_items(
    row_items: List[dict],
    col_ranges: List[Tuple[float, float]],
    col_tolerance: float = 15.0,
) -> bool:
    """是否为**新一张表**的表头行（用于向下扩展时停止）。

    日期-only 行不算新表起点（属于多级表头）。
    """
    if is_date_only_header_row_items(row_items):
        return False
    return _is_coord_header_row(row_items, col_ranges, col_tolerance=col_tolerance)


# --- 表头带 / 主体数据行（region 合并与切分共用，纯结构） ---


def cluster_region_items_to_text_rows(
    region: dict,
    all_page_items: List[dict],
    y_tol: float = 8.0,
) -> List[List[str]]:
    """将 region 内 text_items 按 Y 聚类为行（每行若干 cell 文本）。"""
    ry0, ry1 = region.get("y0", 0), region.get("y1", 0)
    scoped = [
        it for it in all_page_items
        if ry0 - 8 <= it.get("y_mid", (it.get("y0", 0) + it.get("y1", 0)) / 2) <= ry1 + 8
    ]
    if not scoped:
        return []

    scoped.sort(key=lambda it: (it.get("y0", 0), it.get("x0", 0)))
    rows: List[List[str]] = []
    row_y: Optional[float] = None
    for it in scoped:
        ym = it.get("y_mid", (it.get("y0", 0) + it.get("y1", 0)) / 2)
        t = str(it.get("text", "")).strip()
        if not t:
            continue
        if row_y is None or abs(ym - row_y) > y_tol:
            rows.append([t])
            row_y = ym
        else:
            rows[-1].append(t)
    return rows


def is_body_data_row_texts(row_texts: List[str]) -> bool:
    """主体数据行：行内 ≥2 个数值/占位格。"""
    non_empty = [t for t in row_texts if t]
    if len(non_empty) < 2:
        return False
    return sum(1 for t in non_empty if is_value_like_cell(t)) >= 2


def is_header_band_row_texts(row_texts: List[str]) -> bool:
    """表头带行：非主体数据，且呈多列表头或日期表头形态。"""
    if is_body_data_row_texts(row_texts):
        return False
    non_empty = [t for t in row_texts if t]
    if not non_empty:
        return False

    # 披露表行号数据行（1 / 2a + 科目 + 数值）不是表头带
    if re.match(r"^\d+[a-z]?$", non_empty[0], re.IGNORECASE):
        return False

    date_like = sum(1 for t in non_empty if is_year_cell(t) or is_month_day_cell(t))
    num = sum(1 for t in non_empty if is_numeric_data_cell(t))
    if date_like >= 1 and num == 0 and date_like >= len(non_empty) * 0.4:
        return True

    text_cols = [t for t in non_empty if not is_value_like_cell(t)]
    if len(text_cols) >= 2:
        return True
    if len(non_empty) >= 2 and num == 0:
        return True
    return False


def region_has_header_band(
    region: dict,
    all_page_items: List[dict],
    *,
    lookahead: int = 8,
) -> bool:
    """region 顶部是否存在表头带（一行或多行表头文本）。

    若首行已是主体数据、其前又无表头行 → False（疑似被 liteparse 拦腰切断）。
    """
    rows = cluster_region_items_to_text_rows(region, all_page_items)
    if not rows:
        return True

    scan = rows[:lookahead]
    first_body: Optional[int] = None
    for i, row in enumerate(scan):
        if is_body_data_row_texts(row):
            first_body = i
            break

    if first_body is None:
        return any(is_header_band_row_texts(r) for r in scan)

    if first_body == 0:
        return False

    return any(is_header_band_row_texts(r) for r in scan[:first_body])


def table_data_has_header_band(data: List[list], *, max_scan: int = 8) -> bool:
    """表 data 顶部是否存在表头带。

    若首行已是主体数据、其前又无表头行 → False（疑似拦腰切断，应向上回补）。
    """
    if not data:
        return True

    scan = data[:max_scan]
    first_body: Optional[int] = None
    for i, row in enumerate(scan):
        cells = [str(c).strip() for c in row if str(c).strip()]
        if is_body_data_row_texts(cells):
            first_body = i
            break

    if first_body is None:
        return any(
            is_header_band_row_texts([str(c).strip() for c in row if str(c).strip()])
            for row in scan
        )

    if first_body == 0:
        return False

    return any(
        is_header_band_row_texts([str(c).strip() for c in row if str(c).strip()])
        for row in scan[:first_body]
    )


# --- 表尾脚注：原位置唯一一份，拆分/深拷贝只转移不复制 ---


def footnote_text_key(page: int, text: str) -> Tuple[int, str]:
    """页内脚注正文归一化键（用于去重）。"""
    return (int(page or 0), re.sub(r"\s+", "", str(text or "").strip()))


def build_footnote_records(
    table: dict,
    footnote_texts: List[str],
    *,
    rows_before_strip: int,
) -> List[dict]:
    """按剥离前行号估算脚注在页面上的 Y，登记为唯一载荷。"""
    page = int(table.get("page", 0) or 0)
    y0 = float(table.get("y0", 0) or 0)
    y1 = float(table.get("y1", 0) or 0)
    n = max(int(rows_before_strip), 1)
    h = (y1 - y0) / n if y1 > y0 else 12.0
    removed = len(footnote_texts)
    records: List[dict] = []
    for i, raw in enumerate(footnote_texts):
        text = str(raw).strip()
        if not text:
            continue
        row_i = n - removed + i
        fy0 = y0 + row_i * h
        fy1 = y0 + (row_i + 1) * h
        records.append({"text": text, "page": page, "y0": fy0, "y1": fy1})
    return records


def clear_footnote_bundle(table: dict) -> None:
    table.pop("_footnote_records", None)
    table.pop("_footnote_texts", None)


def assign_footnote_bundle(table: dict, records: List[dict]) -> None:
    """脚注只挂到唯一目标表（覆盖，不追加）。"""
    clear_footnote_bundle(table)
    if records:
        table["_footnote_records"] = records


def pop_footnote_bundle(table: dict) -> List[dict]:
    """取出并清除表格脚注载荷（转移语义）。"""
    recs = table.pop("_footnote_records", None)
    if recs:
        return list(recs)
    legacy = table.pop("_footnote_texts", None) or []
    if not legacy:
        return []
    rows_before = int(table.get("rows", 0) or 0) + len(legacy)
    return build_footnote_records(table, legacy, rows_before_strip=rows_before)


def attach_stripped_footnotes(
    table: dict,
    footnote_texts: List[str],
    *,
    rows_before_strip: int,
) -> None:
    """表尾剥离后登记脚注：仅一份，带原行位 Y。"""
    if not footnote_texts:
        return
    records = build_footnote_records(
        table, footnote_texts, rows_before_strip=rows_before_strip,
    )
    assign_footnote_bundle(table, records)

