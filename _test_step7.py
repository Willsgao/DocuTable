# -*- coding: utf-8 -*-
"""Step 7 表头树结构建模验证
用法: python _test_step7.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from codes.v2_steps.step7_header_tree import (
    HeaderNode, HeaderTreeBuilder, Step7HeaderTree,
)

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        failed += 1


# ====== 1. 简单2级表头 ======

print("=== 1. 2级表头（年份→科目）===")

header = [
    ["", "2024年", "", "", "2023年", ""],
    ["资产", "金额", "占比", "资产", "金额", "占比"],
]

tree = HeaderTreeBuilder.build(header, data_cols=6)
check("2级表头: 根节点存在", tree is not None)
check("2级表头: 顶层2个child", len(tree.children) == 2,
      f"got {len(tree.children)}")
check("2级表头: 左child='2024年'", tree.children[0].label == "2024年")
check("2级表头: 左child col_span=3", tree.children[0].col_span == 3,
      f"got {tree.children[0].col_span}")
check("2级表头: 右child='2023年'", tree.children[1].label == "2023年")

# 检查底层叶子
left_leaves = HeaderTreeBuilder._collect_leaves(tree.children[0])
check("2级表头: 左分支3个叶子", len(left_leaves) == 3,
      f"got {len(left_leaves)}")
check("2级表头: 叶子label='金额'", any(n.label == "金额" for n in left_leaves))

# "2023年" 仅覆盖 2 列（col 4-5），应有 2 个叶子
right_leaves = HeaderTreeBuilder._collect_leaves(tree.children[1])
check("2级表头: 右分支2个叶子", len(right_leaves) == 2,
      f"got {len(right_leaves)}")


# ====== 2. 3级表头 ======

print("\n=== 2. 3级表头（大类→中类→细类）===")

header3 = [
    ["", "流动资产", "", "", "非流动资产", ""],
    ["", "货币资金", "应收账款", "存货", "固定资产", "无形资产"],
    ["项目", "金额", "金额", "金额", "金额", "金额"],
]

tree3 = HeaderTreeBuilder.build(header3, data_cols=6)
check("3级表头: 根有2个child", len(tree3.children) == 2,
      f"got {len(tree3.children)}")
check("3级表头: L0='流动资产'", tree3.children[0].label == "流动资产")

# 中类层
mid = tree3.children[0].children
check("3级表头: 中类有3个child", len(mid) == 3,
      f"got {len(mid)}")
check("3级表头: child[0]='货币资金'", mid[0].label == "货币资金")

# 叶子层
leaf = mid[0].children
check("3级表头: L2[0]='金额'", len(leaf) > 0 and leaf[0].label == "金额")

# 深度（wrapper 保护叶子标签，增加1层）
check("3级表头: max_depth=4", tree3._max_depth() == 4,
      f"got {tree3._max_depth()}")


# ====== 3. 空缺填充 ======

print("\n=== 3. fill_gaps 空缺填充 ===")

# 顶层有gap的2级表头
header_gap = [
    ["2024年", "", "", "2023年", "", ""],
    ["资产", "负债", "收入", "资产", "负债", "收入"],
]
tree_gap = HeaderTreeBuilder.build(header_gap, data_cols=6)
filled = HeaderTreeBuilder.fill_gaps(tree_gap)

# 子节点之间不应有gap
for i in range(len(filled.children) - 1):
    check(f"gap填充: child[{i}].col_end <= child[{i+1}].col_start",
          filled.children[i].col_end <= filled.children[i + 1].col_start,
          f"{filled.children[i].col_end} <= {filled.children[i+1].col_start}")


# ====== 4. 列归属修正 ======

print("\n=== 4. fix_column_alignment ===")

header_short = [
    ["2024年", "", "2023年", ""],
    ["金额", "占比", "金额", "占比"],
]
tree_short = HeaderTreeBuilder.build(header_short, data_cols=4)
fixed = HeaderTreeBuilder.fix_column_alignment(tree_short, data_cols=6)

# 叶子应覆盖到 data_cols=6
all_leaves = HeaderTreeBuilder._collect_leaves(fixed)
max_end = max(n.col_end for n in all_leaves) if all_leaves else 0
check("列修正: 最大col_end=6", max_end == 6,
      f"got {max_end}")


# ====== 5. to_dict / to_2d_header ======

print("\n=== 5. to_dict / to_2d_header ===")

d = tree.to_dict()
check("to_dict: label", d["label"] == "")
check("to_dict: children", len(d["children"]) == 2)

flat = Step7HeaderTree.to_2d_header(tree)
check("to_2d_header: 2行", len(flat) == 2, f"got {len(flat)}")  # 2级表头
check("to_2d_header: 每行6列", all(len(r) == 6 for r in flat),
      f"got {[len(r) for r in flat]}")


# ====== 6. 单行表头（退化情况）=====

print("\n=== 6. 单行表头 ===")

header1 = [["项目", "金额", "占比"]]
tree1 = HeaderTreeBuilder.build(header1, data_cols=3)
check("单行表头: 3个叶子", len(HeaderTreeBuilder._collect_leaves(tree1)) == 3)


# ====== 7. flatten_levels ======

print("\n=== 7. flatten_levels 展平 ===")

flat3 = tree3.flatten_levels()
check("3级展平: 3行（wrapper保护后完整层级）", len(flat3) == 3,
      f"got {len(flat3)}: {[r for r in flat3 if any(c for c in r)]}")
check("3级展平: 流动资产存在", any("流动资产" in r for r in flat3))
# 注：col0 的 "项目" 在 row0 有标签段时视为子组的一部分，不独立


# ====== 汇总 ======
print(f"\n{'='*40}")
print(f"Results: {passed} PASS, {failed} FAIL")
if failed == 0:
    print("All passed!")
else:
    print(f"FAILED: {failed} tests")
