# -*- coding: utf-8 -*-
"""还原主策略（硬顺序，不可颠倒）。

以 liteparse 原始 text_item 字框为源，在已切开/合并好的单表上：

  1. 表级切分/合并（表内再表头切开、跨页续表接上）—— 上游/格式纠错边界任务
  2. 锚定 liteparse 字框（本表 bbox 内的 text_items）
  3. 粘连必拆 + 锁定数据主体（连续行、同列同型）
  4. 用主体列界反向对齐行/列表头（规则）
  5. 仍失败 → 才 LLM；禁止为对齐而编造金额
"""

from __future__ import annotations

# 阶段名（写入 _reconstruct.policy_stage）
STAGE_ANCHOR = "anchor_liteparse"
STAGE_GLUE = "glue_split"
STAGE_DATA_BODY = "lock_data_body"
STAGE_HEADER = "align_headers_from_body"
STAGE_RULES = "rule_checklist"
STAGE_LLM = "llm_optional"
STAGE_DONE = "done"

POLICY_ORDER = (
    STAGE_ANCHOR,
    STAGE_GLUE,
    STAGE_DATA_BODY,
    STAGE_HEADER,
    STAGE_RULES,
    STAGE_LLM,
    STAGE_DONE,
)

POLICY_SUMMARY = (
    "liteparse字框为源 → 切分/合并后的单表 → 数据主体+同列同型 → 反向定表头 → 最后LLM"
)
