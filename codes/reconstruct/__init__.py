# -*- coding: utf-8 -*-
"""
PDF 精准还原主链（通用层）
========================

硬顺序（见 policy）：
  liteparse 字框为源 → 表切开/合并 → 数据主体+同列同型 → 反向定表头 → 最后 LLM

  A. 解析还原（通用）  ← 当前
  B. 领域映射（金融包等）  ← 后续
  C. 入库闸门            ← 后续
"""

from codes.reconstruct.pipeline import (
    run_table_reconstruct,
    run_payload_reconstruct,
)
from codes.reconstruct.snapshot import (
    RECONSTRUCT_VERSION,
    build_reconstruct_snapshot,
)
from codes.reconstruct.policy import POLICY_ORDER, POLICY_SUMMARY

try:
    from codes.reconstruct.grid_nucleus import (
        GRID_NUCLEUS,
        apply_grid_to_table,
        restore_table_grid,
    )
except Exception:  # pragma: no cover
    GRID_NUCLEUS = {"enabled": False}
    apply_grid_to_table = None  # type: ignore
    restore_table_grid = None  # type: ignore

__all__ = [
    "RECONSTRUCT_VERSION",
    "POLICY_ORDER",
    "POLICY_SUMMARY",
    "build_reconstruct_snapshot",
    "run_table_reconstruct",
    "run_payload_reconstruct",
    "GRID_NUCLEUS",
    "apply_grid_to_table",
    "restore_table_grid",
]
