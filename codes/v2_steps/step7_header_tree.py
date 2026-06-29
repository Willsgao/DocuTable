# -*- coding: utf-8 -*-
"""
Step 7: 表头树结构建模

借鉴 SMART / HO-Tree 思路，用树结构建模多级表头。

核心能力：
1. 层级关系识别（大类别 → 中类别 → 细分类别）
2. 构建 HeaderNode 树结构（parent-child + column span）
3. 空缺填充（父节点值向下传递到子节点空位）
4. 列归属修正（底层表头列数 = 数据列数）

数据模型：
    HeaderNode
    ├─ label: str          # 表头文本
    ├─ level: int           # 层级（0=顶层, 越大越底层）
    ├─ col_start/col_end    # 列跨度 [start, end)
    ├─ children: List       # 子节点
    └─ parent: Optional     # 父节点
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any


# ============================================================
# 数据模型
# ============================================================

@dataclass
class HeaderNode:
    """表头树节点"""
    label: str
    level: int = 0
    col_start: int = 0
    col_end: int = 0
    children: List[HeaderNode] = field(default_factory=list)
    parent: Optional[HeaderNode] = None

    @property
    def col_span(self) -> int:
        """列跨度"""
        return self.col_end - self.col_start

    @property
    def is_leaf(self) -> bool:
        """是否叶子节点（底层表头）"""
        return len(self.children) == 0

    def to_dict(self) -> dict:
        """递归转字典"""
        return {
            "label": self.label,
            "level": self.level,
            "col_start": self.col_start,
            "col_end": self.col_end,
            "col_span": self.col_span,
            "children": [c.to_dict() for c in self.children],
        }

    def flatten_levels(self) -> List[List[str]]:
        """展平：每层一个 list，非叶子节点在所属列范围展开

        返回: [[row0_col0, row0_col1, ...], [row1_col0, ...], ...]
        """
        if not self.children:
            # 叶子节点：自身占一格
            row = [""] * self.col_end
            row[self.col_start] = self.label
            return [row]

        max_depth = max(c._max_depth() for c in self.children)
        rows: List[List[str]] = [
            [""] * self.col_end for _ in range(max_depth + 1)
        ]

        # 顶层：自己的标签放在第一个子节点的起始列
        if self.label:
            first_child = self.children[0]
            rows[0][first_child.col_start] = self.label

        # 递归子节点
        for child in self.children:
            child_rows = child.flatten_levels()
            for r_idx, row_data in enumerate(child_rows):
                for c_idx in range(child.col_start, min(child.col_end, len(row_data))):
                    if c_idx < len(rows[r_idx + 1]) and row_data[c_idx]:
                        rows[r_idx + 1][c_idx] = row_data[c_idx]

        # 去掉全空行
        return [r for r in rows if any(cell for cell in r)]

    def _max_depth(self) -> int:
        if not self.children:
            return 1
        return 1 + max(c._max_depth() for c in self.children)

    def __repr__(self):
        return f"HeaderNode('{self.label}' L{self.level} [{self.col_start}:{self.col_end}])"


# ============================================================
# 树构建器
# ============================================================

class HeaderTreeBuilder:
    """多级表头 → 树结构构建器

    用法：
        header_rows = [
            ["", "2024年", "", "", "2023年", ""],
            ["资产", "金额", "占比", "资产", "金额", "占比"],
        ]
        tree = HeaderTreeBuilder.build(header_rows, data_cols=6)
        filled = HeaderTreeBuilder.fill_gaps(tree)
    """

    @staticmethod
    def build(header_rows: List[List[str]],
              data_cols: Optional[int] = None) -> HeaderNode:
        """从多级表头行构建树结构

        Args:
            header_rows: 表头行列表，从上到下排列（row[0]=顶层, row[-1]=底层）
            data_cols: 数据列数（None = 用底层表头的列数）

        Returns:
            根节点（label=""，children 指向各顶层类别）

        算法：
        1. 每行识别标签边界（连续非空文本作为一个标签区间）
        2. 上层标签 → parent，下层标签落入其列范围 → child
        3. 递归构建树
        4. 特殊处理：col 0 的孤立标签（如"项目"）自动作为顶层节点
        """
        if not header_rows:
            return HeaderNode(label="", col_start=0, col_end=data_cols or 0)

        # 确定列数
        max_cols = max(len(r) for r in header_rows)
        if data_cols is None:
            data_cols = max_cols
        else:
            data_cols = max(data_cols, max_cols)

        # 规范化：补齐行到统一列数
        normalized = []
        for row in header_rows:
            r = list(row) + [""] * (data_cols - len(row))
            normalized.append(r)

        root = HeaderTreeBuilder._build_level(
            normalized, row_idx=0, col_start=0, col_end=data_cols, level=0)

        # 补充 col 0 孤立标签：
        #   仅当顶层行(row0)完全无标签段时，col0的标签才被视为真实孤立节点。
        #   这是因为如果row0有标签段（如"2024年"/"流动资产"），
        #   则col0的标签属于这些上层标签的某个子组，不应分离。
        covers_col0 = any(c.col_start == 0 for c in root.children)
        row0_has_segments = bool(
            HeaderTreeBuilder._find_label_segments(normalized[0], 0, data_cols))
        if not covers_col0 and not row0_has_segments:
            for row in normalized:
                cell = row[0].strip() if len(row) > 0 else ""
                if cell:
                    col0_node = HeaderNode(
                        label=cell, level=0,
                        col_start=0, col_end=1,
                    )
                    root.children.insert(0, col0_node)
                    col0_node.parent = root
                    break

        return root

    @staticmethod
    def _build_col0_node(label: str, rows: List[List[str]],
                         data_cols: int) -> Optional[HeaderNode]:
        """为 col 0 的孤立标签构建子节点"""
        # 检查 label 在哪些行出现
        for r_idx in range(len(rows)):
            for c in range(data_cols):
                cell = rows[r_idx][c].strip() if c < len(rows[r_idx]) else ""
                if cell == label:
                    return HeaderNode(
                        label=label, level=0,
                        col_start=0, col_end=1,
                    )
        return None

    @staticmethod
    def _build_level(rows: List[List[str]],
                     row_idx: int,
                     col_start: int,
                     col_end: int,
                     level: int) -> HeaderNode:
        """递归构建某一层级的子树

        Args:
            rows: 规范化后的表头行
            row_idx: 当前处理的行索引
            col_start/col_end: 当前节点的列范围
            level: 当前层级
        """
        if row_idx >= len(rows):
            # 叶子层：返回一个占位节点
            return HeaderNode(
                label="", level=level,
                col_start=col_start, col_end=col_end)

        current_row = rows[row_idx]

        # 在当前列范围内找到所有标签段
        segments = HeaderTreeBuilder._find_label_segments(
            current_row, col_start, col_end)

        if not segments:
            # 没有标签段：检查是否低行有孤立的 col 0 标签（如"项目"）
            if col_start == 0 and row_idx + 1 < len(rows):
                for r in range(row_idx + 1, len(rows)):
                    cell = rows[r][0].strip() if rows[r] and len(rows[r]) > 0 else ""
                    if cell and not any(
                        HeaderTreeBuilder._find_label_segments(rows[r], 1, col_end)
                    ):
                        # col 0 有孤立标签 → 作为独立段
                        segments = [(0, 1, cell)]
                        break

            if not segments:
                # 当前行无标签：跳过此行，继续下一行
                return HeaderTreeBuilder._build_level(
                    rows, row_idx + 1, col_start, col_end, level)

        # 为每个标签段创建节点
        node = HeaderNode(
            label=segments[0][2] if len(segments) == 1 else "",
            level=level,
            col_start=col_start,
            col_end=col_end,
        )

        if len(segments) == 1:
            # 单个标签：标签即为此节点的 label
            seg_start, seg_end, seg_label = segments[0]
            node.label = seg_label
            node.col_start = seg_start
            node.col_end = seg_end

            if row_idx + 1 < len(rows):
                # 往下递归
                child = HeaderTreeBuilder._build_level(
                    rows, row_idx + 1, seg_start, seg_end, level + 1)
                child.parent = node
                node.children = [child]
        else:
            # 多个标签段：每个段是一个子节点
            for seg_start, seg_end, seg_label in segments:
                child = HeaderTreeBuilder._build_level(
                    rows, row_idx + 1, seg_start, seg_end, level + 1)
                child.parent = node

                # 叶子保护：如果 child 已有非空标签（来自单段叶子或更低层递归），
                # 保留 child.label，用 seg_label 作为外层包装节点
                if child.label:
                    wrapper = HeaderNode(
                        label=seg_label, level=level,
                        col_start=seg_start, col_end=seg_end)
                    wrapper.children = [child]
                    child.parent = wrapper
                    child = wrapper
                else:
                    child.label = seg_label

                node.children.append(child)

        return node

    @staticmethod
    def _find_label_segments(row: List[str],
                             col_start: int,
                             col_end: int) -> List[Tuple[int, int, str]]:
        """在一行的指定列范围内找到标签段

        标签段 = 连续的非空文本区间，合并相邻的同类标签

        Returns:
            [(seg_start, seg_end, label), ...]  seg_end 为 exclusive
        """
        segments: List[Tuple[int, int, str]] = []
        i = col_start

        while i < col_end:
            cell = row[i].strip() if i < len(row) else ""
            if not cell:
                i += 1
                continue

            # 找到标签段结束
            seg_start = i
            seg_label = cell
            j = i + 1
            while j < col_end:
                next_cell = row[j].strip() if j < len(row) else ""
                if next_cell and next_cell == seg_label:
                    # 相同标签：合并
                    j += 1
                elif not next_cell:
                    # 空单元格：继续扩展（父标签可能占多列）
                    j += 1
                else:
                    break

            segments.append((seg_start, j, seg_label))
            i = j

        return segments

    # ---- 空缺填充 ----

    @staticmethod
    def fill_gaps(root: HeaderNode) -> HeaderNode:
        """填充表头树中的空缺

        规则：
        - 父节点标签向下传递到子节点的空列
        - 同一层相邻标签之间的空列：属于左侧标签的延伸
        - 底层表头的列跨度 = 数据列数

        Args:
            root: 树根节点

        Returns:
            填充后的根节点（原地修改）
        """
        HeaderTreeBuilder._fill_gaps_recursive(root)
        return root

    @staticmethod
    def _fill_gaps_recursive(node: HeaderNode) -> None:
        """递归填充空缺"""
        if not node.children:
            return

        # 如果有父标签，补齐子节点之间的空缺
        if node.label and len(node.children) > 1:
            for i in range(len(node.children) - 1):
                current = node.children[i]
                next_child = node.children[i + 1]
                if current.col_end < next_child.col_start:
                    # 有缺口：用父标签填充
                    gap_start = current.col_end
                    gap_end = next_child.col_start
                    gap_node = HeaderNode(
                        label=node.label,
                        level=current.level,
                        col_start=gap_start,
                        col_end=gap_end,
                        parent=node,
                    )
                    node.children.insert(i + 1, gap_node)

        # 递归
        for child in node.children:
            HeaderTreeBuilder._fill_gaps_recursive(child)

    # ---- 列归属修正 ----

    @staticmethod
    def fix_column_alignment(root: HeaderNode, data_cols: int) -> HeaderNode:
        """修正底层表头列数与数据列数的对齐

        Args:
            root: 树根
            data_cols: 数据列数

        Returns:
            修正后的根节点
        """
        # 找到所有叶子节点
        leaves = HeaderTreeBuilder._collect_leaves(root)

        if not leaves:
            return root

        # 按 col_start 排序
        leaves.sort(key=lambda n: n.col_start)

        # 检查首尾对齐
        if leaves[0].col_start > 0:
            # 首个叶子不始于 col 0：扩展
            leaves[0].col_start = 0

        if leaves[-1].col_end < data_cols:
            # 末个叶子未覆盖到 data_cols：扩展
            leaves[-1].col_end = data_cols

        # 检查叶子间是否有间隙
        for i in range(len(leaves) - 1):
            if leaves[i].col_end < leaves[i + 1].col_start:
                # 间隙：均匀分配给相邻叶子
                mid = (leaves[i].col_end + leaves[i + 1].col_start) // 2
                leaves[i].col_end = mid
                leaves[i + 1].col_start = mid

        return root

    @staticmethod
    def _collect_leaves(node: HeaderNode) -> List[HeaderNode]:
        """收集所有叶子节点"""
        if not node.children:
            return [node] if node.label else []
        leaves = []
        for child in node.children:
            leaves.extend(HeaderTreeBuilder._collect_leaves(child))
        return leaves


# ============================================================
# Step 7 主入口
# ============================================================

class Step7HeaderTree:
    """表头树结构建模（V2 Step 7）

    使用方式：
        tree = Step7HeaderTree.build_tree(header_rows, data_cols=6)
        filled = Step7HeaderTree.fill_and_align(tree, data_cols=6)
        print(tree.to_dict())
    """

    @staticmethod
    def build_tree(header_rows: List[List[str]],
                   data_cols: int = 0) -> HeaderNode:
        """构建表头树

        Args:
            header_rows: 多级表头行（从上到下）
            data_cols: 数据列数
        """
        return HeaderTreeBuilder.build(header_rows, data_cols=data_cols)

    @staticmethod
    def fill_and_align(tree: HeaderNode,
                       data_cols: int = 0) -> HeaderNode:
        """填充空缺 + 修正列归属"""
        tree = HeaderTreeBuilder.fill_gaps(tree)
        if data_cols > 0:
            tree = HeaderTreeBuilder.fix_column_alignment(tree, data_cols)
        return tree

    @staticmethod
    def to_2d_header(tree: HeaderNode) -> List[List[str]]:
        """将树展平为 2D 表头数组（可用于输出）"""
        rows = tree.flatten_levels()
        # 规范化每行长度
        if rows:
            max_cols = max(len(r) for r in rows)
            for r in rows:
                while len(r) < max_cols:
                    r.append("")
        return rows
