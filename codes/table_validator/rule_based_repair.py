# -*- coding: utf-8 -*-
"""
基于规则的表格结构修复器 — 表头引导的分层修复

核心思路（纯规则，零 LLM 调用）：
1. 定位数据区域
2. 根据数据区确定有效的表头区
3. 根据最底层的表头列数确定数据区是否存在空白列或者数据错位
4. 对数据区和表头区分别进行修复：
   - 数据区：判断是否插入空白列或者数据被拆成多列
   - 表头区：判断是否有文本截断或者层级嵌套关系
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 异常数据模型
# ============================================================

# 异常严重程度
ANOMALY_LOW = "low"
ANOMALY_MEDIUM = "medium"
ANOMALY_HIGH = "high"

# 异常类型常量
ANCHOR_SHIFT = "anchor_shift"
"""锚定列被数据感知校正 — 表头锚定位置与实际数据列不匹配，已自动移位"""
WEAK_ANCHOR = "weak_anchor"
"""弱锚定列补充 — col0 因表头为空但数据有行标签，被自动补充为锚定列"""
HEADER_TEXT_MISSING = "header_text_missing"
"""表头文字缺失 — 数据列有值但底层表头缺少对应标签，规则引擎已自动推断补齐"""
DATA_HEADER_MISMATCH = "data_header_mismatch"
"""数据-表头列数不匹配 — 数据列数与表头结构不一致"""
ORPHAN_HEADER_TEXT = "orphan_header_text"
"""孤立表头文本 — 表头文字无法归并到任何锚定数据列"""
TRUNCATED_HEADER_MERGED = "truncated_header_merged"
"""截断表头合并 — 两行表头被合并，可能合并了不应该合并的内容"""
MULTI_TABLE_MERGED = "multi_table_merged"
"""多表合并 — 数据区结束后检测到孤儿数据区，疑似多张独立表格被合并为一"""

_DESCRIPTION_HINTS = ['下表', '如下', '所示', '如下图', '见图',
                      '注：', '注释：', '备注：', '数据来源',
                      '单位：', '说明：', '以下为', '报告期内',
                      '其中：', '其中，', '例如：', '如：']
"""描述文本引导词集合，用于 _is_description_row"""


@dataclass
class RepairAnomaly:
    """规则修复过程中检测到的异常，标记为后期需要 LLM 确认"""

    type: str
    """异常类型（使用 ANCHOR_SHIFT / HEADER_TEXT_MISSING 等常量）"""

    severity: str
    """严重程度: low / medium / high"""

    description: str
    """人类可读的中文描述"""

    location: str
    """异常发生的表结构位置（如 'bottom_header' / 'header_row_1'）"""

    details: Dict[str, Any] = field(default_factory=dict)
    """机器可读的上下文信息（如原始列号、校正后列号、置信度等）"""

    confidence: float = 0.5
    """规则引擎自身的置信度（0.0 ~ 1.0，越低越需要 LLM 确认）"""


# ============================================================
# 工具函数
# ============================================================

def _is_effectively_empty(cell: str) -> bool:
    """判断单元格是否为"有效空"

    PDF 提取常产生各种非标准空白字符：
    - \\u00a0  不间断空格
    - \\u200b  零宽空格
    - \\u3000  全角空格
    - \\t, \\n, 连续空格
    """
    if cell is None:
        return True
    if not isinstance(cell, str):
        return False
    cleaned = re.sub(r'[\s\u00a0\u200b\u200c\u200d\ufeff\u3000]+', '', cell)
    return len(cleaned) == 0


def _count_nonempty_cells(row: List[str]) -> int:
    """统计一行中非空单元格的数量"""
    return sum(1 for c in row if not _is_effectively_empty(c))


def _is_numeric_cell(cell: str) -> bool:
    """判断单元格是否包含数值内容（包括百分比、带括号的负数、千分位等）"""
    if _is_effectively_empty(cell):
        return False
    cleaned = cell.strip()
    # 去除常见数值格式符号
    cleaned = re.sub(r'[,，\s%‰]', '', cleaned)
    cleaned = re.sub(r'^[\(（]|[\)）]$', '', cleaned)
    cleaned = cleaned.strip('+-')
    if not cleaned:
        return False
    # 纯数字或带小数点的数字
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


# ---- PDF 字符归一化（用于去重指纹匹配）----

def _normalize_cell_content(cell: str) -> str:
    """归一化单元格内容，处理 PDF 提取产生的字符编码差异。

    PDF 提取常产生各种变体字符：
    - 各种 dash/hyphen：– — ‐ ‑ ‒ ― − → 统一为 -
    - 零宽字符：\\u200b \\u200c \\u200d \\ufeff
    - 不间断空格：\\u00a0 → 普通空格
    - 全角空格：\\u3000 → 普通空格
    - 多余空白：合并为单空格
    - 大小写：统一转为小写
    """
    s = str(cell).strip()
    if not s:
        return ""
    # 1. 归一化各种 dash/hyphen 字符
    s = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u2043\-\u2500\u2501\u2502\u2503]', '-', s)
    # 2. 去除零宽字符
    s = re.sub(r'[\u200b\u200c\u200d\ufeff\u200e\u200f]', '', s)
    # 3. 不间断空格和全角空格转普通空格
    s = s.replace('\u00a0', ' ').replace('\u3000', ' ')
    # 4. 空白标准化（多个空格合并为1个）
    s = re.sub(r'\s+', ' ', s)
    # 5. 小写化（英文忽略大小写差异）
    s = s.lower()
    return s


def _is_uniform_short_row(row: List[str]) -> bool:
    """判断一行是否全由相同的短词填充（如整行都是"亿元。"）

    这类行不是表头，也不是数据，而是页面排版残留。
    """
    nonempty_texts = [
        (c or "").strip() for c in row
        if not _is_effectively_empty(c)
    ]
    if len(nonempty_texts) < 2:
        return False
    unique = set(nonempty_texts)
    # 所有非空格内容完全相同且极短（≤5 字符）→ 排版残留
    if len(unique) == 1 and len(nonempty_texts[0]) <= 5:
        return True
    return False


def _is_description_row(row: List[str]) -> bool:
    """
    判断一行是否为描述文本（而非表头或数据）

    特征：
    - 任意列存在长文本（>20字符），且该列是唯一的非空内容所在
    - 或首列文本很长（>15字符），且只有首列有内容
    - 或首列文本以句号结尾且较长
    - 或首列文本包含"下表"/"如下"/"如图所示"等引导词
    - 或全行由相同的短词重复填充（排版残留，如全行"亿元。"）
    - 或首列是长句、其他列全是短重复词（正文+页面残留混合行）
    - 或 col0 为空但其他某列有长描述文本（如"下表列出于所示日期..."）
    """
    col0 = (row[0] or "").strip() if row else ""

    # 收集所有非空列信息
    nonempty_indices = [
        j for j, c in enumerate(row)
        if not _is_effectively_empty(c)
    ]
    if not nonempty_indices:
        return False

    # 场景：col0 为空时，检查其他列是否有描述文本
    if not col0:
        if len(nonempty_indices) == 1:
            # 只有一个非空列且不在col0 → 可能是"下表..."这类引导文本
            solo_idx = nonempty_indices[0]
            solo_text = (row[solo_idx] or "").strip()
            if len(solo_text) > 12:
                return True
            for hint in _DESCRIPTION_HINTS:
                if hint in solo_text:
                    return True
        else:
            # col0 为空但有多列非空 → 拼接检查是否为被拆分的长描述文本
            combined = "".join((row[j] or "").strip() for j in nonempty_indices)
            if len(combined) > 25:
                # 多个非空列拼接后是长文本 → 描述文本被拆分
                return True
            return False
        return False
        # col0 有值，走原有逻辑
        other_filled = any(
            not _is_effectively_empty(c) for j, c in enumerate(row) if j > 0
        )

        if other_filled:
            # 场景A：全行都是同一个短词（如"亿元。"×N）→ 排版残留
            if _is_uniform_short_row(row):
                return True

            # 场景B：首列是长句 + 其余列都是短重复词 → 正文+页面残留
            if len(col0) > 15:
                other_texts = [
                    (c or "").strip() for j, c in enumerate(row)
                    if j > 0 and not _is_effectively_empty(c)
                ]
                if other_texts:
                    unique_texts = set(other_texts)
                    avg_len = sum(len(t) for t in other_texts) / len(other_texts)
                    if avg_len < 8 and len(unique_texts) <= 2:
                        return True

            return False

        # 以下：只有首列有内容

        if len(col0) > 15:
            return True

        if col0.endswith('。') and len(col0) > 8:
            return True

        for hint in _DESCRIPTION_HINTS:
            if hint in col0:
                return True

    return False


def _is_header_row(row: List[str], data_cols: int) -> bool:
    """
    判断一行是否为表头行（位于数据行上方，包含标签性文本）

    表头特征：
    - 非空单元格较多（但非数值）
    - 文本内容看起来像标签/分类名
    - 位于数据区域上方
    """
    nonempty = _count_nonempty_cells(row)
    if nonempty == 0:
        return False

    numeric_count = sum(1 for c in row if _is_numeric_cell(c))

    # 如果大部分非空单元格是数值，不是表头
    if numeric_count > 0 and numeric_count >= nonempty * 0.4:
        return False

    # 表头的非空单元格数通常 >= 1，但不会全部是数值
    return nonempty > 0


def _is_data_row(row: List[str], min_numeric: int = 2) -> bool:
    """
    判断一行是否为数据行

    数据行特征：
    - 包含足够多的数值单元格
    - 或非空单元格的比例较高且包含数值
    """
    numeric_count = sum(1 for c in row if _is_numeric_cell(c))
    if numeric_count >= min_numeric:
        return True

    # 放宽条件：如果非空单元格很多且至少有一些数值
    nonempty = _count_nonempty_cells(row)
    if nonempty >= 3 and numeric_count >= 1:
        return True

    return False


def _is_standalone_caption(row: List[str]) -> bool:
    """
    判断一行是否为独立的表标题（如"表1：减值损失构成情况"）

    特征：只有 col[0] 有文本，且以"表"字开头
    """
    col0 = (row[0] or "").strip()
    if not col0:
        return False
    other_filled = any(
        not _is_effectively_empty(c) for j, c in enumerate(row) if j > 0
    )
    if other_filled:
        return False
    return col0.startswith('表') or col0.startswith('Table')


def _normalize_row_width(row: List[str], target_cols: int) -> List[str]:
    """将一行标准化到目标列数"""
    if len(row) >= target_cols:
        return row[:target_cols]
    return row + [""] * (target_cols - len(row))


# ============================================================
# 第一步：定位数据区域
# ============================================================

# ---- 子表边界检测关键词 ----
# 子标题行可能包含的结构关键词（表示新表格的开始）
_SUB_TITLE_STRUCTURE_KW = (
    '资产负债表', '利润表', '现金流量表', '损益表',
    '余额', '损益', '现金流', '经营情况', '重大交易',
)
# 非子标题关键词（不应被当成子标题）
_NOT_SUB_TITLE_KW = (
    '合计', '总计', '小计', '平均', '其中', '减：', '加：',
)


def _find_all_data_clusters(
    table_data: List[List[str]],
    total_rows: int,
    total_cols: int
) -> Tuple[List[Tuple[int, int]], List[int], set]:
    """查找所有数据区块（core logic extracted from locate_data_region Steps 1-3）。

    Returns:
        (clusters, row_scores, data_cols)
        clusters: List of (start, end) for each data cluster
        row_scores: per-row density scores
        data_cols: set of column indices identified as data columns
    """
    # ---- Step 1: 列级数值分析 ----
    col_numeric_count = [0] * total_cols
    for row in table_data:
        for j in range(min(len(row), total_cols)):
            if _is_numeric_cell(row[j]):
                col_numeric_count[j] += 1

    data_cols = {
        j for j, cnt in enumerate(col_numeric_count)
        if cnt >= 2
    }
    if not data_cols:
        return [], [], data_cols

    # ---- Step 2: 每行在数据列上的密度评分 ----
    row_scores = []
    for row in table_data:
        score = 0
        for j in data_cols:
            cell = row[j] if j < len(row) else ""
            if _is_numeric_cell(cell):
                score += 1
        row_scores.append(score)

    # ---- Step 3: 按密度 > 0 分组为连续区块（循环桥接间隙行） ----
    clusters = []
    i = 0
    while i < total_rows:
        if row_scores[i] > 0:
            start = i
            while i < total_rows and row_scores[i] > 0:
                i += 1
            end = i

            # 循环桥接：持续跳过间隙行，直到遇到真正的空白或表头
            while i < total_rows and row_scores[i] == 0:
                gap_row = table_data[i]
                gap_col0 = (gap_row[0] or "").strip() if gap_row else ""
                gap_nonempty = _count_nonempty_cells(gap_row)
                gap_has_any_num = any(_is_numeric_cell(c) for c in gap_row)

                # 间隙行类型判断
                gap_is_label = bool(
                    gap_col0
                    and not _is_numeric_cell(gap_col0)
                    and gap_nonempty >= 1
                )
                gap_is_data_cont = bool(
                    gap_has_any_num and gap_nonempty >= 2
                )

                if (gap_is_label or gap_is_data_cont) and i + 1 < total_rows and row_scores[i + 1] > 0:
                    i += 1  # 跳过间隙行
                    while i < total_rows and row_scores[i] > 0:
                        i += 1
                    end = i
                else:
                    break  # 无法桥接，真正的分隔

            clusters.append((start, end))
        else:
            i += 1

    return clusters, row_scores, data_cols


def _is_new_sub_table_boundary(
    table_data: List[List[str]],
    gap_start: int,   # first gap row (end of prev cluster)
    gap_end: int,     # first row of next cluster (exclusive)
    data_cols: set,
    total_rows: int,
) -> bool:
    """检测两个数据区块之间的间隙是否为"子表边界"。

    判断依据：
    1. 间隙中有一个"子标题行"（col0 独有文本，其他列空，含结构关键词或长度≥4）
    2. 子标题后紧跟新的列头行（多列纯文本，非数值）
    3. 不是分节分隔（同一表格多期数据）

    Returns:
        True 如果间隙表明这是两张不同的表
    """
    if gap_start >= gap_end:
        return False

    # 收集间隙行
    gap_rows = table_data[gap_start:gap_end]

    # 跳过纯空行，找到第一个有内容的行
    first_nonempty = gap_start
    while first_nonempty < gap_end:
        if _count_nonempty_cells(table_data[first_nonempty]) > 0:
            break
        first_nonempty += 1
    if first_nonempty >= gap_end:
        return False

    # ---- 检测 0: 间隙行自身是否构成"完整表头" ----
    # 场景：两个数据区块之间的间隙直接就是新表格的完整表头
    # （如 "本集团/本行" 组织标签 + "2024年/2023年" 列标签）
    # 此时不需要 col0 有子标题文本，间隙行本身就是表头标识
    sub_title_row_idx = -1

    gap_meaningful_rows = sum(
        1 for ri in range(first_nonempty, gap_end)
        if _count_nonempty_cells(table_data[ri]) >= 2
    )
    if gap_meaningful_rows >= 2:
        gap_all_cells = [
            str(c).strip() for ri in range(first_nonempty, gap_end)
            for c in table_data[ri] if str(c).strip()
        ]
        gap_text = ' '.join(gap_all_cells)
        gap_numeric = sum(1 for c in gap_all_cells if _is_numeric_cell(str(c)))
        gap_ratio = gap_numeric / max(len(gap_all_cells), 1)

        has_year = bool(re.search(r'\d{4}\s*年', gap_text))
        has_org = any(kw in gap_text for kw in
                      ('本集团', '本行', '本公司', '本行及', '本行和'))
        has_col_kw = any(kw in gap_text for kw in
                         ('项目', '金额', '余额', '比例', '占比', '附注'))

        # 表头特征：含年份列标签 + 数值占比 < 0.3（表头主要是文字）
        if (has_year or (has_org and has_col_kw)) and gap_ratio < 0.3:
            sub_title_row_idx = first_nonempty

    # ---- 检测 1: 是否存在"子标题行" ----
    # 子标题行特征：col0 有文本，其他列几乎全空，非合计类关键词
    if sub_title_row_idx < 0:
        for ri in range(first_nonempty, gap_end):
            row = table_data[ri]
            col0 = (row[0] or "").strip() if row else ""

            # 场景 A：col0 有文本的子标题检测
            if col0 and not _is_numeric_cell(col0):
                other_nonempty = _count_nonempty_cells(row[1:]) if len(row) > 1 else 0
                if other_nonempty <= 1:
                    # 排除合计类关键词
                    if not any(kw in col0 for kw in _NOT_SUB_TITLE_KW):
                        # 必须满足：含结构关键词 OR 长度≥4个中文字符
                        has_structure_kw = any(kw in col0 for kw in _SUB_TITLE_STRUCTURE_KW)
                        chinese_chars = sum(1 for ch in col0 if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf')
                        is_long_enough = chinese_chars >= 4 or (not chinese_chars and len(col0) >= 8)

                        if has_structure_kw or is_long_enough:
                            sub_title_row_idx = ri
                            break

            # 场景 B：非 col0 位置的日期分隔行检测
            # 特征：某个非 col0 列包含日期文本（如"2023年12月31日"），
            #       且该行只有这一个有效非空单元格
            if not col0:
                nonempty_in_row = [(j, (row[j] or "").strip())
                                   for j in range(len(row))
                                   if not _is_effectively_empty(row[j])]
                if len(nonempty_in_row) == 1:
                    j, text = nonempty_in_row[0]
                    # 包含年份标记且长度合适（≤15，避免长描述文本误判）
                    if re.search(r'\d{4}\s*年', text) and len(text) <= 15:
                        sub_title_row_idx = ri
                        break

    if sub_title_row_idx < 0:
        return False

    # ---- 检测 2: 子标题后是否存在新列头行 ----
    # 新列头行特征：非空列≥3 且 数值列≤1（排除数据行被误判为列头）
    header_candidates_after = []
    for ri in range(sub_title_row_idx + 1, gap_end):
        row = table_data[ri]
        nonempty = _count_nonempty_cells(row)
        if nonempty < 2:
            continue
        num_count = sum(1 for c in row if _is_numeric_cell(c))
        if nonempty >= 3 and num_count <= 1:
            header_candidates_after.append(ri)
        elif nonempty >= 2 and num_count <= 1 and any(
            kw in ''.join(str(c) for c in row if not _is_effectively_empty(c))
            for kw in ('注释', '项目', '金额', '余额', '比例', '占比')
        ):
            header_candidates_after.append(ri)

    if not header_candidates_after:
        return False

    # ---- 检测 3: 排除分节分隔（同一表格多期） ----
    # 分节分隔特征：子标题附近有纯年份行（如"2024年"）或重复的阶段关键词
    gap_all_text = ' '.join(
        c for row in gap_rows for c in row
        if c.strip() and not _is_numeric_cell(c)
    )
    has_year_only = bool(re.search(r'^\s*\d{4}\s*年\s*$', gap_all_text, re.MULTILINE))
    has_section_kw = any(kw in gap_all_text for kw in ('阶段一', '阶段二', '阶段三', '阶段四'))

    if has_year_only and has_section_kw:
        return False

    return True


def _expand_data_boundaries(
    table_data: List[List[str]],
    data_start: int,
    data_end: int,
    data_cols: set,
    total_rows: int,
) -> Tuple[int, int]:
    """对单个数据区块进行起始/终止边界扩展（原 locate_data_region Steps 4-5）。"""
    # ---- Step 4: 起始边界确认 ----
    for _ in range(2):
        if data_start <= 0:
            break
        prev = table_data[data_start - 1]
        prev_nonempty = _count_nonempty_cells(prev)
        prev_has_data_num = any(
            _is_numeric_cell(prev[j]) for j in data_cols if j < len(prev)
        )
        prev_has_any_num = any(_is_numeric_cell(c) for c in prev)
        prev_col0 = (prev[0] or "").strip() if prev else ""

        if prev_has_data_num:
            data_start -= 1
        elif prev_col0 and prev_nonempty <= 2:
            data_start -= 1
        elif prev_has_any_num and prev_nonempty >= 2:
            data_start -= 1
        else:
            break

    # ---- Step 5: 终止边界确认 ----
    _SUMMARY_KEYWORDS = ('合计', '总计', '小计', '净值', '余额', '净额', '合  计')
    while data_end < total_rows:
        next_row = table_data[data_end]
        next_has_data_num = any(
            _is_numeric_cell(next_row[j]) for j in data_cols if j < len(next_row)
        )
        next_has_any_num = any(_is_numeric_cell(c) for c in next_row)
        next_nonempty = _count_nonempty_cells(next_row)

        if next_has_data_num:
            data_end += 1
        elif next_has_any_num and next_nonempty >= 2:
            data_end += 1
        else:
            next_col0 = (next_row[0] or "").strip() if next_row else ""
            is_summary = any(kw in next_col0 for kw in _SUMMARY_KEYWORDS)
            if is_summary and next_nonempty > 1:
                data_end += 1
            else:
                break

    return (data_start, data_end)


def _find_sub_title_row(
    table_data: List[List[str]],
    search_start: int,
    search_end: int,
) -> int:
    """在指定范围内查找子标题行（col0 独有文本 + 结构关键词）。

    Returns:
        子标题行的行号，找不到返回 -1
    """
    for ri in range(search_start, search_end):
        row = table_data[ri]
        col0 = (row[0] or "").strip() if row else ""
        if not col0 or _is_numeric_cell(col0):
            continue
        other_nonempty = _count_nonempty_cells(row[1:]) if len(row) > 1 else 0
        if other_nonempty > 1:
            continue
        if any(kw in col0 for kw in _NOT_SUB_TITLE_KW):
            continue
        has_structure_kw = any(kw in col0 for kw in _SUB_TITLE_STRUCTURE_KW)
        chinese_chars = sum(1 for ch in col0 if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf')
        is_long_enough = chinese_chars >= 4 or (not chinese_chars and len(col0) >= 8)
        if has_structure_kw or is_long_enough:
            return ri
    return -1


def locate_data_region(table_data: List[List[str]]) -> Optional[Tuple[int, int]]:
    """
    定位数据区域（返回 data_start_row, data_end_row）

    整体分析策略（全表视角，不只看单行）：
    1. 列级统计：按列统计数值分布，识别"数据列"（有多行数值集中的列）
    2. 行级评分：基于数据列，为每行计算密度得分，形成全表密度图
    3. 聚类定位：找最大连续高密度区块作为数据区域（允许标签行间隙桥接）
    4. 边界确认：起始/终止边界各向外检查，确认是否遗漏标签行或汇总行

    如果找不到数据列或数据行，返回 None。
    """
    if not table_data or len(table_data) < 2:
        return None

    total_rows = len(table_data)
    total_cols = max(len(row) for row in table_data) if table_data else 0
    if total_cols == 0:
        return None

    clusters, row_scores, data_cols = _find_all_data_clusters(
        table_data, total_rows, total_cols
    )

    if not clusters:
        return None

    # 取总分最高的区块（不是最长，是数据密度总和最高——即核心数据区）
    best = max(clusters, key=lambda c: sum(row_scores[c[0]:c[1]]))
    data_start, data_end = best

    # 边界扩展
    data_start, data_end = _expand_data_boundaries(
        table_data, data_start, data_end, data_cols, total_rows
    )

    return (data_start, data_end)


def _detect_orphan_data_rows(
    table_data: List[List[str]],
    data_end: int,
) -> Optional[dict]:
    """检测 data_end 之后是否有孤儿数据区（疑似多表合并）。

    在 locate_data_region 确定数据区边界后，检查边界之后是否还有
    含数值的数据行被丢弃。如果是，说明可能发生了多表合并。
    这些信息会作为 HIGH 异常标记保存，供 LLM 手动确认时使用。

    Args:
        table_data: 已统一列宽的完整表格数据
        data_end: locate_data_region 返回的数据结束行号

    Returns:
        None 如果没有孤儿数据
        dict: {
            "orphan_start": int,       # 孤儿数据起始行
            "orphan_end": int,         # 孤儿数据结束行
            "separator_rows": List[str],  # 分隔行原文
            "orphan_preview": List[str],  # 孤儿数据预览（前5行）
        }
    """
    total_rows = len(table_data)

    # 从 data_end 开始扫描，跳过空行
    ri = data_end
    while ri < total_rows:
        row = table_data[ri]
        if all(_is_effectively_empty(c) for c in row):
            ri += 1
            continue
        break

    if ri >= total_rows:
        return None

    # 收集分隔行（data_end 到孤儿数据起始之间的非空行）
    separator_start = data_end
    separator_end = ri

    # 查找孤儿数据起始：第一个在数据列上包含数值的行
    # 先用 locate_data_region 的列发现逻辑确定数据列
    total_cols = max(len(row) for row in table_data) if table_data else 0
    col_numeric_count = [0] * total_cols
    for row in table_data:
        for j in range(min(len(row), total_cols)):
            if _is_numeric_cell(row[j]):
                col_numeric_count[j] += 1
    data_cols = {j for j, cnt in enumerate(col_numeric_count) if cnt >= 2}

    orphan_start = ri
    while orphan_start < total_rows:
        row = table_data[orphan_start]
        has_data_num = any(
            _is_numeric_cell(row[j])
            for j in data_cols if j < len(row)
        ) if data_cols else any(_is_numeric_cell(c) for c in row)
        if has_data_num:
            break
        orphan_start += 1

    if orphan_start >= total_rows:
        return None

    # 收集孤儿数据区
    orphan_end = orphan_start
    while orphan_end < total_rows:
        row = table_data[orphan_end]
        if all(_is_effectively_empty(c) for c in row):
            # 遇到全空行停止（可能表格结束）
            break
        orphan_end += 1

    # 确认孤儿区确实有数据（至少2行且有数值的行≥2行）
    orphan_rows = table_data[orphan_start:orphan_end]
    orphan_num_count = sum(
        1 for row in orphan_rows
        if any(_is_numeric_cell(c) for c in row)
    )
    if orphan_num_count < 2:
        return None

    # 收集分隔行原文
    separator_rows = []
    for si in range(separator_start, separator_end):
        row = table_data[si]
        text = " ".join(c for c in row if not _is_effectively_empty(c))
        if text:
            separator_rows.append(text)

    # 孤儿数据预览（前5行）
    orphan_preview = []
    for row in orphan_rows[:5]:
        text = " | ".join(c for c in row if not _is_effectively_empty(c))
        if text:
            orphan_preview.append(text)

    return {
        "orphan_start": orphan_start,
        "orphan_end": orphan_end,
        "separator_rows": separator_rows,
        "orphan_preview": orphan_preview,
    }


# ============================================================
# 第二步：确定数据列数（带列剪除和偏移合并）
# ============================================================

def count_data_columns(data_rows: List[List[str]]) -> int:
    """
    从数据行中确定列数

    取所有数据行中列数的众数（最常见列数），忽略极端异常值。
    """
    if not data_rows:
        return 0

    col_counts = [len(row) for row in data_rows]
    from collections import Counter
    counter = Counter(col_counts)
    most_common = counter.most_common(1)
    if most_common:
        return most_common[0][0]
    return max(col_counts) if col_counts else 0


def prune_empty_and_merge_columns(
    table_data: List[List[str]],
    data_rows: List[List[str]]
) -> Tuple[List[List[str]], int]:
    """
    对表格数据进行列级清理：

    1. **剪除全空列**：某列在所有行（含表头+数据）中填充率 < 3% → spacer 列，删除
    2. **合并互补偏移列**：相邻列在数据行中极少同时有值（每行最多1个有值）
       且各自独立有数据 → 同一逻辑列因 PDF 排版偏移，合并
    3. **保留 header-bearing 列**：即使某列仅在表头行有值，也保留（不剪除）

    Args:
        table_data: 完整表格数据（含表头）
        data_rows: 数据行子集

    Returns:
        (cleaned_table, new_col_count)
    """
    if not table_data or not data_rows:
        return table_data, 0

    max_cols = max(len(row) for row in table_data)
    if max_cols <= 1:
        return table_data, max_cols

    normalized = [_normalize_row_width(row, max_cols) for row in table_data]
    total_all_rows = len(normalized)
    total_data_rows = len(data_rows)

    # ---- Pass 1: 基于全表检测全空 spacer 列 ----
    # 既看数据行填充率，也看全表填充率。只有两处都极低才剪除
    empty_col_mask = [False] * max_cols

    for col_idx in range(max_cols):
        # 数据行填充率
        data_filled = sum(
            1 for row in data_rows
            if col_idx < len(row) and not _is_effectively_empty(row[col_idx])
        )
        # 全表填充率
        all_filled = sum(
            1 for row in normalized
            if col_idx < len(row) and not _is_effectively_empty(row[col_idx])
        )

        data_fill_rate = data_filled / total_data_rows if total_data_rows > 0 else 0
        all_fill_rate = all_filled / total_all_rows if total_all_rows > 0 else 0

        # 剪除条件：数据行填充率 < 5% 且全表填充率也 < 5%
        if data_fill_rate < 0.05 and all_fill_rate < 0.05:
            empty_col_mask[col_idx] = True
            logger.debug(f"  col {col_idx}: data_fill={data_fill_rate:.1%}, "
                         f"all_fill={all_fill_rate:.1%} → PRUNE")
        else:
            logger.debug(f"  col {col_idx}: data_fill={data_fill_rate:.1%}, "
                         f"all_fill={all_fill_rate:.1%} → KEEP")

    # ---- Pass 2: 互补偏移列检测 ----
    merge_pairs = []
    col_skipped = set()

    for col_idx in range(max_cols - 1):
        if col_idx in col_skipped:
            continue
        if empty_col_mask[col_idx] or empty_col_mask[col_idx + 1]:
            continue

        overlap_count = 0
        fill_a = 0
        fill_b = 0
        for row in data_rows:
            a = not _is_effectively_empty(row[col_idx]) if col_idx < len(row) else False
            b = not _is_effectively_empty(row[col_idx + 1]) if col_idx + 1 < len(row) else False
            if a and b:
                overlap_count += 1
            if a:
                fill_a += 1
            if b:
                fill_b += 1

        if overlap_count <= 1 and fill_a >= 1 and fill_b >= 1:
            merge_pairs.append((col_idx, col_idx + 1))
            col_skipped.add(col_idx)
            col_skipped.add(col_idx + 1)
            logger.debug(f"  merge cols ({col_idx},{col_idx+1}) (互补数据列): overlap={overlap_count}, "
                         f"fill_a={fill_a}/{total_data_rows}, fill_b={fill_b}/{total_data_rows}")

    # ---- Build column mapping ----
    # col_map: old_col_idx → new_col_idx (-1 = removed/merged)
    col_map = []
    skip_next = False
    for col_idx in range(max_cols):
        if skip_next:
            skip_next = False
            continue

        if empty_col_mask[col_idx]:
            col_map.append(-1)
            continue

        merged = False
        for left, right in merge_pairs:
            if col_idx == left:
                col_map.append(len([m for m in col_map if m >= 0]))
                col_map.append(-1)
                skip_next = True
                merged = True
                break

        if not merged:
            col_map.append(len([m for m in col_map if m >= 0]))

    new_col_count = sum(1 for m in set(col_map) if m >= 0)

    if new_col_count == max_cols:
        return normalized, max_cols

    logger.info(f"  列清理: {max_cols}→{new_col_count} (剪除 {sum(1 for m in col_map if m < 0)} 列)")

    # ---- Rebuild all rows (simplified, correct merge logic) ----
    rebuilt = []
    for row in normalized:
        new_row = []
        col_idx = 0
        while col_idx < max_cols:
            m = col_map[col_idx]
            if m < 0:
                col_idx += 1
                continue

            # Check if this column is the LEFT of a merge pair
            if col_idx + 1 < max_cols and col_map[col_idx + 1] < 0:
                left_val = (row[col_idx].strip()
                            if col_idx < len(row) and not _is_effectively_empty(row[col_idx])
                            else "")
                right_val = (row[col_idx + 1].strip()
                             if col_idx + 1 < len(row) and not _is_effectively_empty(row[col_idx + 1])
                             else "")
                merged = left_val if left_val else right_val
                new_row.append(merged)
                col_idx += 2  # skip both
            else:
                cell = (row[col_idx].strip()
                        if col_idx < len(row) and not _is_effectively_empty(row[col_idx])
                        else "")
                new_row.append(cell)
                col_idx += 1

        while len(new_row) < new_col_count:
            new_row.append("")
        rebuilt.append(new_row[:new_col_count])

    return rebuilt, new_col_count


# ============================================================
# 第二步前半：分析底层表头 → 确定预期数据列数
# ============================================================

def analyze_bottom_header(
    bottom_header_row: List[str],
    data_rows: Optional[List[List[str]]] = None,
    anomalies: Optional[List[RepairAnomaly]] = None,
) -> Tuple[int, List[int]]:
    """分析底层表头（最靠近数据的那行），确定预期数据列数。

    底层表头定义了表格的"真列结构"——每个非空单元格对应一个实际数据列。
    这是整个修复过程最稳定的参考基准。

    增强（数据感知）：
    - 第一列（col 0）即使表头为空，若数据行中有显著的文本内容（如行标签），
      也会自动补充为锚定列。这解决了"第一列表头为空但数据有值"的场景。
    - 第一列表头比较特殊：它往往是单位说明（如"（人民币百万元）"）或为空，
      不应该作为判断全表列结构的唯一依据。

    Args:
        bottom_header_row: 底层表头行（最靠近数据行的那一行）
        data_rows: 数据行（可选，用于数据感知补充锚定列）
        anomalies: 异常收集列表（可选），检测到的异常会追加到此列表

    Returns:
        (expected_data_cols, anchor_positions)
        - expected_data_cols: 预期数据列数 N
        - anchor_positions: 表头非空列在原表中的位置索引列表

    Example:
        底层表头: ["（人民币百万元）", "", "12月31日", "", "12月31日"]
        → (3, [0, 2, 4])
        表示预期 3 列数据，分别在原表 col 0, 2, 4
    """
    _anomalies: List[RepairAnomaly] = anomalies if anomalies is not None else []

    positions = []
    for i, cell in enumerate(bottom_header_row):
        if not _is_effectively_empty(cell):
            positions.append(i)

    # ---- 数据感知增强 ----
    # 场景：第一列表头为空（无单位说明），但数据行有行标签
    # 例如表头 ["", "2024年", "2023年"]，数据 ["资产", 100, 200]
    # 此时 col 0 应该被识别为锚定列（行标签列）
    weak_anchor_added = False
    if data_rows and data_rows[0] and 0 not in positions:
        col0_filled = sum(
            1 for r in data_rows
            if len(r) > 0 and not _is_effectively_empty(r[0])
        )
        fill_rate = col0_filled / len(data_rows) if data_rows else 0
        # 第一列填充率 >= 30% → 视为行标签列，补充为锚定列
        if fill_rate >= 0.3:
            positions.insert(0, 0)
            weak_anchor_added = True
            logger.debug(
                f"  analyze_bottom_header: data-aware added col 0 as anchor "
                f"(fill_rate={fill_rate:.1%}, header was empty)"
            )

    # ---- 锚定列校正 ----
    # 场景：表头提取时多了空列，导致锚定列位置与实际数据列不匹配
    # 例如表头 ["", "", "12月31日", "12月31日"] → 锚定 [2, 3]
    # 但数据只有 3 列真正有值：col 0(行标签), col 1(数值), col 2(数值)
    # col 3 在数据中全空 → 应该把锚定从 col 3 移到 col 1
    #
    # 策略：对每个锚定列，如果它在数据行中的填充率 < 10%，
    # 尝试向附近寻找填充率 >= 50% 的非锚定列来替换
    original_positions = list(positions)  # 保存用于异常检测
    anchor_shifts: List[Dict] = []  # 记录锚定列偏移

    if data_rows:
        total_data = len(data_rows)
        # 计算所有列的数据填充率
        max_col = max(
            max(len(r) for r in data_rows),
            len(bottom_header_row)
        )
        data_fills = {}
        for col in range(max_col):
            filled = sum(
                1 for r in data_rows
                if col < len(r) and not _is_effectively_empty(r[col])
            )
            data_fills[col] = filled / total_data if total_data > 0 else 0

        LOW_THRESHOLD = 0.10  # 低填充阈值：锚定列数据极少
        HIGH_THRESHOLD = 0.50  # 高填充阈值：非锚定列数据丰富

        adjusted = []
        used_cols = set()  # 已占用的列位置
        for anchor in positions:
            fill = data_fills.get(anchor, 0)
            if fill >= LOW_THRESHOLD:
                # 锚定列有足够数据 → 保留
                adjusted.append(anchor)
                used_cols.add(anchor)
            else:
                # 锚定列数据太少 → 寻找附近有数据的非锚定列来替换
                positions_set = set(positions)
                best_col = None
                best_dist = 999

                for col in range(max_col):
                    if col in positions_set or col in used_cols:
                        continue
                    if data_fills.get(col, 0) >= HIGH_THRESHOLD:
                        dist = abs(col - anchor)
                        if dist < best_dist:
                            best_dist = dist
                            best_col = col

                if best_col is not None:
                    adjusted.append(best_col)
                    used_cols.add(best_col)
                    anchor_shifts.append({
                        "from_col": anchor,
                        "to_col": best_col,
                        "header_fill": fill,
                        "data_fill": data_fills[best_col],
                    })
                    logger.debug(
                        f"  analyze_bottom_header: anchor shift col {anchor}"
                        f"→{best_col} (header fill={fill:.1%}, "
                        f"data fill={data_fills[best_col]:.1%})"
                    )
                else:
                    # 找不到替代 → 仍保留原锚定（可能整个表头都有问题）
                    adjusted.append(anchor)
                    used_cols.add(anchor)

        if adjusted != positions:
            adjusted.sort()
            logger.info(
                f"  analyze_bottom_header: anchors adjusted "
                f"{positions}→{adjusted}"
            )
            positions = adjusted

        # ---- 伴随标签列检测 ----
        # 检测非锚定列中是否存在"伴随标签列"（companion label column）
        # 特征：高填充率(>=40%) + 高文本率(>=60%) + 非锚定列
        # 这类列通常是多级行标签结构中的子标签列（如场景→子场景），
        # 底层表头为空但数据行有实质性文本内容。
        # 例如：col0="上升100个基点", col1="存放央行款项利率不变", col2+数据
        positions_set = set(positions)
        companion_added = []
        for col in range(max_col):
            if col in positions_set:
                continue
            fill = data_fills.get(col, 0)
            if fill < 0.4:
                continue
            # 计算文本率（非数值单元格占比）
            nonempty_count = 0
            text_count = 0
            for r in data_rows:
                if col < len(r) and not _is_effectively_empty(r[col]):
                    nonempty_count += 1
                    if not _is_numeric_cell(r[col]):
                        text_count += 1
            if nonempty_count > 0 and text_count / nonempty_count >= 0.6:
                companion_added.append(col)
                logger.debug(
                    f"  analyze_bottom_header: companion label col {col} "
                    f"(fill={fill:.1%}, text_rate={text_count / nonempty_count:.1%})"
                )

        if companion_added:
            positions = sorted(positions + companion_added)
            logger.info(
                f"  analyze_bottom_header: added companion label cols "
                f"{companion_added} → anchors now {positions}"
            )
            # 记录异常
            for col in companion_added:
                _anomalies.append(RepairAnomaly(
                    type=WEAK_ANCHOR,
                    severity=ANOMALY_LOW,
                    description=(
                        f"第{col}列为伴随标签列（填充率{data_fills[col]:.1%}，"
                        f"文本率{text_count / nonempty_count:.1%}），"
                        f"已自动补充为锚定列"
                    ),
                    location="bottom_header",
                    details={
                        "column": col,
                        "fill_rate": round(data_fills[col], 3),
                        "text_rate": round(text_count / nonempty_count, 3) if nonempty_count > 0 else 0,
                        "reason": "companion_label",
                    },
                    confidence=0.55,
                ))

    # ---- 表头左移检测 ----
    # 场景：底部表头整体向左偏移了 1 列。
    # 例如表头 ["2024年","2023年","2024年","2023年","2024年","2023年",""]，
    # 实际数据有 7 列，其中 col0 是行标签列（如"年初余额"），
    # 但表头 col0 是"2024年"（子标签），导致表头锚定与实际数据列错位。
    #
    # 检测条件：
    # 1. col0 在锚定列中（表头有值）
    # 2. 数据 col0 文本率 ≥ 70%（行标签特征）+ 填充率 ≥ 70%
    # 3. 数据最后一个有值列 > 表头最后一个锚定列（有列没被覆盖）
    header_left_shifted = False
    if data_rows and 0 in positions and len(positions) >= 2:
        col0_total = sum(
            1 for r in data_rows
            if len(r) > 0 and not _is_effectively_empty(r[0])
        )
        col0_text = sum(
            1 for r in data_rows
            if len(r) > 0
            and not _is_effectively_empty(r[0])
            and not _is_numeric_cell(r[0])
        )
        col0_text_rate = col0_text / max(col0_total, 1)
        col0_fill_rate = col0_total / len(data_rows)

        max_data_col = max(len(r) for r in data_rows)
        data_last_nonempty = max(
            j for j in range(max_data_col)
            if any(
                len(r) > j and not _is_effectively_empty(r[j])
                for r in data_rows
            )
        )
        header_last = max(positions)

        if (col0_text_rate >= 0.7 and col0_fill_rate >= 0.7
                and data_last_nonempty > header_last):
            # 补充缺失的尾部锚定列（表头为空但数据有值的列）
            missing_cols = [
                c for c in range(header_last + 1, data_last_nonempty + 1)
            ]
            positions = sorted(set(positions + missing_cols))
            header_left_shifted = True
            logger.info(
                f"  analyze_bottom_header: header left-shift detected, "
                f"added missing anchors {missing_cols}, "
                f"anchors now {positions} "
                f"(col0 is row-label col, text_rate={col0_text_rate:.1%})"
            )

    # ---- 异常检测 ----
    # A. weak_anchor: col0 被数据感知补充
    if weak_anchor_added:
        col0_header = bottom_header_row[0] if bottom_header_row else ""
        col0_filled = sum(
            1 for r in (data_rows or [])
            if len(r) > 0 and not _is_effectively_empty(r[0])
        )
        col0_total = len(data_rows) if data_rows else 0
        _anomalies.append(RepairAnomaly(
            type=WEAK_ANCHOR,
            severity=ANOMALY_LOW,
            description=(
                f"第0列表头为空（'{col0_header}'），但数据行中{col0_filled}/{col0_total}行有行标签，"
                f"已自动将 col 0 补充为锚定列"
            ),
            location="bottom_header",
            details={
                "column": 0,
                "header_text": col0_header,
                "data_fill_rate": round(col0_filled / col0_total, 3) if col0_total > 0 else 0,
            },
            confidence=0.6,
        ))

    # B. anchor_shift: 锚定列被数据感知校正
    for shift in anchor_shifts:
        old_col = shift["from_col"]
        new_col = shift["to_col"]
        confidence = 0.5 + 0.3 * (1.0 - min(shift.get("header_fill", 0) / 0.1, 1.0))
        _anomalies.append(RepairAnomaly(
            type=ANCHOR_SHIFT,
            severity=ANOMALY_MEDIUM,
            description=(
                f"锚定列 col {old_col} 在数据行中填充率仅 {shift['header_fill']:.1%}，"
                f"已自动移位至 col {new_col}（数据填充率 {shift['data_fill']:.1%}）"
            ),
            location="bottom_header",
            details={
                "original_anchor_col": old_col,
                "corrected_anchor_col": new_col,
                "original_fill_rate": round(shift["header_fill"], 3),
                "corrected_fill_rate": round(shift["data_fill"], 3),
            },
            confidence=round(confidence, 2),
        ))

    # C. data_header_mismatch: 数据列数与表头锚定列数严重不匹配
    if data_rows:
        data_col_count = max(
            sum(1 for c in r if not _is_effectively_empty(c))
            for r in data_rows
        ) if data_rows else 0
        header_col_count = len(positions)
        if header_col_count > 0 and abs(data_col_count - header_col_count) >= 3:
            _anomalies.append(RepairAnomaly(
                type=DATA_HEADER_MISMATCH,
                severity=ANOMALY_HIGH,
                description=(
                    f"数据行最多有 {data_col_count} 个非空列，"
                    f"但表头锚定列识别为 {header_col_count} 列，"
                    f"差异 ≥ 3 列（{abs(data_col_count - header_col_count)}列），"
                    f"表头结构可能不准确"
                ),
                location="table_level",
                details={
                    "data_nonempty_cols": data_col_count,
                    "header_anchor_cols": header_col_count,
                    "difference": abs(data_col_count - header_col_count),
                },
                confidence=0.4,
            ))

    n = len(positions)

    # ---- 计算 label_offset ----
    # label_offset = 开头连续标签列的数量（底层表头为空或为单位说明的列）
    # 这些列不参与表头标签填充，因为它们是行标签列/单位说明列
    label_offset = 0
    sorted_positions = sorted(positions)
    for pos in sorted_positions:
        if pos == label_offset:
            cell = bottom_header_row[pos] if pos < len(bottom_header_row) else ""
            if _is_effectively_empty(cell) or '（' in cell or '(' in cell:
                label_offset += 1
            else:
                break
        else:
            break

    # 表头左移时，col0 是行标签列，强制 label_offset ≥ 1
    if header_left_shifted and label_offset == 0:
        label_offset = 1

    logger.debug(
        f"  analyze_bottom_header: {n} anchors at positions {positions}, "
        f"label_offset={label_offset}"
    )
    return n, positions, label_offset


# ============================================================
# 第二步后半：用表头锚定列指导数据区清理
# ============================================================

def clean_data_region_by_header(
    data_rows: List[List[str]],
    all_rows: List[List[str]],
    anchor_positions: List[int],
) -> List[List[str]]:
    """根据底层表头的锚定列位置清理数据区域。

    清理策略：
    1. **锚定列**：底层表头有值的列 → 直接保留（这些是真数据列）
    2. **空白 spacer 列**：非锚定列且在所有行中全空 → 删除
    3. **数据错位列**：非锚定列但数据行有值 → 归并到最近的无重叠锚定列

    归并逻辑：
    - 遍历所有非锚定有值列，对每个列找到最近的锚定列
    - 计算与每个相邻锚定列的"重叠度"（同行同时有值的次数）
    - 归并到重叠度为 0（完全互补）的锚定列
    - 优先归并到左侧锚定列（更自然的数据流向）

    Args:
        data_rows: 数据行列表
        all_rows: 完整表格所有行（用于检测全空列）
        anchor_positions: 底层表头锚定列位置列表

    Returns:
        cleaned_data_rows: 列对齐到 anchor_positions 数量的数据行
    """
    if not data_rows:
        return data_rows

    max_cols = max(len(row) for row in all_rows)
    anchor_set = set(anchor_positions)
    expected_cols = len(anchor_positions)

    if expected_cols == 0:
        return data_rows

    # ---- Pass 1: 对每个非锚定列，判断是 spacer 还是 offset ----
    merge_target = {}  # non_anchor_col → target_anchor_col

    for col in range(max_cols):
        if col in anchor_set:
            continue

        # 统计该列在所有行（表头 + 数据）中的填充情况
        total_rows = len(all_rows)
        filled_in_all = sum(
            1 for row in all_rows
            if col < len(row) and not _is_effectively_empty(row[col])
        )
        filled_in_data = sum(
            1 for row in data_rows
            if col < len(row) and not _is_effectively_empty(row[col])
        )

        # 如果全表填充率 < 5% → spacer 列，直接跳过
        if total_rows > 0 and filled_in_all / total_rows < 0.05:
            logger.debug(f"  col {col}: spacer (fill={filled_in_all}/{total_rows})")
            continue

        # 如果数据行中完全无值 → spacer 列（可能仅表头有残余文本）
        if filled_in_data == 0:
            logger.debug(f"  col {col}: spacer (no data, header-only fill={filled_in_all})")
            continue

        # 非锚定列但在数据行中有值 → 偏移列，需要归并
        # 找到最近的左侧锚定列和右侧锚定列
        left_anchor = max((a for a in anchor_set if a < col), default=None)
        right_anchor = min((a for a in anchor_set if a > col), default=None)

        best_anchor = None
        best_overlap = float('inf')

        for anchor in [left_anchor, right_anchor]:
            if anchor is None:
                continue
            # 计算重叠：同行同时有值的次数
            overlap = 0
            for row in data_rows:
                a_filled = (anchor < len(row)
                            and not _is_effectively_empty(row[anchor]))
                c_filled = (col < len(row)
                            and not _is_effectively_empty(row[col]))
                if a_filled and c_filled:
                    overlap += 1
            if overlap < best_overlap:
                best_overlap = overlap
                best_anchor = anchor

        if best_anchor is not None:
            merge_target[col] = best_anchor
            logger.debug(
                f"  col {col}: offset → merge into anchor {best_anchor} "
                f"(overlap={best_overlap}, data_fill={filled_in_data}/{len(data_rows)})"
            )
        else:
            logger.warning(
                f"  col {col}: offset data ({filled_in_data} cells) "
                f"but no adjacent anchor found → data may be lost"
            )

    if not merge_target:
        # 无偏移列 → 直接截取锚定列
        cleaned = []
        for row in data_rows:
            new_row = []
            for anchor in anchor_positions:
                new_row.append(
                    row[anchor].strip() if anchor < len(row)
                    and not _is_effectively_empty(row[anchor]) else ""
                )
            cleaned.append(new_row)
        return cleaned

    # ---- Pass 2: 构建列映射并重建数据行 ----
    # col_map: old_col → new_col (-1 = 删除/归并)
    col_map = {}
    new_idx = 0
    for col in range(max_cols):
        if col in anchor_set:
            col_map[col] = new_idx
            new_idx += 1
        elif col in merge_target:
            col_map[col] = -1  # 归并到目标锚定列
        else:
            col_map[col] = -1  # spacer

    logger.info(
        f"  数据区清理: {max_cols}→{expected_cols} 列 "
        f"(保持 {len(anchor_set)} 锚定列, "
        f"归并 {len(merge_target)} 偏移列)"
    )

    # 重建
    cleaned = []
    for row in data_rows:
        new_row = [""] * expected_cols

        # 先填入锚定列的值
        for anchor in anchor_set:
            target = col_map[anchor]
            if anchor < len(row) and not _is_effectively_empty(row[anchor]):
                new_row[target] = row[anchor].strip()

        # 归并偏移列的值
        for offset_col, target_anchor in merge_target.items():
            target = col_map[target_anchor]
            if offset_col < len(row) and not _is_effectively_empty(row[offset_col]):
                offset_val = row[offset_col].strip()
                # 只在目标列为空时归并（不覆盖已有数据）
                if _is_effectively_empty(new_row[target]):
                    new_row[target] = offset_val

        cleaned.append(new_row)

    return cleaned


# ============================================================
# 第二步后半续：表头区清理（映射层 + 修复层）
# ============================================================

def clean_header_region(
    header_rows: List[List[str]],
    header_indices: List[int],
    anchor_positions: List[int],
    data_cols: int,
    data_rows: Optional[List[List[str]]] = None,
    anomalies: Optional[List[RepairAnomaly]] = None,
) -> Tuple[List[List[str]], List[int]]:
    """清理表头区域。

    分两个阶段：
    阶段1 — 映射层：将所有表头行映射到锚定列结构，处理表头列文本偏移
    阶段2 — 修复层：合并截断表头 + 恢复层级嵌套

    映射层处理策略（数据感知增强）：
    - 锚定列直接保留
    - 非锚定列若有表头文本 → 归并到最近的锚定列，优先选择下方有数据的锚定列
    - 非锚定列若为空 → 删除
    - 第一列（col 0）锚定更灵活：它往往是行标签列或单位说明，不强制对齐

    Args:
        header_rows: 表头行数据（从下到上排列）
        header_indices: 表头行在原表中的索引
        anchor_positions: 底层表头锚定列位置
        data_cols: 预期数据列数
        data_rows: 数据行（可选，用于判断锚定列是否有实际数据）
        anomalies: 异常收集列表（可选）

    Returns:
        (cleaned_header_rows, surviving_indices)
    """
    _anomalies: List[RepairAnomaly] = anomalies if anomalies is not None else []

    if not header_rows:
        return [], []

    anchor_set = set(anchor_positions)

    # ---- 数据感知：计算每个锚定列在数据行中的填充率 ----
    anchor_fill_rates = {}
    if data_rows:
        total_data = len(data_rows)
        for anchor in anchor_positions:
            filled = sum(
                1 for r in data_rows
                if anchor < len(r) and not _is_effectively_empty(r[anchor])
            )
            anchor_fill_rates[anchor] = filled / total_data if total_data > 0 else 0
        logger.debug(
            f"  clean_header_region: anchor fill rates = "
            f"{{{', '.join(f'{a}:{r:.1%}' for a, r in anchor_fill_rates.items())}}}"
        )

    MIN_DATA_FILL = 0.10  # 锚定列在数据行中最少填充率阈值

    # ---- 阶段1：映射 —— 将每行表头映射到锚定列 ----
    mapped_headers = []
    orphan_texts: List[Dict] = []  # 记录无法归并的表头文本

    for row_idx, row in enumerate(header_rows):
        new_row = [""] * data_cols
        for new_idx, anchor_col in enumerate(anchor_positions):
            if anchor_col < len(row):
                val = row[anchor_col]
                if not _is_effectively_empty(val):
                    new_row[new_idx] = val.strip()

        # 处理非锚定列的表头文本：归并到数据感知的最佳锚定列
        max_cols = len(row)
        for col in range(max_cols):
            if col in anchor_set:
                continue
            if col >= len(row) or _is_effectively_empty(row[col]):
                continue

            text = row[col].strip()

            # 选择目标锚定列：右侧优先 + 数据感知
            right_anchor = min((a for a in anchor_set if a > col), default=None)
            left_anchor = max((a for a in anchor_set if a < col), default=None)

            # 构建候选列表 [(anchor, is_right_side, data_fill_rate)]
            candidates = []
            for anchor in [right_anchor, left_anchor]:
                if anchor is not None:
                    fill = anchor_fill_rates.get(anchor, 1.0)  # 无 data_rows 时默认有数据
                    is_right = (anchor > col)
                    candidates.append((anchor, is_right, fill))

            if not candidates:
                # 非锚定列有文本但无相邻锚定列 → 孤立表头文本
                orphan_texts.append({
                    "row_idx": row_idx,
                    "col": col,
                    "text": text,
                    "reason": "no_adjacent_anchor",
                })
                continue

            # 数据感知排序：
            # 1) 优先有数据的锚定列（fill >= threshold）
            # 2) 同侧优先（右侧优先，因为表头标签描述右侧数据列）
            # 3) 数据量大的优先
            def _candidate_score(c):
                anchor, is_right, fill = c
                has_data = 1 if fill >= MIN_DATA_FILL else 0
                right_bonus = 1 if is_right else 0
                return (has_data, right_bonus, fill)

            candidates.sort(key=_candidate_score, reverse=True)
            target_anchor = candidates[0][0]

            target_new_idx = anchor_positions.index(target_anchor)

            # 就近放置：如果目标列已有文本，向右/向左寻找最近的空列
            if new_row[target_new_idx]:
                placed = False
                # 优先向右找空列（保持从左到右的自然阅读顺序）
                for offset in range(1, data_cols):
                    right_idx = target_new_idx + offset
                    if right_idx < data_cols and not new_row[right_idx]:
                        new_row[right_idx] = text
                        placed = True
                        break
                # 向右无空列 → 向左找
                if not placed:
                    for offset in range(1, data_cols):
                        left_idx = target_new_idx - offset
                        if left_idx >= 0 and not new_row[left_idx]:
                            new_row[left_idx] = text
                            placed = True
                            break
                # 无处可放 → 记录为孤立表头文本
                if not placed:
                    orphan_texts.append({
                        "row_idx": row_idx,
                        "col": col,
                        "text": text,
                        "reason": "no_empty_slot_near_target",
                        "target_col": target_new_idx,
                    })
            else:
                new_row[target_new_idx] = text

        mapped_headers.append(new_row)

    # ---- 异常检测：底层表头文字缺失 ----
    # 底层表头（row_idx=0）中某些列为空，但数据行中对应该列的位置有值
    if len(mapped_headers) > 0 and data_rows:
        bottom = mapped_headers[0]  # 底层表头
        missing_cols = []
        for col_idx in range(data_cols):
            if _is_effectively_empty(bottom[col_idx]):
                # 检查数据行中该列是否有值
                data_filled = sum(
                    1 for r in data_rows
                    if col_idx < len(r) and not _is_effectively_empty(r[col_idx])
                )
                if data_filled >= len(data_rows) * 0.3:
                    missing_cols.append({
                        "col_idx": col_idx,
                        "data_filled": data_filled,
                        "data_total": len(data_rows),
                    })

        if missing_cols:
            missing_desc = "、".join(
                f"col {m['col_idx']}（{m['data_filled']}/{m['data_total']}行有数据）"
                for m in missing_cols
            )
            _anomalies.append(RepairAnomaly(
                type=HEADER_TEXT_MISSING,
                severity=ANOMALY_HIGH,
                description=(
                    f"底层表头中 {len(missing_cols)} 列缺失标签文字（{missing_desc}），"
                    f"规则引擎无法自动推断正确的标签名"
                ),
                location="bottom_header",
                details={
                    "missing_columns": [
                        {"col_idx": m["col_idx"], "data_fill_rate": round(m["data_filled"] / m["data_total"], 3)}
                        for m in missing_cols
                    ],
                    "current_bottom_header": bottom,
                },
                confidence=0.3,
            ))

    # ---- 异常检测：孤立表头文本 ----
    if orphan_texts:
        for ot in orphan_texts:
            _anomalies.append(RepairAnomaly(
                type=ORPHAN_HEADER_TEXT,
                severity=ANOMALY_MEDIUM,
                description=(
                    f"表头行 {ot['row_idx']} 的 col {ot['col']} 有文本 "
                    f"'{ot['text'][:30]}'，但无法归并到任何锚定列（{ot['reason']}）"
                ),
                location=f"header_row_{ot['row_idx']}",
                details={
                    "row_idx": ot["row_idx"],
                    "column": ot["col"],
                    "text": ot["text"],
                    "reason": ot["reason"],
                },
                confidence=0.5,
            ))

    # ---- 阶段2：修复 —— 合并截断 + 层级嵌套 ----
    merged, surviving = merge_truncated_headers(
        mapped_headers, header_indices, anomalies=_anomalies
    )

    return merged, surviving


# ============================================================
# 第三步：自底向上识别表头行
# ============================================================

def find_header_rows_bottom_up(
    table_data: List[List[str]],
    data_start_row: int,
    data_cols: int
) -> List[int]:
    """
    从数据区域上方开始，自底向上逐行识别表头行

    返回表头行的行索引列表（从下到上排列，即最靠近数据的在前）。

    停止条件：
    - 遇到描述文本行
    - 遇到全同短词行（排版残留，如全行"亿元。"）
    - 遇到空行
    - 遇到独立标题行（如"表1：..."）
    - 追溯到表格开头
    """
    if data_start_row <= 0:
        return []

    header_indices = []

    for i in range(data_start_row - 1, -1, -1):
        row = table_data[i]

        # 空行 → 停止
        if _count_nonempty_cells(row) == 0:
            break

        # 描述文本行 → 停止
        if _is_description_row(row):
            break

        # 全同短词行（如整行都是"亿元。"）→ 排版残留，停止
        if _is_uniform_short_row(row):
            break

        # 残留行检测：col0 为空且非空列极少（≤1）
        # 需要区分真正的 PDF 残留和表头标签（如"2024年"这种年份分组标签）
        col0_empty = _is_effectively_empty(row[0]) if row else True
        if col0_empty and _count_nonempty_cells(row) <= 1:
            # 检查唯一的非空单元格是否像表头标签（短文本、非描述、无句号）
            is_label = False
            for j, c in enumerate(row):
                if not _is_effectively_empty(c):
                    text = str(c).strip()
                    if (len(text) <= 12
                            and not text.endswith('。')
                            and not any(kw in text for kw in _DESCRIPTION_HINTS)):
                        is_label = True
                    break
            if is_label:
                # 作为表头行纳入，继续向上查找
                if _is_header_row(row, data_cols):
                    header_indices.append(i)
                continue
            # 已有表头 → 停止；无表头 → 继续向上找
            if header_indices:
                break
            continue

        # 是表头行 → 加入
        if _is_header_row(row, data_cols):
            header_indices.append(i)
        else:
            # 不是表头 → 如果已有表头则停止
            # 若尚未找到任何表头，继续向上（可能遇到真正的表头行）
            if header_indices:
                break

    # header_indices 是从下到上排列的：[data_start-1, data_start-2, ...]
    return header_indices


# ============================================================
# 第四步：合并被截断的表头
# ============================================================

def merge_truncated_headers(
    header_rows: List[List[str]],
    header_indices: List[int],
    anomalies: Optional[List[RepairAnomaly]] = None,
) -> Tuple[List[List[str]], List[int]]:
    """
    合并被 PDF 解析截断的表头

    检测场景：
    - 两行表头中，上行某些列有文本，下行对应列为空 → 可能是截断
    - 上行文本 + 下行文本连起来是完整词语

    Args:
        anomalies: 异常收集列表（可选）

    返回 (merged_header_rows, surviving_indices)
    """
    _anomalies: List[RepairAnomaly] = anomalies if anomalies is not None else []

    if len(header_rows) < 2:
        return header_rows, header_indices

    # header_rows 按从下到上排列（索引递减）
    # header_indices 也是从下到上
    # 我们需要从下往上检测相邻行的合并关系

    result_rows = []
    result_indices = []
    skip_next = False

    for idx in range(len(header_rows)):
        if skip_next:
            skip_next = False
            continue

        row = header_rows[idx]

        if idx < len(header_rows) - 1:
            next_row = header_rows[idx + 1]  # 上一行（在表格中更靠上的行）

            # 检查是否可以合并：上行和下行在同一列都有文本的情况
            # 更关键的是：如果某行只有部分列有值，可能是截断
            should_merge = _check_header_merge(row, next_row)

            if should_merge:
                merged_row = _merge_two_header_rows(row, next_row)
                result_rows.append(merged_row)
                result_indices.append(header_indices[idx])
                skip_next = True  # 跳过下一行（已合并）

                # ---- 异常检测：截断表头合并 ----
                row_below_nf = sum(1 for c in row if not _is_effectively_empty(c))
                row_above_nf = sum(1 for c in next_row if not _is_effectively_empty(c))
                _anomalies.append(RepairAnomaly(
                    type=TRUNCATED_HEADER_MERGED,
                    severity=ANOMALY_LOW,
                    description=(
                        f"表头行 {header_indices[idx]} 和 {header_indices[idx+1]} 被合并 "
                        f"（下方行 {row_below_nf} 个非空单元格 + 上方行 {row_above_nf} 个非空单元格），"
                        f"如果两行实际是独立层级，此处可能错误合并"
                    ),
                    location=f"header_rows_{header_indices[idx]}_{header_indices[idx+1]}",
                    details={
                        "merged_row_below_idx": header_indices[idx],
                        "merged_row_above_idx": header_indices[idx + 1],
                        "row_below_nonempty": row_below_nf,
                        "row_above_nonempty": row_above_nf,
                        "result_row": merged_row,
                    },
                    confidence=0.55,
                ))
                continue

        result_rows.append(row)
        result_indices.append(header_indices[idx])

    return result_rows, result_indices


def _check_header_merge(row_below: List[str], row_above: List[str]) -> bool:
    """
    检查两行表头是否应该合并

    核心判断：只有当下方行（更靠近数据）看起来"不完整"时，才考虑合并。
    如果下方行已经大部分列有值，说明它是一层完整的表头，不应合并。

    合并条件（同时满足）：
    1. 下方行非空单元格较少（< 40%），看起来像被截断
    2. 两行互补关系明显（填充关系多，重叠少）
    3. 不是典型的嵌套表头（上方是分组标签，下方是子标签）
    """
    max_len = max(len(row_below), len(row_above))
    a = _normalize_row_width(row_above, max_len)
    b = _normalize_row_width(row_below, max_len)

    # 统计各行的填充情况
    a_nonempty = sum(1 for c in a if not _is_effectively_empty(c))
    b_nonempty = sum(1 for c in b if not _is_effectively_empty(c))
    total = max_len

    # 关键规则：如果下方行已大部分填充（>= 40%），则不合并
    # 这说明下方行是一个完整的表头层级
    if total > 0 and b_nonempty >= max(3, total * 0.4):
        return False

    # 层级表头检测：下方行极稀疏（≤1 非空），上方行密集（≥3 非空），且无同列重叠
    # → 判定为父子层级关系（如"2024年"在上层，"2023年12月31日/产品发行募集/..."在下层）
    # → 不合并，保留层级
    if b_nonempty <= 1 and a_nonempty >= 3:
        overlap = 0
        for col in range(max_len):
            if (not _is_effectively_empty(a[col])
                    and not _is_effectively_empty(b[col])):
                overlap += 1
        if overlap == 0:
            return False

    # 如果下方行非空很少（<= 2 列），高度怀疑是截断
    if b_nonempty <= 1 and a_nonempty <= 1:
        fill_count = 0
        overlap_count = 0
        for col in range(max_len):
            a_filled = not _is_effectively_empty(a[col])
            b_filled = not _is_effectively_empty(b[col])
            if a_filled != b_filled:
                fill_count += 1
            elif a_filled and b_filled:
                overlap_count += 1
        if fill_count >= 1 and overlap_count <= 1:
            return True

    # 一般情况：检查互补填充关系
    fill_count = 0
    overlap_count = 0
    for col in range(max_len):
        a_filled = not _is_effectively_empty(a[col])
        b_filled = not _is_effectively_empty(b[col])

        if a_filled != b_filled:
            fill_count += 1
        elif a_filled and b_filled:
            overlap_count += 1

    # 需要同时满足：互补关系强、重叠少、下方行本身不完整
    if fill_count >= 2 and overlap_count <= 1 and b_nonempty <= 2:
        return True

    return False


def _merge_two_header_rows(row_below: List[str], row_above: List[str]) -> List[str]:
    """合并两行表头：取每个位置的非空值，如果都有值则保留下方行内容"""
    max_len = max(len(row_below), len(row_above))
    a = _normalize_row_width(row_above, max_len)
    b = _normalize_row_width(row_below, max_len)

    result = []
    for col in range(max_len):
        a_val = a[col].strip() if not _is_effectively_empty(a[col]) else ""
        b_val = b[col].strip() if not _is_effectively_empty(b[col]) else ""

        if a_val and b_val:
            # 两行都有值 → 保留下方行（更靠近数据，通常是更准确的子标签）
            result.append(b_val)
        elif a_val:
            result.append(a_val)
        elif b_val:
            result.append(b_val)
        else:
            result.append("")

    return result


# ============================================================
# 第五步：恢复表头层级关系
# ============================================================

def restore_header_hierarchy(
    header_rows: List[List[str]],
    header_indices: List[int],
    data_cols: int,
    label_offset: int = 1,
) -> List[List[str]]:
    """
    恢复表头的层级关系

    核心逻辑（改进版——不依赖标签列位置）：
    1. 底层表头（最靠近数据）：检测重复模式 → 得到 group_size
       - 例如 [金额, 占比(%), 金额, 占比(%), ...] → group_size = 2
       - 这个 group_size 定义了一个"语义组"（如一个指标对）占几列
       - label_offset 前的列（标签列）不参与模式检测
    2. 上层表头：按标签出现顺序均匀分配列
       - 不再依赖标签在哪个列位置（PDF 提取可能偏移）
       - 只依赖标签的顺序（顺序是可靠正确的）
       - col 0..label_offset-1 保持原值（行标签列/单位说明列，不参与填充）
    3. 底层表头不执行填充（保留原始子标签）

    Args:
        header_rows: 表头行列表（从下到上排列）
        header_indices: 表头行在原始表格中的索引
        data_cols: 预期数据列数
        label_offset: 开头标签列的列数，这些列不参与标签填充

    返回修复后的表头行列表（从上到下排列）
    """
    if not header_rows or data_cols <= 0:
        return header_rows

    # header_rows 按从下到上排列（第0个 = 最靠近数据 = 底层表头）

    # ---- 检测底层表头重复模式 ----
    bottom_group_size = 1
    if len(header_rows) > 0:
        bottom_row = header_rows[0]
        bottom_group_size = _detect_bottom_header_group_size(bottom_row, label_offset)
        logger.debug(
            f"  restore_header_hierarchy: bottom_group_size={bottom_group_size}, "
            f"data_cols={data_cols}, header_layers={len(header_rows)}, "
            f"label_offset={label_offset}"
        )

    fixed_headers = []

    for idx, row in enumerate(header_rows):
        # 标准化列数
        clean_row = [_clean_cell(c) for c in row]
        if len(clean_row) > data_cols:
            clean_row = clean_row[:data_cols]
        elif len(clean_row) < data_cols:
            clean_row = clean_row + [""] * (data_cols - len(clean_row))

        if idx == 0:
            # 底层表头：保持原样，不填充（每个子标签独立）
            fixed_headers.append(clean_row)
        else:
            # 上层表头：按标签顺序均匀分配列（不依赖标签位置）
            filled = _fill_labels_evenly(clean_row, data_cols, bottom_group_size, label_offset)
            fixed_headers.append(filled)

    # fixed_headers 是从下到上的，反转为从上到下
    fixed_headers.reverse()
    return fixed_headers


def _detect_bottom_header_group_size(bottom_row: List[str], label_offset: int = 1) -> int:
    """
    检测底层表头的重复模式大小（最小重复周期）。

    底层表头定义了表格的真正列结构，通常存在重复的"语义对"：
    - [金额, 占比(%)] 重复 → group_size = 2
    - [期数, 金额] 重复 → group_size = 2
    - [Q1, Q2, Q3, Q4] 重复 → group_size = 4

    检测策略：
    1. 跳过前 label_offset 列（行标签列/单位说明列，不参与重复模式）
    2. 收集剩余非空文本序列
    3. 找最小周期：对于周期 k，检查所有 i 是否 texts[i] == texts[i+k]
    4. 如果精确匹配失败，用首个文本的间距作为近似周期

    如果无重复模式，返回 1（每列独立）。

    Args:
        bottom_row: 底层表头行
        label_offset: 开头标签列的列数，这些列不参与模式检测
    """
    if not bottom_row:
        return 1

    # 收集 col label_offset..N 的非空文本（跳过标签列）
    data_texts: List[str] = []
    for i in range(label_offset, len(bottom_row)):
        cell = bottom_row[i] if i < len(bottom_row) else ""
        if not _is_effectively_empty(cell):
            data_texts.append(cell.strip())

    n = len(data_texts)
    if n < 2:
        return 1

    # ---- 精确匹配：找最小重复周期 ----
    for period in range(1, n // 2 + 1):
        all_match = True
        for i in range(n - period):
            if data_texts[i] != data_texts[i + period]:
                all_match = False
                break
        if all_match:
            logger.debug(
                f"  _detect_bottom_header_group_size: exact period={period} "
                f"from texts={data_texts[:8]}..."
            )
            return period

    # ---- 近似匹配：找第一个文本的下次出现位置 ----
    first = data_texts[0]
    for i in range(1, n):
        if data_texts[i] == first:
            logger.debug(
                f"  _detect_bottom_header_group_size: approximate period={i} "
                f"(first='{first}' repeats at index {i})"
            )
            return i

    logger.debug(
        f"  _detect_bottom_header_group_size: no repeat pattern, "
        f"texts={data_texts[:6]}..."
    )
    return 1


def _fill_labels_evenly(
    row: List[str],
    n_cols: int,
    bottom_group_size: int = 1,
    label_offset: int = 1,
) -> List[str]:
    """
    将表头行的标签在数据列间均匀分配——不依赖标签的列位置，只依赖标签的顺序。

    与 _fill_header_labels_right 的关键区别：
    - 旧函数：看标签在哪个列位置，从该位置向右填充直到遇到下一个标签
      → 问题：PDF 提取的标签列位置可能偏移/错位，导致跨度错误
    - 新函数：只看标签的出现顺序，将数据列均匀分配给各标签
      → 保证：顺序对 → 结果对，不受位置偏移影响

    分配规则：
    1. col 0..label_offset-1 保持原值（行标签列/单位说明/伴随标签列，不参与填充）
    2. 收集 col label_offset..N 中所有非空标签，保持出现顺序
    3. 将剩余数据列均匀分配给这些标签
    4. 每个标签向右扩展到其分配列范围的末尾
    5. 如有余数（不能整除），末尾标签多分一列

    例如（6 个标签，12 个数据列，label_offset=1）：
      ['', '建信理财', '', '本行', '', '本集团', '', '建信理财', '', '本行', '', '本集团', '']
    → ['', '建信理财', '建信理财', '本行', '本行', '本集团', '本集团',
        '建信理财', '建信理财', '本行', '本行', '本集团', '本集团']

    Args:
        row: 待填充的表头行
        n_cols: 目标总列数
        bottom_group_size: 底层表头检测出的组大小（用于日志/验证，不强制绑定）
        label_offset: 开头标签列的列数，这些列不参与标签填充
    """
    if not row or n_cols <= label_offset:
        # 没有数据列可填充
        if not row:
            return [""] * n_cols
        result = list(row[:n_cols])
        while len(result) < n_cols:
            result.append("")
        return result[:n_cols]

    # ---- 收集标签顺序（跳过前 label_offset 列） ----
    labels: List[str] = []
    for i in range(label_offset, min(n_cols, len(row))):
        cell = row[i] if i < len(row) else ""
        if not _is_effectively_empty(cell):
            labels.append(cell.strip())

    if not labels:
        # 无可填充标签 → 保持原样
        result = list(row[:n_cols])
        while len(result) < n_cols:
            result.append("")
        return result[:n_cols]

    data_cols = n_cols - label_offset
    num_labels = len(labels)

    if num_labels >= data_cols:
        # 标签数 ≥ 数据列数 → 每列一个标签（多余标签截断）
        # 保留前 label_offset 列原值
        result = []
        for i in range(min(label_offset, n_cols)):
            if i < len(row) and not _is_effectively_empty(row[i]):
                result.append(row[i].strip())
            else:
                result.append("")
        for i in range(label_offset, n_cols):
            idx = i - label_offset
            if idx < len(labels):
                result.append(labels[idx])
            else:
                result.append(labels[-1])
        return result

    # ---- 正常情况：标签数 < 数据列数 → 均匀分配 ----
    span_per_label = data_cols // num_labels
    remainder = data_cols % num_labels  # 前 remainder 个标签多分 1 列

    # 保留前 label_offset 列原值
    result = []
    for i in range(min(label_offset, n_cols)):
        if i < len(row) and not _is_effectively_empty(row[i]):
            result.append(row[i].strip())
        else:
            result.append("")

    for label_idx, label in enumerate(labels):
        # 此标签跨的列数：基础跨度 + 前 remainder 个标签的余数
        label_span = span_per_label + (1 if label_idx < remainder else 0)
        for _ in range(label_span):
            if len(result) < n_cols:
                result.append(label)

    # 兜底：补满到 n_cols
    while len(result) < n_cols:
        result.append("")

    logger.debug(
        f"  _fill_labels_evenly: {num_labels} labels → "
        f"{data_cols} data cols (span={span_per_label}+r{remainder}), "
        f"bottom_group_size={bottom_group_size}, label_offset={label_offset}, "
        f"labels={labels[:6]}..."
    )

    return result[:n_cols]


def _fill_header_labels_right(row: List[str], n_cols: int) -> List[str]:
    """
    对上表头执行向右填充：每个非空标签向右填充空单元格，直到遇到下一个非空标签。

    例如：
      ['理财产品名称', '收益率', '', '', '风险等级', '']
    → ['理财产品名称', '收益率', '收益率', '收益率', '风险等级', '风险等级']

    首列如果为空通常保持不变（对应行标签列，合并单元格转角）

    注意：此函数依赖标签的列位置，当 PDF 提取导致标签位置偏移时
    可能产生错误结果。restore_header_hierarchy 中优先使用 _fill_labels_evenly。
    """
    if not row:
        return [""] * n_cols

    result = list(row)
    last_label = ""

    for col in range(n_cols):
        cell = result[col] if col < len(result) else ""

        if cell:
            # 遇到新标签，更新当前标签
            last_label = cell
        elif last_label:
            # 空单元格 → 用左侧标签填充
            if col < len(result):
                result[col] = last_label
            else:
                result.append(last_label)

    # 确保长度正确
    while len(result) < n_cols:
        result.append("")
    return result[:n_cols]


def _clean_cell(cell: str) -> str:
    """清理单元格文本"""
    if _is_effectively_empty(cell):
        return ""
    return cell.strip()


# ============================================================
# 第六步：移除描述文本
# ============================================================

def remove_description_text(
    table_data: List[List[str]],
    header_start_row: int,
    header_indices: List[int]
) -> List[List[str]]:
    """
    移除表格上方的描述文本

    header_indices 中包含所有表头行的原始索引，
    header_start_row 是表头区域最上面一行的索引。
    在此之前的内容即为描述文本。
    """
    if header_start_row is None:
        return table_data

    if header_start_row <= 0:
        return table_data

    # 保留从 header_start_row 开始的内容
    return table_data[header_start_row:]


# ============================================================
# 表头缺失检测与合成
# ============================================================

def _synthesize_header_if_needed(
    table: List[List[str]],
    max_cols: int,
    context_text: Optional[str] = None,
    raw_header_info: Optional[Tuple[List[str], List[str]]] = None,
) -> bool:
    """
    检测表格是否缺乏表头，如果是则合成一个默认表头行插入到表格顶部。

    适用场景：
    - 从大表分裂出的子表丢失了列标题行（如 table_011/012 从 table_010 延续）
    - 数据行直接从第0行开始，col0 为文本标签、其余列为数值的表格
    - 2-3 行的小表（如拆分出的尾部子表），放宽检测阈值

    检测路径（按优先级）：
    A. 严格路径（≥2标签 + ≥2数值行 + ≥50%数值占比）—— 原有逻辑
    B. 小表宽松路径（2-3行表，≥1标签 + ≥1数值行 + 首行≥2数值列）

    表头合成策略（按优先级）：
    1. 从 context_text（表格上方的页面文本）解析列标题
    2. 从表格 col0 的文本标签推断列标题（如"项目"、"金额"）
    3. 默认通用标题（"项目"、"列1"、"列2"…）

    Returns:
        True 如果合成了表头并插入到 table[0]，False 如果不需要
    """
    if len(table) < 2 or max_cols < 2:
        return False

    # 条件 0: 前 3 行已有纯文本表头行 → 跳过（正常表不需要合成）
    for i in range(min(3, len(table))):
        row = table[i]
        nonempty_cells = [c for c in row if not _is_effectively_empty(c)]
        if nonempty_cells and all(not _is_numeric_cell(c) for c in nonempty_cells):
            return False

    text_in_col0 = 0
    numeric_rows = 0
    total_other_cells = 0
    numeric_other_cells = 0

    rows_to_check = min(8, len(table))

    for i in range(rows_to_check):
        row = table[i]

        # 跳过空行
        nonempty = _count_nonempty_cells(row)
        if nonempty == 0:
            continue

        # col0 检查：非空且非数值 → 文本标签
        if len(row) > 0 and not _is_effectively_empty(row[0]):
            if not _is_numeric_cell(row[0]):
                text_in_col0 += 1

        # 其余列检查：数值占比
        row_has_numeric = False
        for j in range(1, max_cols):
            if j < len(row) and not _is_effectively_empty(row[j]):
                total_other_cells += 1
                if _is_numeric_cell(row[j]):
                    numeric_other_cells += 1
                    row_has_numeric = True

        if row_has_numeric:
            numeric_rows += 1

    # ══════════════════════════════════════════════════════════
    # 严格路径：3条件（≥2标签 + ≥2数值行 + ≥50%数值占比）
    # ══════════════════════════════════════════════════════════
    passed_strict = (
        text_in_col0 >= 2 and
        numeric_rows >= 2 and
        total_other_cells > 0 and
        numeric_other_cells / total_other_cells >= 0.5
    )

    # ══════════════════════════════════════════════════════════
    # 小表宽松路径（2-3行）：第一行必须是明确的数据行
    # ══════════════════════════════════════════════════════════
    passed_relaxed = False
    if not passed_strict and len(table) <= 3:
        first_row = table[0]
        first_numeric = 0
        for j in range(1, max_cols):
            if j < len(first_row) and _is_numeric_cell(first_row[j]):
                first_numeric += 1
        passed_relaxed = (
            text_in_col0 >= 1 and
            numeric_rows >= 1 and
            first_numeric >= 2 and
            (total_other_cells == 0 or
             numeric_other_cells / max(total_other_cells, 1) >= 0.5)
        )

    if not passed_strict and not passed_relaxed:
        return False

    # ══════════════════════════════════════════════════════════
    # 合成表头：优先使用 context_text，其次智能推断，最后默认
    # ══════════════════════════════════════════════════════════
    synthetic = _build_synthetic_headers(
        table, max_cols, context_text, raw_header_info
    )

    table.insert(0, synthetic)
    if passed_relaxed:
        logger.info(
            f"  [header_synth] 小表宽松检测 -> 合成表头: {synthetic}"
        )
    else:
        logger.info(
            f"  [header_synth] 表头缺失 -> 自动合成表头: {synthetic}"
        )
    return True


def _build_synthetic_headers(
    table: List[List[str]],
    max_cols: int,
    context_text: Optional[str] = None,
    raw_header_info: Optional[Tuple[List[str], List[str]]] = None,
) -> List[str]:
    """根据可用信息构建合成表头。

    优先级:
    1. raw_header_info → 从原始页面 items 提取的实体×年份表头（最高质量）
    2. context_text 中提取的列标题 tokens
    3. 根据表格 col0 内容推断（文本标签 → "项目"，纯数值 → "金额"）
    4. 通用默认: col0="项目", 其余="列1","列2"…
    """
    synthetic = [""] * max_cols

    # ── 优先级 1: 从原始页面 items 提取的实体×年份表头 ──
    if raw_header_info:
        entities, years = raw_header_info
        if entities and years:
            synthetic[0] = "项目"
            idx = 1
            for entity in entities:
                for year in years:
                    if idx < max_cols:
                        synthetic[idx] = f"{entity}·{year}"
                        idx += 1
            # 填充剩余列
            for j in range(idx, max_cols):
                synthetic[j] = f"列{j}"
            return synthetic

    # ── 尝试从 context_text 解析列标题 ──
    if context_text:
        headers_from_context = _try_parse_headers_from_context(
            context_text, max_cols
        )
        if headers_from_context:
            for j in range(min(max_cols, len(headers_from_context))):
                synthetic[j] = headers_from_context[j]
            return synthetic

    # ── 根据 col0 内容推断第一列名称 ──
    col0_texts = []
    for i in range(min(5, len(table))):
        row = table[i]
        if len(row) > 0 and not _is_effectively_empty(row[0]):
            val = row[0].strip()
            if not _is_numeric_cell(val):
                col0_texts.append(val)

    if col0_texts:
        # 如果 col0 有文本标签 → 第一列是"项目"类
        synthetic[0] = "项目"
    else:
        synthetic[0] = "指标"

    # ── 其余列：根据内容类型命名 ──
    has_numeric_cols = False
    for j in range(1, max_cols):
        col_values = []
        for i in range(min(8, len(table))):
            row = table[i]
            if j < len(row) and not _is_effectively_empty(row[j]):
                col_values.append(row[j])
        if col_values and all(_is_numeric_cell(v) for v in col_values):
            has_numeric_cols = True
            break

    if has_numeric_cols and max_cols == 2:
        synthetic[1] = "金额"
    elif has_numeric_cols and max_cols >= 3:
        for j in range(1, max_cols):
            synthetic[j] = f"列{j}"
    else:
        for j in range(1, max_cols):
            synthetic[j] = f"列{j}"

    return synthetic


def _try_parse_headers_from_context(
    context_text: str,
    max_cols: int,
) -> Optional[List[str]]:
    """尝试从表格上方的页面上下文文本中提取列标题。

    解析策略:
    - 如果 context_text 包含以空格/制表符分隔的多个 token
    - 且 token 数量 ≥ 3（至少包含一些列信息）
    - 且 token 中有文本标签（非纯数值）
    - 取前 max_cols 个 token 作为列标题

    Returns:
        列标题列表，如果解析失败返回 None
    """
    if not context_text or len(context_text) < 3:
        return None

    # 尝试按常见分隔符拆分
    import re
    # 优先按制表符/多个空格/中文顿号/逗号分隔
    tokens = re.split(r'[\t]{1,}|［\s　]{2,}|[、，,；;]', context_text)
    tokens = [t.strip() for t in tokens if t.strip()]
    # 过滤掉纯数值 token（不太可能是列标题）
    tokens = [t for t in tokens if not re.match(r'^[\d.,+\-（）()%％百千万亿]+$', t)]

    if len(tokens) >= 3:
        # 有足够的文本 token，尝试用作列标题
        headers = [""] * max_cols
        for j in range(min(max_cols, len(tokens))):
            headers[j] = tokens[j][:20]  # 限制长度
        return headers

    return None


# ── 从原始 liteparse items 中提取真实列标题 ──

# 年份模式（与 liteparse_table_segmenter 保持一致）
_RAW_YEAR_PATTERN = re.compile(r'(19|20)\d{2}年?')
_RAW_ENTITY_KW = ['本集团', '本行', '本公司', '母公司', '子公司',
                  '本基金', '本计划', '本集合', '本产品']


def _extract_headers_from_raw_items(
    text_items: List[dict],
    column_x_ranges: List[Tuple[float, float]],
) -> Optional[Tuple[List[str], List[str]]]:
    """从原始 liteparse text_items 中提取真实列标题（实体标签 × 年份）。

    扫描页面 text_items 中落在表格 X 范围内的所有文本块，
    寻找"实体标签行"（如"本集团 本行"）和"年份标签行"（如"2024年 2023年"），
    构建 entity × year 的完整列标题集合。

    使用场景：
    - 分割/分段过程中表头行被丢弃（如小表从大表尾部剥离）
    - _synthesize_header_if_needed 合成表头时作为高质量标题来源

    Args:
        text_items: liteparse 原始文本块列表 [{x0, y0, x1, y1, text}, ...]
        column_x_ranges: 列 X 范围 [(x0_min, x1_max), ...]，用于限定搜索范围

    Returns:
        (entities, years) 元组，如果未找到则返回 None
        - entities: 实体标签列表，如 ["本集团", "本行"]
        - years: 年份标签列表，如 ["2024年", "2023年"]
    """
    if not text_items or not column_x_ranges or len(column_x_ranges) < 2:
        return None

    # ── 计算表格 X 范围（含少量容差） ──
    x_min = min(r[0] for r in column_x_ranges) - 10.0
    x_max = max(r[1] for r in column_x_ranges) + 10.0

    # ── 筛选表格 X 范围内的 items ──
    in_range = []
    for it in text_items:
        ix0 = it.get("x0", 0)
        ix1 = it.get("x1", 0)
        # item 与表格 X 范围有交集
        if ix1 > x_min and ix0 < x_max:
            in_range.append(it)

    if not in_range:
        return None

    # ── 按 Y 行聚类（同行的 items 有相近的 y0） ──
    Y_TOLERANCE = 6.0  # PDF 中同行的 item y0 偏差容忍度
    in_range.sort(key=lambda it: (it.get("y0", 0), it.get("x0", 0)))

    rows: List[List[dict]] = []
    current_row: List[dict] = []
    current_y: Optional[float] = None

    for it in in_range:
        y0 = it.get("y0", 0)
        if current_y is None or abs(y0 - current_y) > Y_TOLERANCE:
            if current_row:
                rows.append(current_row)
            current_row = [it]
            current_y = y0
        else:
            current_row.append(it)
    if current_row:
        rows.append(current_row)

    # ── 逐行扫描，寻找实体行和年份行 ──
    entities: List[str] = []
    years: List[str] = []

    for row in rows:
        texts = [it.get("text", "").strip() for it in row]
        if not texts:
            continue

        # 检测实体关键词
        row_entities = []
        for kw in _RAW_ENTITY_KW:
            for t in texts:
                if kw in t and kw not in row_entities:
                    row_entities.append(kw)
        if len(row_entities) >= 2:
            # 多个实体关键词出现 → 实体标签行
            entities = row_entities

        # 检测年份（排除长数字串、含特殊符号的 token，但允许纯4位年份）
        row_years = []
        for t in texts:
            # 跳过太长的 token（不可能是年份标签）
            if len(t) > 20:
                continue
            # 尝试匹配年份模式（包括纯数字 2024 和带年后缀的 2024年）
            for m in _RAW_YEAR_PATTERN.finditer(t):
                yv = m.group()
                # 路径1: 年份后直接带"年" → 如"2024年"
                if yv.endswith('年') or (m.end() < len(t) and t[m.end()] == '年'):
                    yv_with_suffix = yv if yv.endswith('年') else yv + '年'
                    if yv_with_suffix not in row_years:
                        row_years.append(yv_with_suffix)
                # 路径2: 纯4位年份（如"2024"），前后不是数字 → 独立年份
                elif re.match(r'^(19|20)\d{2}$', yv):
                    prev_ch = t[m.start() - 1] if m.start() > 0 else ''
                    next_ch = t[m.end()] if m.end() < len(t) else ''
                    if not prev_ch.isdigit() and not next_ch.isdigit():
                        yv_with_suffix = yv + '年'
                        if yv_with_suffix not in row_years:
                            row_years.append(yv_with_suffix)

        if len(row_years) >= 2:
            # 去重并保持顺序
            seen = set()
            years = [y for y in row_years if not (y in seen or seen.add(y))]

    if not entities or not years:
        return None

    return (entities, years)


# ============================================================
# 检查表格是否需要修复
# ============================================================

def _needs_repair(table_data: List[List[str]]) -> Tuple[bool, str]:
    """
    快速检查表格是否需要规则修复

    思路：先定位数据区域 → 从数据区向上精准找表头行 → 只检查真正的表头
    （表头之上的噪音行如描述文本、章节标题不参与判断，避免误报）

    Returns:
        (needs_repair, reason)
    """
    if not table_data or len(table_data) < 3:
        return False, "表格行数不足"

    # Step A: 定位数据区域
    region = locate_data_region(table_data)
    if region is None:
        return False, "无法定位数据区域"
    data_start, data_end = region

    if data_start <= 1:
        return False, "数据区域太靠前，可能无表头"

    # Step B: 从数据区域向上扫描，找到真正的表头行
    # 复用 find_header_rows_bottom_up 的停止逻辑：
    # 遇到描述文本/空行/排版残留/章节标题 → 停止，不会把噪音当表头
    table_cols = max(len(row) for row in table_data) if table_data else 0
    header_indices = find_header_rows_bottom_up(table_data, data_start, table_cols)

    if not header_indices:
        return False, "未检测到表头行（数据向上扫描未找到表头）"

    # Step C: 只检查真正的表头行是否有空缺（层级填充需求）
    issues_found = 0
    for idx in header_indices:
        row = table_data[idx]
        nonempty = _count_nonempty_cells(row)
        total = len(row) if row else 0
        if total > 0 and nonempty > 0 and nonempty < total:
            issues_found += 1

    if issues_found > 0:
        return True, f"检测到 {issues_found} 个表头行可能存在层级问题"

    # Step D: 检查表头之上是否有噪音行需要清理
    # 即使表头结构完整，如果有描述文本/章节标题等噪音行在表头之上，
    # 也需要触发修复流程将其移除
    topmost_header_idx = min(header_indices)
    if topmost_header_idx > 0:
        return True, f"表头之上有 {topmost_header_idx} 行噪音（描述文本/标题），需要清理"

    # Step E: 检查表头列数与数据列数是否一致
    data_rows = table_data[data_start:data_end]
    header_cols = max(len(table_data[i]) for i in header_indices)
    data_cols = count_data_columns(data_rows)
    if header_cols != data_cols:
        return True, f"表头列数({header_cols})与数据列数({data_cols})不一致"

    return False, "表头结构看似完整"


# ============================================================
# 主函数：规则修复表格
# ============================================================

def repair_table_rules(
    table_data: List[List[str]],
    force: bool = False,
    context_text: Optional[str] = None,
    raw_header_info: Optional[Tuple[List[str], List[str]]] = None,
) -> Tuple[List[List[str]], dict]:
    """基于规则修复表格结构（表头引导的分层修复）。

    新流程：
    1. 定位数据区域
    2. 根据数据区确定表头区 + 分析底层表头确定预期列数 N
    3. 根据底层表头列数，修复数据区（删空白列 + 合并错位列）
    4. 修复表头区（映射列 + 合并截断 + 层级嵌套）
    5. 移除描述文本 + 组装

    Args:
        table_data: 原始 2D 表格数据
        force: 是否强制修复
        context_text: 表格上方的页面上下文文本（如 caption），用于表头合成
        raw_header_info: 从原始页面 items 提取的 (entities, years) 元组，
                         用于合成高质量列标题（如"本集团·2024年"）

    Returns:
        (repaired_table, repair_info)
    """
    info = {
        'needed': False,
        'reason': '',
        'steps': [],
        'original_rows': len(table_data),
        'repaired_rows': len(table_data),
        'data_cols': 0,
        'header_rows_found': 0,
        'headers_merged': False,
        'description_removed': False,
        'columns_pruned': 0,
        'columns_merged': 0,
        'anomalies': [],  # 新增：修复过程中检测到的异常
    }

    if not table_data:
        info['reason'] = '空表格'
        return table_data, info

    # 转换所有单元格为字符串
    table = [
        [str(c) if c is not None else "" for c in row]
        for row in table_data
    ]

    # 预处理：统一每行列数到表格最大列数
    max_cols = max(len(row) for row in table) if table else 0
    if max_cols == 0:
        info['reason'] = '表格无列'
        return table, info
    table = [_normalize_row_width(row, max_cols) for row in table]

    # ══════════════════════════════════════════════════════════
    # Step 0: 表头缺失检测与合成
    # ══════════════════════════════════════════════════════════
    header_synthesized = _synthesize_header_if_needed(
        table, max_cols, context_text, raw_header_info
    )
    if header_synthesized:
        info['steps'].append('0. 表头合成: 无表头→合成默认表头行')
        info['header_synthesized'] = True
        force = True  # 合成表头后强制进入修复流程，绕过 _needs_repair 的 data_start<=1 拦截

    # 检查是否需要修复
    needs, reason = _needs_repair(table)
    if not needs and not force:
        info['reason'] = reason
        return table, info

    info['needed'] = True
    info['reason'] = reason if needs else '强制修复'
    logger.info(f"规则修复开始: {reason}")

    # ---- 创建异常收集器 ----
    anomalies: List[RepairAnomaly] = []

    # ══════════════════════════════════════════════════════════
    # Step 1: 定位数据区域
    # ══════════════════════════════════════════════════════════
    region = locate_data_region(table)
    if region is None:
        info['reason'] = '无法定位数据区域'
        return table, info
    data_start, data_end = region
    data_rows = table[data_start:data_end]
    info['steps'].append(f'1. 数据区域: 行 {data_start}-{data_end - 1}')
    logger.info(f"  Step 1: data region rows [{data_start}:{data_end}]")

    # ---- 孤儿数据检测（Step 1.1：在修复之前检测多表合并） ----
    orphan_info = _detect_orphan_data_rows(table, data_end)
    if orphan_info:
        anomalies.append(RepairAnomaly(
            type=MULTI_TABLE_MERGED,
            severity=ANOMALY_HIGH,
            description=(
                f"疑似多表合并：数据区（行{data_start}-{data_end - 1}）结束后"
                f"检测到孤儿数据区（行{orphan_info['orphan_start']}-{orphan_info['orphan_end'] - 1}），"
                f"共{orphan_info['orphan_end'] - orphan_info['orphan_start']}行，"
                f"建议拆分为独立表格"
            ),
            location="data_region",
            details={
                **orphan_info,
                "context_description": "",  # Step 5 补齐
            },
            confidence=0.82,
        ))
        logger.warning(
            f"  ⚠ 检测到孤儿数据区: 行{orphan_info['orphan_start']}-{orphan_info['orphan_end'] - 1}"
            f"（分隔行: {orphan_info['separator_rows'][:1] if orphan_info['separator_rows'] else '无'}）"
        )

    # ══════════════════════════════════════════════════════════
    # Step 2: 根据数据区确定表头区 + 分析底层表头
    # ══════════════════════════════════════════════════════════
    # 使用当前全表列数先识别表头（后续会重新映射列）
    header_indices = find_header_rows_bottom_up(table, data_start, max_cols)
    info['header_rows_found'] = len(header_indices)
    logger.info(
        f"  Step 2: found {len(header_indices)} header rows "
        f"(indices {header_indices})"
    )

    if not header_indices:
        if header_synthesized and data_start > 0:
            # 合成了表头但向上扫描未识别 → 强制使用合成表头行 (row 0)
            logger.warning(
                "  Step 2: synthesized header not detected by scanner, "
                "force-using row 0"
            )
            header_indices = [0]
        else:
            # 无表头行 → 不做表头引导修复，返回原表
            logger.info("  Step 2: no header rows found → skip repair")
            info['reason'] = '未检测到表头行'
            return table, info

    # header_indices 从下到上：[closest_to_data, ..., topmost]
    header_rows_raw = [table[i] for i in header_indices]
    bottom_header = header_rows_raw[0]  # 最靠近数据的表头行

    # 分析底层表头 → 预期数据列数 N + 锚定列位置
    # 传入 data_rows 用于数据感知补充锚定列（如第一列表头为空但数据有值）
    # 传入 anomalies 用于自动收集异常
    expected_cols, anchor_positions, label_offset = analyze_bottom_header(
        bottom_header, data_rows, anomalies=anomalies
    )
    if expected_cols <= 0:
        info['reason'] = '底层表头全空，无法确定列数'
        return table, info

    info['data_cols'] = expected_cols
    info['label_offset'] = label_offset
    info['steps'].append(
        f'2. 表头识别: {len(header_indices)} 行'
        f'（底层锚定 {expected_cols} 列→{anchor_positions}，'
        f'标签列 {label_offset} 列）'
    )
    logger.info(
        f"  Step 2: bottom header → {expected_cols} anchor cols "
        f"at {anchor_positions}, label_offset={label_offset}"
    )

    # ══════════════════════════════════════════════════════════
    # Step 3: 修复数据区
    #   - 判断是否插入空白列 → 剪除 spacer
    #   - 判断数据是否被拆成多列 → 归并 offset
    # ══════════════════════════════════════════════════════════
    original_data_cols = max(len(r) for r in data_rows) if data_rows else 0
    cleaned_data = clean_data_region_by_header(
        data_rows, table, anchor_positions
    )

    cols_removed = max(0, original_data_cols - expected_cols)
    if cols_removed > 0:
        info['columns_pruned'] = cols_removed
        info['steps'].append(
            f'3. 数据区清理: {original_data_cols}→{expected_cols} 列'
        )
    logger.info(
        f"  Step 3: data cleaned, {len(cleaned_data)} rows × {expected_cols} cols"
    )

    # ══════════════════════════════════════════════════════════
    # Step 4: 修复表头区
    #   - 映射到锚定列结构（处理表头列偏移）
    #   - 合并截断表头
    #   - 恢复层级嵌套
    # ══════════════════════════════════════════════════════════
    cleaned_headers, surviving_indices = clean_header_region(
        header_rows_raw, header_indices, anchor_positions, expected_cols,
        data_rows, anomalies=anomalies
    )

    if len(cleaned_headers) < len(header_rows_raw):
        info['headers_merged'] = True
        info['steps'].append(
            f'4. 表头合并: {len(header_rows_raw)}→{len(cleaned_headers)} 行'
        )

    # 恢复层级关系
    topmost_header_idx = min(surviving_indices) if surviving_indices else data_start
    fixed_headers = restore_header_hierarchy(
        cleaned_headers, surviving_indices, expected_cols, label_offset
    )
    # fixed_headers 从上到下排列
    info['steps'].append(f'   层级恢复: {len(fixed_headers)} 行表头')
    logger.info(
        f"  Step 4: header repaired, {len(fixed_headers)} rows × {expected_cols} cols"
    )

    # ══════════════════════════════════════════════════════════
    # Step 5: 移除描述文本 + 组装最终表格
    # ══════════════════════════════════════════════════════════
    description_rows_count = topmost_header_idx
    if description_rows_count > 0:
        info['description_removed'] = True
        info['steps'].append(
            f'5. 移除描述文本: {description_rows_count} 行'
        )

    # ---- 补充 multi_table_merged 异常的上下文描述 ----
    for a in anomalies:
        if a.type == MULTI_TABLE_MERGED and topmost_header_idx > 0:
            desc_texts = []
            for i in range(0, topmost_header_idx):
                row = table[i]
                text = " ".join(c for c in row if not _is_effectively_empty(c))
                if text:
                    desc_texts.append(text)
            if desc_texts:
                a.details["context_description"] = desc_texts

    # 组装：表头（从上到下）+ 数据行
    result = []
    for header_row in fixed_headers:
        result.append(_normalize_row_width(header_row, expected_cols)[:expected_cols])
    result.extend(cleaned_data)

    # 移除全空行
    result = [row for row in result
              if any(not _is_effectively_empty(c) for c in row)]

    info['repaired_rows'] = len(result)
    info['steps'].append(
        f'6. 组装: {info["original_rows"]}→{info["repaired_rows"]} 行'
    )

    # ---- 汇总异常信息 ----
    info['anomalies'] = [_anomaly_to_dict(a) for a in anomalies]
    if anomalies:
        high_count = sum(1 for a in anomalies if a.severity == ANOMALY_HIGH)
        medium_count = sum(1 for a in anomalies if a.severity == ANOMALY_MEDIUM)
        low_count = sum(1 for a in anomalies if a.severity == ANOMALY_LOW)
        anomaly_summary = (
            f'7. ⚠ 异常标记: {len(anomalies)} 处 '
            f'(高{high_count}/中{medium_count}/低{low_count})，'
            f'建议后期 LLM 确认'
        )
        info['steps'].append(anomaly_summary)
        logger.warning(
            f"  规则修复完成，检测到 {len(anomalies)} 处异常: "
            f"高={high_count}, 中={medium_count}, 低={low_count}"
        )
        # 详细记录每个异常
        for a in anomalies:
            logger.warning(f"    [{a.severity}] {a.type}: {a.description}")

    logger.info(
        f"规则修复完成: {info['original_rows']}→{info['repaired_rows']} rows, "
        f"{expected_cols} cols, {len(fixed_headers)} header rows"
    )

    return result, info


def _anomaly_to_dict(anomaly: RepairAnomaly) -> Dict[str, Any]:
    """将 RepairAnomaly 转为普通字典（用于 JSON 序列化）"""
    return {
        "type": anomaly.type,
        "severity": anomaly.severity,
        "description": anomaly.description,
        "location": anomaly.location,
        "details": anomaly.details,
        "confidence": anomaly.confidence,
    }


def _trim_empty_rows(table: List[List[str]]) -> List[List[str]]:
    """去除表格首尾的纯空行"""
    if not table:
        return table
    start = 0
    while start < len(table) and all(_is_effectively_empty(c) for c in table[start]):
        start += 1
    end = len(table)
    while end > start and all(_is_effectively_empty(c) for c in table[end - 1]):
        end -= 1
    return table[start:end]


def repair_and_split_tables(
    table_data: List[List[str]],
    force: bool = False,
    context_text: Optional[str] = None,
    raw_header_info: Optional[Tuple[List[str], List[str]]] = None,
) -> List[Tuple[List[List[str]], dict]]:
    """检测并拆分多表合并的表格，对每个子表独立进行规则修复。

    基于"先数据区 → 再表头区 → 再边界"的策略：
    1. 找到所有数据区块（clusters）
    2. 扩展每个区块的边界（纳入标签行/汇总行）
    3. 相邻区块间的间隙中，检测子表边界（子标题 + 新列头 = 新表格）
    4. 在子表边界处切分，每个子表独立调用 repair_table_rules 修复

    Args:
        table_data: 原始 2D 表格数据
        force: 是否强制修复
        context_text: 表格上方的页面上下文文本（如 caption），用于表头合成
        raw_header_info: 从原始页面 items 提取的 (entities, years) 元组

    Returns:
        List of (repaired_table, repair_info)，至少包含 1 个元素。
        多表时按原文档顺序排列。
    """
    if not table_data:
        return [(table_data, {'needed': False, 'reason': '空表格'})]

    total_rows = len(table_data)
    total_cols = max(len(row) for row in table_data) if table_data else 0
    if total_cols == 0:
        return [(table_data, {'needed': False, 'reason': '表格无列'})]

    # ---- Step 1: 找到所有数据区块 ----
    clusters, row_scores, data_cols = _find_all_data_clusters(
        table_data, total_rows, total_cols
    )

    if len(clusters) <= 1:
        # 只有 0 或 1 个数据区块 → 走单表修复
        return [repair_table_rules(
            table_data, force=force, context_text=context_text,
            raw_header_info=raw_header_info,
        )]

    # ---- Step 2: 扩展每个区块的边界 ----
    expanded = []
    for start, end in clusters:
        ds, de = _expand_data_boundaries(
            table_data, start, end, data_cols, total_rows
        )
        # 去重：如果扩展后和上一个区块重叠，跳过
        if expanded and ds < expanded[-1][1]:
            # 合并或跳过（取更大的 de）
            prev_ds, prev_de = expanded[-1]
            expanded[-1] = (prev_ds, max(prev_de, de))
        else:
            expanded.append((ds, de))

    if len(expanded) <= 1:
        return [repair_table_rules(
            table_data, force=force, context_text=context_text,
            raw_header_info=raw_header_info,
        )]

    # ---- Step 3: 检测子表边界 ----
    # 对每对相邻的扩展区块，检查间隙是否是子表边界
    split_rows = []  # 分割点行号（此行的内容归属下一个表）

    for i in range(len(expanded) - 1):
        _, de_i = expanded[i]
        ds_next, _ = expanded[i + 1]

        gap_start = de_i
        gap_end = ds_next

        if gap_start >= gap_end:
            continue

        if _is_new_sub_table_boundary(
            table_data, gap_start, gap_end, data_cols, total_rows
        ):
            # 在间隙中精确定位子标题行
            sub_row = _find_sub_title_row(table_data, gap_start, gap_end)
            if sub_row < 0:
                # 回退：_is_new_sub_table_boundary 通过"完整表头"检测返回 True，
                # 但 _find_sub_title_row 需要 col0 文本，纯列头行（col0 为空）会失败。
                # 此时用间隙第一行作为分割点。
                sub_row = gap_start
            if sub_row >= 0:
                # 确认子标题后确实有新的数据区块（对应 expanded[i+1]）
                if sub_row < gap_end:
                    split_rows.append(sub_row)
                    logger.info(
                        f"  检测到子表边界: 行{sub_row} "
                        f"\"{(table_data[sub_row][0] or '').strip()[:30]}\""
                    )

    if not split_rows:
        logger.info(f"  未检测到子表边界，{len(clusters)} 个数据区块视为同一表格")
        return [repair_table_rules(
            table_data, force=force, context_text=context_text,
            raw_header_info=raw_header_info,
        )]

    # ---- Step 4: 按分割点切片并分别修复 ----
    split_rows.sort()
    logger.info(f"  将拆分为 {len(split_rows) + 1} 个独立子表")

    results = []
    prev_cut = 0

    for cut in split_rows:
        if cut <= prev_cut:
            continue
        sub_table = table_data[prev_cut:cut]
        sub_table = _trim_empty_rows(sub_table)
        if _has_minimal_data(sub_table):
            repaired, info = repair_table_rules(
                sub_table, force=force, context_text=context_text,
                raw_header_info=raw_header_info,
            )
            results.append((repaired, info))
        else:
            logger.info(f"  跳过空子表: 行{prev_cut}-{cut}")
        prev_cut = cut

    # 最后一段（剩余的所有行）
    if prev_cut < total_rows:
        sub_table = table_data[prev_cut:]
        sub_table = _trim_empty_rows(sub_table)
        if _has_minimal_data(sub_table):
            repaired, info = repair_table_rules(
                sub_table, force=force, context_text=context_text,
                raw_header_info=raw_header_info,
            )
            results.append((repaired, info))

    if not results:
        logger.warning("  拆分后所有子表均为空/无效，回退为单表修复")
        return [repair_table_rules(
            table_data, force=force, context_text=context_text,
            raw_header_info=raw_header_info,
        )]

    logger.info(f"  拆分完成: {len(results)} 个独立子表")

    # ---- Step 5: 相邻子表去重 ----
    if len(results) >= 2:
        results = deduplicate_adjacent_tables(results)

    return results


def deduplicate_adjacent_tables(
    tables: List[Tuple[List[List[str]], dict]]
) -> List[Tuple[List[List[str]], dict]]:
    """第三轮：检测相邻表格的行级重叠并去重。

    对每对相邻表格 (T_i, T_{i+1})，比较 T_i 末尾行 与 T_{i+1} 前N行：
    - 行指纹匹配 → 判定为重叠
    - 数值占比 < 0.3 → 表头行，归入 T_{i+1}，从 T_i 删除
    - 数值占比 ≥ 0.3 → 数据行，归入 T_i，从 T_{i+1} 删除
    - 删除记录写入各表的 repair_info["overlap_removed"]

    根因：第二轮修复时，repair_table_rules 各自从数据区向上扫描表头，
    表头行可能在第一轮分割时已经归属到了上一张表格中。

    Args:
        tables: [(table_data, repair_info), ...] 按页面从上到下排列

    Returns:
        去重后的列表（原序，长度不变）
    """
    MAX_CHECK_ROWS = 8  # 每侧最多检查行数（提高以覆盖更多重叠场景）

    if len(tables) < 2:
        return tables

    def _row_fingerprint(row: List[str]) -> str:
        """对一行计算内容指纹（忽略空白、PDF字符编码差异和格式差异）

        使用 _normalize_cell_content 统一归一化后拼接非空单元格内容。
        """
        parts = []
        for c in row:
            s = _normalize_cell_content(str(c))
            if s:
                parts.append(s)
        return " | ".join(parts)

    def _row_cell_set(row: List[str]) -> set:
        """获取一行归一化后的单元格内容集合（用于 Jaccard 相似度计算）"""
        return {
            _normalize_cell_content(str(c))
            for c in row
            if _normalize_cell_content(str(c))
        }

    def _jaccard_similarity(set1: set, set2: set) -> float:
        """计算两个集合的 Jaccard 相似度"""
        if not set1 and not set2:
            return 1.0
        if not set1 or not set2:
            return 0.0
        return len(set1 & set2) / len(set1 | set2)

    def _row_numeric_ratio(row: List[str]) -> float:
        """一行中数值单元格占比（排除年份列干扰）"""
        non_empty = [c for c in row if not _is_effectively_empty(str(c))]
        if not non_empty:
            return 0.0
        return sum(1 for c in non_empty if _is_numeric_cell(str(c))) / len(non_empty)

    # 从后往前处理，避免索引在修改后失效
    for idx in range(len(tables) - 2, -1, -1):
        t1_data, t1_info = tables[idx]
        t2_data, t2_info = tables[idx + 1]

        if not t1_data or not t2_data:
            continue

        # 匹配 T1 末尾行 与 T2 前N行
        t1_tail_rows = min(MAX_CHECK_ROWS, len(t1_data))
        t2_head_rows = min(MAX_CHECK_ROWS, len(t2_data))

        t1_fps = {}  # {fingerprint: row_index_in_t1_data}
        for i in range(len(t1_data) - t1_tail_rows, len(t1_data)):
            fp = _row_fingerprint(t1_data[i])
            if fp:
                t1_fps[fp] = i

        t2_fps = {}
        for j in range(t2_head_rows):
            fp = _row_fingerprint(t2_data[j])
            if fp:
                t2_fps[fp] = j

        # 找到重叠的行指纹（精确匹配）
        overlap_fps = set(t1_fps.keys()) & set(t2_fps.keys())

        # ---- Jaccard 相似度回退匹配：当精确指纹不匹配时，用集合相似度检测 ----
        # 场景：PDF 提取的同一行在不同表格区域中可能存在列位偏移或个别字符差异
        jaccard_matches = {}  # {t1_row_idx: t2_row_idx}
        if not overlap_fps:
            JACCARD_THRESHOLD = 0.7
            for i in range(len(t1_data) - t1_tail_rows, len(t1_data)):
                t1_set = _row_cell_set(t1_data[i])
                if not t1_set:
                    continue
                for j in range(t2_head_rows):
                    # 跳过已被精确匹配占用的行
                    if j in jaccard_matches.values():
                        continue
                    t2_set = _row_cell_set(t2_data[j])
                    if not t2_set:
                        continue
                    sim = _jaccard_similarity(t1_set, t2_set)
                    if sim >= JACCARD_THRESHOLD:
                        # 额外校验：两行非空单元格数不能差太多（列位不同可以容忍，但不能是不同长度的行）
                        size_ratio = min(len(t1_set), len(t2_set)) / max(len(t1_set), len(t2_set))
                        if size_ratio >= 0.5:  # 最少一半的非空单元格匹配上
                            jaccard_matches[i] = j
                            # 也需要确保 T1 的行指纹能映射到 T2 的行指纹（用于后续归属判定）
                            fp_key = _row_fingerprint(t1_data[i])
                            if fp_key:
                                t1_fps[fp_key] = i
                                t2_fps[fp_key] = j
                            break

            if jaccard_matches:
                logger.info(
                    f"  Jaccard 回退: 表{idx}↔表{idx+1} 精确匹配=0, "
                    f"相似度匹配={len(jaccard_matches)} 行"
                    f" (阈值={JACCARD_THRESHOLD})"
                )

        if not overlap_fps and not jaccard_matches:
            continue

        # 对每个重叠行裁决归属
        # 策略：结合数值占比 + 行在 T2 中的位置综合判定
        # - T2 前2行重叠 → 几乎是表头 → 归入 T2（从 T1 删除）
        # - T2 后方重叠 + 数值占比高 → 可能是数据 → 归入 T1（从 T2 删除）
        rows_to_remove_t1 = []  # 从 T1 删除（归入 T2）
        rows_to_remove_t2 = []  # 从 T2 删除（留在 T1）

        for fp in overlap_fps:
            row = t1_data[t1_fps[fp]]  # 取 T1 中的版本（内容应相同）
            ratio = _row_numeric_ratio(row)
            pos_in_t2 = t2_fps[fp]  # 该行在 T2 中的位置

            # 行在 T2 前2行 → 强信号是表头
            if pos_in_t2 <= 1:
                rows_to_remove_t1.append(t1_fps[fp])
            # 行在 T2 第3行及以后 + 数值占比 >= 0.5 → 可能是数据行
            elif pos_in_t2 >= 2 and ratio >= 0.5:
                rows_to_remove_t2.append(t2_fps[fp])
            # 行在 T2 第3行及以后 但 数值占比低 → 仍是表头（如跨页表头重复等）
            else:
                rows_to_remove_t1.append(t1_fps[fp])

        # Jaccard 匹配的归属判定（同上逻辑）
        for t1_idx, t2_idx in jaccard_matches.items():
            if t1_idx not in rows_to_remove_t1 and t2_idx not in rows_to_remove_t2:
                row = t1_data[t1_idx]
                ratio = _row_numeric_ratio(row)
                if t2_idx <= 1:
                    rows_to_remove_t1.append(t1_idx)
                elif t2_idx >= 2 and ratio >= 0.5:
                    rows_to_remove_t2.append(t2_idx)
                else:
                    rows_to_remove_t1.append(t1_idx)

        # 应用删除（从后往前删，避免索引偏移）
        overlap_records_t1 = []
        if rows_to_remove_t1:
            rows_to_remove_t1.sort(reverse=True)
            for row_idx in rows_to_remove_t1:
                overlap_records_t1.append({
                    "index": row_idx,
                    "cells": [str(c).strip() for c in t1_data[row_idx][:6]],
                    "numeric_ratio": round(_row_numeric_ratio(t1_data[row_idx]), 2),
                    "reason": "header_of_table_below",
                })
                del t1_data[row_idx]

        overlap_records_t2 = []
        if rows_to_remove_t2:
            rows_to_remove_t2.sort(reverse=True)
            for row_idx in rows_to_remove_t2:
                overlap_records_t2.append({
                    "index": row_idx,
                    "cells": [str(c).strip() for c in t2_data[row_idx][:6]],
                    "numeric_ratio": round(_row_numeric_ratio(t2_data[row_idx]), 2),
                    "reason": "data_of_table_above",
                })
                del t2_data[row_idx]

        # 记录去重元数据
        if overlap_records_t1:
            t1_info.setdefault("overlap_removed", {})
            t1_info["overlap_removed"] = {
                "count": len(overlap_records_t1),
                "rows": overlap_records_t1,
                "duplicate_with": f"table[{idx + 1}]",
                "direction": "removed_from_upper",
            }
            logger.info(
                f"  去重: 从表{idx}(上)删除 {len(overlap_records_t1)} 行"
                f"（表头行归入表{idx+1}(下)）"
            )

        if overlap_records_t2:
            t2_info.setdefault("overlap_removed", {})
            t2_info["overlap_removed"] = {
                "count": len(overlap_records_t2),
                "rows": overlap_records_t2,
                "duplicate_with": f"table[{idx}]",
                "direction": "removed_from_lower",
            }
            logger.info(
                f"  去重: 从表{idx+1}(下)删除 {len(overlap_records_t2)} 行"
                f"（数据行留在表{idx}(上)）"
            )

        # 更新 rows 元数据
        for info in (t1_info, t2_info):
            if "repaired_rows" in info and info.get("original_rows"):
                # 近似更新：repaired_rows -= removed
                old_repaired = info.get("repaired_rows", 0)
                info["repaired_rows"] = max(1, info["repaired_rows"] - info.get("overlap_removed", {}).get("count", 0))

    return tables


def _has_complete_table_structure(data: List[List[str]]) -> bool:
    """判断表格是否具有完整结构：表头层 + 数据体 + 汇总特征。

    用于跨表去重时判断"后表"是否是一个自洽的完整表格。
    完整的表格拥有其数据的归属权，不完整的前表残片应让位。

    标准：
    1. 至少有 3 行
    2. 有表头层（前 N 行以文本为主，数值占比 < 0.3）
    3. 有数据体（存在数值占比 ≥ 0.3 的行）
    4. 有汇总特征（末尾行含"合计/总计/小计"关键词，或至少有 2 个数据行）

    Args:
        data: 表格行数据

    Returns:
        True 如果表格结构完整
    """
    if len(data) < 3:
        return False

    def _num_ratio(row: List[str]) -> float:
        non_empty = [c for c in row if not _is_effectively_empty(str(c))]
        if not non_empty:
            return 0.0
        return sum(1 for c in non_empty if _is_numeric_cell(str(c))) / len(non_empty)

    # 1. 检测表头层：从上到下，连续的非数值行为表头
    header_end = 0
    for i in range(min(5, len(data))):
        if _num_ratio(data[i]) < 0.3 and any(not _is_effectively_empty(str(c)) for c in data[i]):
            header_end = i + 1
        else:
            break

    has_header = header_end >= 1

    # 2. 检测数据体
    data_row_count = 0
    for i in range(header_end, len(data)):
        if _num_ratio(data[i]) >= 0.3:
            data_row_count += 1

    has_data = data_row_count >= 1

    # 3. 检测汇总特征
    summary_keywords = ['合计', '总计', '小计', 'total', 'sum']
    has_summary = False
    for row in data[-3:]:
        row_text = ' '.join(str(c).strip().lower() for c in row)
        if any(kw in row_text for kw in summary_keywords):
            has_summary = True
            break

    return has_header and has_data and (has_summary or data_row_count >= 2)


def deduplicate_cross_tables(
    source_tables: List[Tuple[List[List[str]], dict]],
    reference_entries: List[Dict],
) -> List[Tuple[List[List[str]], dict]]:
    """跨表去重：前表尾部+头部 ↔ 后表头部 方向性去重（V3.1）。

    根因：两轮提取过程中，后表的表头行被错误分到前表尾部；第二轮修复时
    后表向上扫描重新补充了表头，导致相邻两表出现重叠行。重复永远是：前表
    尾部或头部 = 后表头部。

    核心策略：
    1. 必须是紧相邻的表格（同页或相邻页）
    2. 检查两个维度：
       a. "前表头部 ↔ 后表头部"（表头行重叠，前表前 N 行 vs 后表前 N 行）
       b. "前表尾部 ↔ 后表头部"（数据行重叠，前表最后 N 行 vs 后表前 N 行）
    3. 后表结构完整 → 从前表删除重叠行（后表拥有数据归属权）
    4. 后表结构不完整 → 不做任何删除
    5. 遍历所有紧相邻参考表（防止最近的恰好不完整导致遗漏完整表）

    两个方向均处理：
    - source_is_former（参考表在源表之后）：源表 = 前表，参考表 = 后表
      → 检查源表头部/尾部 ↔ 参考表头部
      → 参考表完整 → 从源表删除
    - source_is_latter（参考表在源表之前）：参考表 = 前表，源表 = 后表
      → 检查参考表头部/尾部 ↔ 源表头部
      → 源表完整 → 从参考表删除（原地修改）

    Args:
        source_tables: [(data, info), ...] 修复拆分后的子表列表（按页面上到下排列）
        reference_entries: 参考表条目列表，每项为字典：
            {
                "data": List[List[str]],    # 表格行数据（原地可修改引用）
                "is_before_source": bool,   # True = 该表在原列表中位于源表之前
                "position": int,            # 在原列表中的索引
            }

    Returns:
        去重后的 source_tables
    """
    if not source_tables or not reference_entries:
        return source_tables

    MAX_CHECK = 8  # 每侧最多检查行数

    # ---- 本地辅助函数 ----
    def _row_fp(row: List[str]) -> str:
        parts = []
        for c in row:
            s = _normalize_cell_content(str(c))
            if s:
                parts.append(s)
        return " | ".join(parts)

    def _row_cell_set(row: List[str]) -> set:
        return {
            _normalize_cell_content(str(c))
            for c in row
            if _normalize_cell_content(str(c))
        }

    def _num_ratio(row: List[str]) -> float:
        non_empty = [c for c in row if not _is_effectively_empty(str(c))]
        if not non_empty:
            return 0.0
        return sum(1 for c in non_empty if _is_numeric_cell(str(c))) / len(non_empty)

    def _jaccard(set1: set, set2: set) -> float:
        if not set1 and not set2:
            return 1.0
        if not set1 or not set2:
            return 0.0
        return len(set1 & set2) / len(set1 | set2)

    def _detect_header_indices(data: List[List[str]], max_rows: int = MAX_CHECK) -> List[int]:
        """检测表头行的索引列表（前 N 行中连续的非数值行）。"""
        indices = []
        for i in range(min(max_rows, len(data))):
            row = data[i]
            nr = _num_ratio(row)
            if nr < 0.3 and any(not _is_effectively_empty(str(c)) for c in row):
                indices.append(i)
            else:
                break  # 遇到第一个数据行即停止
        return indices

    def _row_matches(row_a: List[str], row_b: List[str]) -> bool:
        """检查两行是否匹配（精确指纹 or Jaccard 回退）。"""
        fp_a = _row_fp(row_a)
        fp_b = _row_fp(row_b)
        if fp_a and fp_b and fp_a == fp_b:
            return True
        set_a = _row_cell_set(row_a)
        set_b = _row_cell_set(row_b)
        if set_a and set_b and _jaccard(set_a, set_b) >= 0.7:
            sz = min(len(set_a), len(set_b)) / max(len(set_a), len(set_b))
            if sz >= 0.5:
                return True
        return False

    # ---- V3.1: 遍历所有紧相邻参考表（不再 [:1]） ----
    former_refs = sorted(
        [e for e in reference_entries if e.get("is_before_source", False)],
        key=lambda e: e.get("position", 0),
        reverse=True,  # 最近的 former（position 最大）排前面
    )
    latter_refs = sorted(
        [e for e in reference_entries if not e.get("is_before_source", False)],
        key=lambda e: e.get("position", 9999),  # 最近的 latter（position 最小）排前面
    )

    # ================================================================
    # Case A: 源表是前表（FORMER），参考表是后表（LATTER）
    #   对每个完整后表：源表头部/尾部 ↔ 后表头部
    #   后表结构完整 → 从源表删除
    # ================================================================
    if source_tables and latter_refs:
        last_idx = len(source_tables) - 1
        src_data, src_info = source_tables[last_idx]

        for ref_entry in latter_refs:
            ref_data = ref_entry["data"]
            if not src_data or not ref_data:
                continue
            if not _has_complete_table_structure(ref_data):
                continue  # 后表不完整 → 跳过，继续检查下一个

            rows_to_remove = set()  # 用 set 防止重复

            # ---- A1: 源表头部 ↔ ref头部（表头重叠） ----
            src_head_indices = _detect_header_indices(src_data)
            ref_head_indices = _detect_header_indices(ref_data)
            ref_head_rows = [ref_data[i] for i in ref_head_indices]
            for src_idx in src_head_indices:
                for ref_row in ref_head_rows:
                    if _row_matches(src_data[src_idx], ref_row):
                        rows_to_remove.add(src_idx)
                        break

            # ---- A2: 源表尾部 ↔ ref头部（数据/残留行重叠） ----
            src_tail = src_data[max(0, len(src_data) - MAX_CHECK):]
            src_tail_start = len(src_data) - len(src_tail)
            ref_head = ref_data[:MAX_CHECK]
            for offset, src_row in enumerate(src_tail):
                if not _row_fp(src_row):
                    continue
                for ref_row in ref_head:
                    if _row_matches(src_row, ref_row):
                        rows_to_remove.add(src_tail_start + offset)
                        break

            # 执行删除 + 记录
            if rows_to_remove:
                overlap_records = []
                sorted_indices = sorted(rows_to_remove, reverse=True)
                for row_idx in sorted_indices:
                    overlap_records.append({
                        "index": row_idx,
                        "cells": [str(c).strip() for c in src_data[row_idx][:6]],
                        "numeric_ratio": round(_num_ratio(src_data[row_idx]), 2),
                        "reason": "header_or_tail_overlap_latter_complete",
                    })
                    del src_data[row_idx]

                src_info.setdefault("overlap_removed", {})
                src_info["overlap_removed"] = {
                    "count": len(overlap_records),
                    "rows": overlap_records,
                    "duplicate_with": "adjacent_latter_table",
                    "direction": "removed_from_former",
                    "reason": "latter_table_has_complete_structure",
                }
                logger.info(
                    f"  跨表去重(A): 源表(前)删除 {len(overlap_records)} 行"
                    f"（后表结构完整，重叠数据归入后表）"
                )

    # ================================================================
    # Case B: 参考表是前表（FORMER），源表是后表（LATTER）
    #   对每个前表：ref头部/尾部 ↔ 源表头部
    #   源表结构完整 → 从参考表删除（原地修改）
    # ================================================================
    if source_tables and former_refs:
        first_idx = 0
        src_data, src_info = source_tables[first_idx]

        if not src_data or not _has_complete_table_structure(src_data):
            # 源表（后表）不完整 → 整体跳过，不做任何删除
            pass
        else:
            for ref_entry in former_refs:
                ref_data = ref_entry["data"]
                if not ref_data:
                    continue

                rows_to_remove = set()

                # ---- B1: ref头部 ↔ 源表头部（表头重叠） ----
                ref_head_indices = _detect_header_indices(ref_data)
                src_head_indices = _detect_header_indices(src_data)
                src_head_rows = [src_data[i] for i in src_head_indices]
                for ref_idx in ref_head_indices:
                    for src_row in src_head_rows:
                        if _row_matches(ref_data[ref_idx], src_row):
                            rows_to_remove.add(ref_idx)
                            break

                # ---- B2: ref尾部 ↔ 源表头部（数据/残留行重叠） ----
                ref_tail = ref_data[max(0, len(ref_data) - MAX_CHECK):]
                ref_tail_start = len(ref_data) - len(ref_tail)
                src_head = src_data[:MAX_CHECK]
                for offset, ref_row in enumerate(ref_tail):
                    if not _row_fp(ref_row):
                        continue
                    for src_row in src_head:
                        if _row_matches(ref_row, src_row):
                            rows_to_remove.add(ref_tail_start + offset)
                            break

                # 执行删除（原地修改 ref_data）
                if rows_to_remove:
                    sorted_indices = sorted(rows_to_remove, reverse=True)
                    for row_idx in sorted_indices:
                        del ref_data[row_idx]
                    logger.info(
                        f"  跨表去重(B): 参考表(前)删除 {len(rows_to_remove)} 行"
                        f"（后表=源表结构完整，重叠数据归入后表）"
                    )

    return source_tables


def _has_minimal_data(table: List[List[str]]) -> bool:
    """判断表格是否包含最小的有效数据（至少有 2 行非空且至少 1 个数值）"""
    if not table or len(table) < 2:
        return False
    has_num = False
    nonempty_rows = 0
    for row in table:
        if any(not _is_effectively_empty(c) for c in row):
            nonempty_rows += 1
        if any(_is_numeric_cell(c) for c in row):
            has_num = True
    return nonempty_rows >= 2 and has_num


# ============================================================
# 辅助：生成修复报告
# ============================================================

def generate_rules_repair_report(table_data: List[List[str]],
                                 repaired: List[List[str]],
                                 info: dict) -> str:
    """生成人类可读的规则修复报告"""
    lines = []
    lines.append("=" * 60)
    lines.append("[规则表格结构修复报告]")
    lines.append("=" * 60)
    lines.append("")

    if not info.get('needed'):
        lines.append(f"[i] 无需修复: {info.get('reason', '未知')}")
        return "\n".join(lines)

    lines.append(f"原因: {info.get('reason', '')}")
    lines.append("")
    lines.append("修复步骤:")
    for i, step in enumerate(info.get('steps', []), 1):
        lines.append(f"   {i}. {step}")
    lines.append("")

    lines.append("修复统计:")
    lines.append(f"   原始行数: {info.get('original_rows', 0)}")
    lines.append(f"   修复后行数: {info.get('repaired_rows', 0)}")
    lines.append(f"   最终列数: {info.get('data_cols', 0)}")
    lines.append(f"   表头层数: {info.get('header_rows_found', 0)}")
    lines.append(f"   表头合并: {'是' if info.get('headers_merged') else '否'}")
    lines.append(f"   移除描述: {'是' if info.get('description_removed') else '否'}")
    lines.append(f"   剪除列数: {info.get('columns_pruned', 0)}")
    lines.append("")

    # ---- 异常标记部分 ----
    anomalies = info.get('anomalies', [])
    if anomalies:
        severity_icon = {ANOMALY_HIGH: '🔴', ANOMALY_MEDIUM: '🟡', ANOMALY_LOW: '🔵'}
        lines.append(f"⚠ 异常标记 ({len(anomalies)} 处，建议后期 LLM 确认):")
        lines.append("-" * 40)
        for i, a in enumerate(anomalies, 1):
            icon = severity_icon.get(a['severity'], '⚪')
            lines.append(f"  {i}. {icon} [{a['severity']}] {a['type']}")
            lines.append(f"     {a['description']}")
            lines.append(f"     位置: {a['location']} | 置信度: {a['confidence']:.0%}")
            lines.append("")
    else:
        lines.append("✅ 无异常标记（规则修复结果置信度较高）")
        lines.append("")

    # 预览修复前后
    lines.append("修复前 (前3行):")
    for row in table_data[:3]:
        cells = [c if c else '(空)' for c in row[:6]]
        lines.append(f"   {cells}")
    lines.append("")
    lines.append("修复后 (前3行):")
    for row in repaired[:3]:
        cells = [c if c else '(空)' for c in row[:6]]
        lines.append(f"   {cells}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================
# 为 LLM 确认预留的接口
# ============================================================

def has_high_severity_anomalies(info: dict) -> bool:
    """判断修复结果中是否包含高严重度异常（需要 LLM 确认）"""
    anomalies = info.get('anomalies', [])
    return any(a['severity'] == ANOMALY_HIGH for a in anomalies)


def has_medium_or_higher_anomalies(info: dict) -> bool:
    """判断修复结果中是否包含中或高严重度异常"""
    anomalies = info.get('anomalies', [])
    return any(a['severity'] in (ANOMALY_MEDIUM, ANOMALY_HIGH) for a in anomalies)


def prepare_anomalies_for_llm(info: dict) -> str:
    """将异常信息格式化为 LLM 确认的提示文本

    供后期 LLM 确认阶段使用：将规则修复检测到的异常整理为结构化的
    文本描述，LLM 据此判断是否确认或修正规则修复结果。
    """
    anomalies = info.get('anomalies', [])
    if not anomalies:
        return "(无异常)"

    lines = ["## 规则修复检测到的异常（需 LLM 确认）\n"]

    # 按严重程度分组
    for sev, label in [(ANOMALY_HIGH, "高严重度"), (ANOMALY_MEDIUM, "中严重度"), (ANOMALY_LOW, "低严重度")]:
        group = [a for a in anomalies if a['severity'] == sev]
        if not group:
            continue
        lines.append(f"### {label} ({len(group)} 处)\n")
        for i, a in enumerate(group, 1):
            lines.append(f"**异常 {i}**: [{a['type']}]")
            lines.append(f"  - 描述: {a['description']}")
            lines.append(f"  - 位置: {a['location']}")
            lines.append(f"  - 置信度: {a['confidence']:.0%}")
            if a['details']:
                # 对 multi_table_merged 类型展开上下文详情
                if a['type'] == MULTI_TABLE_MERGED:
                    detail_lines = []
                    ctx_desc = a['details'].get('context_description', [])
                    if ctx_desc:
                        detail_lines.append("  📄 表格上方描述文本:")
                        for d in ctx_desc:
                            detail_lines.append(f"     {d}")
                    sep_rows = a['details'].get('separator_rows', [])
                    if sep_rows:
                        detail_lines.append("  🔀 疑似分隔行:")
                        for s in sep_rows:
                            detail_lines.append(f"     {s}")
                    prev = a['details'].get('orphan_preview', [])
                    if prev:
                        detail_lines.append("  👀 孤儿数据预览（被丢弃的数据）:")
                        for p in prev:
                            detail_lines.append(f"     {p}")
                    if detail_lines:
                        lines.append("  - 详情:")
                        lines.extend(detail_lines)
                    else:
                        lines.append(f"  - 详情: {a['details']}")
                else:
                    lines.append(f"  - 详情: {a['details']}")
            lines.append("")

    return "\n".join(lines)
