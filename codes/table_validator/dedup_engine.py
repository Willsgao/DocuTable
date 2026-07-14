# -*- coding: utf-8 -*-
"""
统一去重引擎（DeduplicationEngine）—— 架构修复 #3

硬伤诊断：
  8 个去重函数分布在 5 个文件中，同一业务不变量（"两表不应重复"）有 4 个不同实现。
  判定口径不一致（V4→V5 碎片保护不同步），导致漏删/误删/规则漂移。

解决方案：
  DeduplicationEngine 是唯一执法点。所有去重统一经此模块。
  单一指纹计算、单一 Jaccard、单一数值占比判定。

用法：
    engine = DeduplicationEngine()
    results = engine.run_all(results)  # 一站式去重
    # 或分步调用
    results = engine.dedup_text_against_tables(results)
    results = engine.dedup_adjacent(results)

设计原则：
  - 单一入口 + 可组合的独立操作
  - 一致的 _row_fingerprint / _normalize_cell / _jaccard 实现
  - DedupPolicy 控制各操作的阈值和行为
  - 去重记录写入 _dedup_metadata 供调试
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ==================================================================
# DedupPolicy — 统一的去重配置
# ==================================================================

@dataclass
class DedupPolicy:
    """去重策略配置，所有阈值集中管理。"""

    # ── 相邻表去重 ──
    max_check_rows: int = 8
    jaccard_threshold: float = 0.70
    size_ratio_threshold: float = 0.50

    # ── 碎片表保护 ──
    fragment_max_total_rows: int = 5
    fragment_max_data_rows: int = 1
    fragment_max_header_rows: int = 1

    # ── 表格→文本去重 ──
    text_dedup_max_rows: int = 12
    text_overlap_threshold: float = 0.60

    # ── 跨表去重 ──
    cross_dedup_max_check: int = 8
    cross_dedup_page_range: int = 1

    # ── 开关 ──
    enable_adjacent_dedup: bool = True
    enable_text_dedup: bool = True
    enable_cross_dedup: bool = True
    enable_fragment_protection: bool = True
    enable_subset_detection: bool = True  # A0 整表子集检测


# ==================================================================
# 归一化工具（统一实现）
# ==================================================================

# PDF 字符编码差异映射
_CHAR_NORMALIZE_MAP = {
    "\u2013": "-",  # – (en dash)
    "\u2014": "-",  # — (em dash)
    "\u2212": "-",  # − (minus sign)
    "\u2018": "'",  # ' (left single quote)
    "\u2019": "'",  # ' (right single quote)
    "\u201c": '"',  # " (left double quote)
    "\u201d": '"',  # " (right double quote)
    "\u00a0": " ",  # 不间断空格
    "\u200b": "",   # 零宽空格
    "\u200c": "",   # 零宽非连接符
    "\u200d": "",   # 零宽连接符
}

_CHAR_NORMALIZE_RE = re.compile("|".join(re.escape(k) for k in _CHAR_NORMALIZE_MAP))


def normalize_cell(content: str) -> str:
    """统一单元格内容归一化：去除PDF编码差异、空白规范化。

    替代分散在 table_structure_repair.py 和 processor.py 中的多个 _normalize_cell_content 实现。
    """
    if not content:
        return ""
    # 1. 替换已知编码变体
    s = _CHAR_NORMALIZE_RE.sub(lambda m: _CHAR_NORMALIZE_MAP[m.group(0)], str(content))
    # 2. 移除多余空白
    s = re.sub(r"\s+", " ", s)
    # 3. 去除首尾空白
    return s.strip()


def row_fingerprint(row: List[str]) -> str:
    """统一的行指纹计算：归一化非空单元格后拼接。

    替代分散在多处的 _row_fp / _row_fingerprint 实现。
    """
    parts = []
    for cell in row:
        s = normalize_cell(str(cell))
        if s:
            parts.append(s)
    return " | ".join(parts)


def row_cell_set(row: List[str]) -> Set[str]:
    """将一行转为归一化的单元格集合（用于 Jaccard）。"""
    return {
        normalize_cell(str(c))
        for c in row
        if normalize_cell(str(c))
    }


def jaccard_similarity(set_a: Set, set_b: Set) -> float:
    """统一的 Jaccard 相似度计算。"""
    if not set_a and not set_b:
        return 1.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def is_numeric_cell(text: str) -> bool:
    """统一的数值单元格判定。"""
    t = text.strip()
    if not t:
        return False
    # 纯划线占位 → 非数值
    if re.match(r"^[\s\-–—]+$", t):
        return False
    # 纯日期 → 非数值
    if re.match(r"^\d{1,2}[/\-月]\d{1,2}[日/]?", t):
        return False
    # 百分比/数值检测
    cleaned = t.replace(",", "").replace(" ", "").replace("%", "")
    cleaned = re.sub(r"^[\(（\-]", "", cleaned)
    cleaned = re.sub(r"[\)）]$", "", cleaned)
    cleaned = re.sub(r"%$", "", cleaned)
    try:
        float(cleaned)
        return True
    except (ValueError, TypeError):
        return False


def row_numeric_ratio(row: List[str]) -> float:
    """统一的行数值占比计算。"""
    non_empty = [
        normalize_cell(str(c))
        for c in row
        if normalize_cell(str(c))
    ]
    if not non_empty:
        return 0.0
    numeric_count = sum(1 for c in non_empty if is_numeric_cell(c))
    return numeric_count / len(non_empty)


def is_effectively_empty(text: str) -> bool:
    """判定单元格是否为有效空（去除空白后为空）。"""
    return not normalize_cell(str(text))


# ==================================================================
# DeduplicationEngine — 统一去重引擎
# ==================================================================

class DeduplicationEngine:
    """统一去重引擎。

    所有去重操作的唯一入口。替代分散在 processor.py、table_structure_repair.py、
    UI 层中的 8 个独立去重函数。

    核心不变量的唯一执法点：
      - 相邻两表不应重复
      - 表内内容不应再出现在段落里
      - 碎片表/表头应受保护
    """

    def __init__(self, policy: Optional[DedupPolicy] = None):
        self.policy = policy or DedupPolicy()
        self._debug_log: List[str] = []

    # ── 一站式入口 ──────────────────────────────────────────

    def run_all(self, results: List[dict]) -> List[dict]:
        """一站式去重：执行所有启用的去重操作。"""
        self._debug_log.clear()

        if self.policy.enable_text_dedup:
            results = self.dedup_text_against_tables(results)

        if self.policy.enable_adjacent_dedup:
            results = self.dedup_adjacent(results)

        return results

    # ── 操作 1: 表格→文本去重 ─────────────────────────────────

    def dedup_text_against_tables(self, results: List[dict]) -> List[dict]:
        """表格→文本去重：已被表格内容覆盖的段落/注解不应重复出现。

        三层策略（按优先级）：
          Tier 1: item_index 精确去重（零误杀）
          Tier 2: bbox 空间重叠判定
          Tier 3: token 内容重叠判定
        """
        tables = [
            r for r in results
            if r.get("type") not in ("paragraph", "annotation", "failed")
        ]
        text_entries = [
            r for r in results
            if r.get("type") in ("paragraph", "annotation")
        ]

        if not text_entries:
            return results

        # ── 准备索引 ──
        table_indices: Dict[int, Set[int]] = {}    # page → item_index set
        table_bboxes: Dict[int, List[Tuple[float, float, float, float]]] = {}  # page → [(y0,y1,x0,x1)]
        table_tokens: Dict[int, Set[str]] = {}     # page → normalized token set

        for t in tables:
            pg = t.get("page", 0)

            # Tier 1/2: item_index
            text_items = t.get("text_items", [])
            if text_items:
                indices = {
                    it.get("item_index", 0)
                    for it in text_items
                    if it.get("item_index", 0)
                }
                table_indices.setdefault(pg, set()).update(indices)

            # Tier 2/3: bbox
            y0, y1 = t.get("y0"), t.get("y1")
            x0, x1 = t.get("x0"), t.get("x1")
            if y0 and y1:
                table_bboxes.setdefault(pg, []).append(
                    (y0 if y0 else 0, y1 if y1 else 0,
                     x0 if x0 else 0, x1 if x1 else 9999)
                )

            # Tier 3: tokens
            data = t.get("data", [])
            if data:
                tokens: Set[str] = set()
                for row in data:
                    if isinstance(row, list):
                        for cell in row:
                            if cell:
                                tokens.update(self._tokenize(str(cell)))
                table_tokens.setdefault(pg, set()).update(tokens)

        # ── 去重判定 ──
        entries_to_remove: Set[int] = set()
        text_meta = []  # 记录去重详情

        for idx, entry in enumerate(text_entries):
            pg = entry.get("page", 0)
            entry_type = entry.get("type", "paragraph")

            # Tier 1: item_index 精确去重
            source_indices = entry.get("_source_item_indices", set())
            if source_indices and pg in table_indices:
                overlap = source_indices & table_indices[pg]
                if overlap:
                    entries_to_remove.add(idx)
                    text_meta.append(f"[Tier1] P{pg} {entry_type}#{idx}: index_overlap={len(overlap)}")
                    continue

            # Tier 2: bbox 空间重叠
            if pg in table_bboxes:
                entry_y0 = entry.get("y0", 0)
                entry_y1 = entry.get("y1", 0)
                if self._has_bbox_overlap(entry_y0, entry_y1, table_bboxes[pg]):
                    entries_to_remove.add(idx)
                    text_meta.append(f"[Tier2] P{pg} {entry_type}#{idx}: bbox_overlap")
                    continue

            # Tier 3: token 内容重叠
            if pg in table_tokens:
                entry_text = entry.get("context_text", "")
                if entry_text:
                    entry_tokens = self._tokenize(entry_text)
                    if entry_tokens:
                        overlap_ratio = len(entry_tokens & table_tokens[pg]) / len(entry_tokens)
                        if overlap_ratio >= self.policy.text_overlap_threshold:
                            entries_to_remove.add(idx)
                            text_meta.append(
                                f"[Tier3] P{pg} {entry_type}#{idx}: token_overlap={overlap_ratio:.2f}"
                            )

        # ── 执行删除 ──
        if entries_to_remove:
            for entry in text_meta:
                self._log(entry)
            results = [
                r for i, r in enumerate(results)
                if i not in entries_to_remove
            ]

        return results

    # ── 操作 2: 相邻表去重 A0/A1/A2 ────────────────────────────

    def dedup_adjacent(self, results: List[dict]) -> List[dict]:
        """相邻表格去重：同页内相邻表对 (T_i, T_{i+1}) 去重。

        三个策略（按优先级）：
          A0: 整表子集检测 — 表A全部行 = 表B前缀 → 移除表A
          A1: 表头行重叠 — 表i头部 ↔ 表i+1头部
          A2: 数据行重叠 — 表i尾部 ↔ 表i+1头部

        碎片保护：
          仅当表A是真实碎片（≤1表头行 + ≤1数据行 + ≤5总行）时
          才从表A删除表头行，否则保护完整表不被误删。
        """
        if not results or len(results) < 2:
            return results

        MAX_CHECK = self.policy.max_check_rows

        # 按页分组
        by_page: Dict[int, List[int]] = {}
        for i, r in enumerate(results):
            if r.get("type") == "paragraph":
                continue
            by_page.setdefault(r.get("page", 0), []).append(i)

        entries_to_remove: Set[int] = set()

        for pg, indices in by_page.items():
            if len(indices) < 2:
                continue

            for a_idx in range(len(indices) - 1):
                i_a = indices[a_idx]
                i_b = indices[a_idx + 1]

                if i_a in entries_to_remove or i_b in entries_to_remove:
                    continue

                table_a = results[i_a]
                table_b = results[i_b]
                data_a = table_a.get("data", [])
                data_b = table_b.get("data", [])

                if not data_a or not data_b:
                    continue

                # ── A0: 整表子集检测 ──
                if self.policy.enable_subset_detection:
                    if self._is_table_data_subset(data_a, data_b):
                        self._log(f"[A0] P{pg} 表#{i_a}→#{i_b}: 整表子集，移除#{i_a}")
                        entries_to_remove.add(i_a)
                        continue
                    if self._is_table_data_subset(data_b, data_a):
                        self._log(f"[A0] P{pg} 表#{i_b}→#{i_a}: 整表子集，移除#{i_b}")
                        entries_to_remove.add(i_b)
                        continue

                # ── A1/A2: 行级去重 ──
                is_fragment_a = self._is_fragment_table(data_a)
                is_fragment_b = self._is_fragment_table(data_b)

                # A1: 前表头部 ↔ 后表头部
                check_a_head = min(len(data_a), MAX_CHECK)
                check_b_head = min(len(data_b), MAX_CHECK)

                for pos_a in range(check_a_head):
                    fp_a = row_fingerprint(data_a[pos_a])
                    nr_a = row_numeric_ratio(data_a[pos_a])
                    if not fp_a:
                        continue

                    for pos_b in range(check_b_head):
                        fp_b = row_fingerprint(data_b[pos_b])
                        if fp_a == fp_b:
                            # 精确匹配
                            if nr_a < 0.30:
                                # 表头行 → 归入后表
                                if is_fragment_a:
                                    data_a[pos_a] = [""] * len(data_a[pos_a])
                                    self._log(
                                        f"[A1] P{pg} 表#{i_a}行{pos_a}↔#{i_b}行{pos_b}: "
                                        f"表头→从碎片#{i_a}删除"
                                    )
                            else:
                                # 数据行 → 归入前表
                                data_b[pos_b] = [""] * len(data_b[pos_b])
                                self._log(
                                    f"[A1] P{pg} 表#{i_a}行{pos_a}↔#{i_b}行{pos_b}: "
                                    f"数据→从#{i_b}删除"
                                )
                            break

                # A2: 前表尾部 ↔ 后表头部
                tail_start = max(0, len(data_a) - MAX_CHECK)
                for pos_a in range(tail_start, len(data_a)):
                    fp_a = row_fingerprint(data_a[pos_a])
                    nr_a = row_numeric_ratio(data_a[pos_a])
                    if not fp_a:
                        continue

                    for pos_b in range(check_b_head):
                        fp_b = row_fingerprint(data_b[pos_b])
                        if fp_a == fp_b:
                            if nr_a < 0.30 and is_fragment_a:
                                data_a[pos_a] = [""] * len(data_a[pos_a])
                                self._log(
                                    f"[A2] P{pg} 表#{i_a}尾{pos_a}↔#{i_b}头{pos_b}: "
                                    f"表头→从碎片#{i_a}删除"
                                )
                            else:
                                data_b[pos_b] = [""] * len(data_b[pos_b])
                                self._log(
                                    f"[A2] P{pg} 表#{i_a}尾{pos_a}↔#{i_b}头{pos_b}: "
                                    f"从#{i_b}删除"
                                )
                            break

                    # Jaccard 回退
                    if not any(
                        fp_a == row_fingerprint(data_b[pb])
                        for pb in range(check_b_head)
                    ):
                        set_a = row_cell_set(data_a[pos_a])
                        for pos_b in range(check_b_head):
                            set_b = row_cell_set(data_b[pos_b])
                            jac = jaccard_similarity(set_a, set_b)
                            if jac >= self.policy.jaccard_threshold:
                                size_ratio = min(len(set_a), len(set_b)) / max(
                                    len(set_a), len(set_b), 1
                                )
                                if size_ratio >= self.policy.size_ratio_threshold:
                                    if is_fragment_a:
                                        data_a[pos_a] = [""] * len(data_a[pos_a])
                                        self._log(
                                            f"[A2-J] P{pg} 表#{i_a}尾↔#{i_b}头: "
                                            f"Jaccard={jac:.2f}→从碎片#{i_a}删除"
                                        )
                                    else:
                                        data_b[pos_b] = [""] * len(data_b[pos_b])
                                        self._log(
                                            f"[A2-J] P{pg} 表#{i_a}尾↔#{i_b}头: "
                                            f"Jaccard={jac:.2f}→从#{i_b}删除"
                                        )
                                    break

        # ── 移除 A0 标记的表 ──
        if entries_to_remove:
            results = [
                r for i, r in enumerate(results)
                if i not in entries_to_remove
            ]

        return results

    # ── 操作 3: 跨表去重 ──────────────────────────────────────

    def dedup_cross(
        self,
        results: List[dict],
        reference_entries: List[dict],
    ) -> List[dict]:
        """跨表去重：检查结果与同页面（或相邻页）其他独立表格的重叠。

        尾头方向：前表尾部 ↔ 后表头部
        后表结构完整 → 从前表删除；后表不完整 → 不删
        """
        if not results or not reference_entries:
            return results

        from codes.table_validator.table_structure_repair import (
            _has_complete_table_structure,
        )

        MAX_CHECK = self.policy.cross_dedup_max_check

        for i, entry in enumerate(results):
            entry_pg = entry.get("page", 0)
            entry_data = entry.get("data", [])
            if not entry_data:
                continue

            for ref in reference_entries:
                ref_pg = ref.get("page", 0)
                ref_data = ref.get("data", [])
                if not ref_data:
                    continue

                # 紧相邻限制
                if abs(entry_pg - ref_pg) > self.policy.cross_dedup_page_range:
                    continue

                # Case A: entry 尾部 vs ref 头部
                if self._has_complete_structure(ref_data):
                    tail_start = max(0, len(entry_data) - MAX_CHECK)
                    for pa in range(tail_start, len(entry_data)):
                        fp_a = row_fingerprint(entry_data[pa])
                        if not fp_a:
                            continue
                        for pb in range(min(MAX_CHECK, len(ref_data))):
                            if fp_a == row_fingerprint(ref_data[pb]):
                                entry_data[pa] = [""] * len(entry_data[pa])
                                self._log(
                                    f"[Cross-A] P{entry_pg}#{i}尾{pa}↔P{ref_pg}头{pb}: 从#{i}删除"
                                )
                                break

                # Case B: ref 尾部 vs entry 头部
                if self._has_complete_structure(entry_data):
                    ref_tail = max(0, len(ref_data) - MAX_CHECK)
                    for pb in range(ref_tail, len(ref_data)):
                        fp_b = row_fingerprint(ref_data[pb])
                        if not fp_b:
                            continue
                        for pa in range(min(MAX_CHECK, len(entry_data))):
                            if fp_b == row_fingerprint(entry_data[pa]):
                                ref_data[pb] = [""] * len(ref_data[pb])
                                self._log(
                                    f"[Cross-B] P{ref_pg}尾{pb}↔P{entry_pg}#{i}头{pa}: 从ref删除"
                                )
                                break

        return results

    # ── 内部判定工具 ──────────────────────────────────────────

    def _is_fragment_table(self, data: List[List[str]]) -> bool:
        """判定表是否为碎片表。"""
        if not self.policy.enable_fragment_protection:
            return False

        total_rows = len(data)
        if total_rows > self.policy.fragment_max_total_rows:
            return False

        data_rows = sum(
            1 for row in data
            if row_numeric_ratio(row) >= 0.30
        )
        header_rows = total_rows - data_rows

        if data_rows == 0:
            return True
        if (
            data_rows <= self.policy.fragment_max_data_rows
            and header_rows <= self.policy.fragment_max_header_rows
            and total_rows <= self.policy.fragment_max_total_rows
        ):
            return True

        return False

    def _is_table_data_subset(
        self, data_a: List[List[str]], data_b: List[List[str]]
    ) -> bool:
        """A0: 表A是否为表B的前缀子集。"""
        if not data_a or not data_b:
            return False
        if len(data_a) >= len(data_b):
            return False

        # 三重校验
        # 1. 同位精确匹配
        all_match = True
        for pos in range(len(data_a)):
            fp_a = row_fingerprint(data_a[pos])
            fp_b = row_fingerprint(data_b[pos])
            if not fp_a or not fp_b or fp_a != fp_b:
                all_match = False
                break
        if not all_match:
            return False

        # 2. 集合子集
        set_a = {row_fingerprint(r) for r in data_a if row_fingerprint(r)}
        set_b = {row_fingerprint(r) for r in data_b if row_fingerprint(r)}
        if not set_a.issubset(set_b):
            return False

        # 3. 非空行数严格多于
        nonempty_a = sum(1 for r in data_a if row_fingerprint(r))
        nonempty_b = sum(1 for r in data_b if row_fingerprint(r))
        return nonempty_b > nonempty_a

    def _has_complete_structure(self, data: List[List[str]]) -> bool:
        """检测表结构完整性（表头+数据体+汇总特征）。"""
        if len(data) < 2:
            return False

        # 表头层：前几行数值占比 < 0.3
        has_header = any(
            row_numeric_ratio(data[i]) < 0.30
            for i in range(min(5, len(data)))
        )

        # 数据体：至少 2 行数值占比 >= 0.3
        data_rows = sum(
            1 for row in data
            if row_numeric_ratio(row) >= 0.30
        )

        # 汇总特征：末尾含合计/总计关键词 或 2+数据行
        summary_keywords = ["合计", "总计", "总额", "小计", "累计"]
        has_summary = any(
            any(kw in normalize_cell(str(c)) for kw in summary_keywords)
            for row in data[-3:]
            for c in row
        )

        return has_header and data_rows >= 2 and (has_summary or data_rows >= 3)

    @staticmethod
    def _has_bbox_overlap(
        y0: float, y1: float,
        bboxes: List[Tuple[float, float, float, float]],
        min_overlap_ratio: float = 0.50,
    ) -> bool:
        """检测 Y 范围是否与表格 bbox 有显著重叠。"""
        if y0 <= 0 and y1 <= 0:
            return False
        item_h = y1 - y0
        if item_h <= 0:
            return False
        for by0, by1, bx0, bx1 in bboxes:
            overlap = min(y1, by1) - max(y0, by0)
            if overlap / item_h >= min_overlap_ratio:
                return True
        return False

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        """将文本拆分为规范化 token 集合。"""
        if not text:
            return set()
        # 分割为 token（汉字/英文/数字）
        tokens = set()
        for token in re.split(r"[，,。\.\s\(\)（）;；:：、/\\\-–—]+", text):
            token = normalize_cell(token)
            if token and len(token) >= 2:
                tokens.add(token)
        return tokens

    # ── 日志 ──

    def _log(self, msg: str) -> None:
        self._debug_log.append(msg)

    def get_log(self) -> str:
        return "\n".join(self._debug_log)

    def print_summary(self) -> None:
        """打印去重摘要。"""
        if not self._debug_log:
            return
        print(f"\n[DeduplicationEngine] 共执行 {len(self._debug_log)} 次去重操作:")

        # 按类型统计
        type_counts: Dict[str, int] = {}
        for entry in self._debug_log:
            prefix = entry.split("]")[0].lstrip("[")
            type_counts[prefix] = type_counts.get(prefix, 0) + 1

        for t, c in sorted(type_counts.items()):
            print(f"  {t}: {c}")
        print()


# ==================================================================
# 便捷函数
# ==================================================================

def dedup_all(results: List[dict], policy: Optional[DedupPolicy] = None) -> List[dict]:
    """便捷函数：一键去重。"""
    engine = DeduplicationEngine(policy)
    return engine.run_all(results)
