# -*- coding: utf-8 -*-
"""Step 4 LLM 智能路由快速验证
用法: python _test_step4.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from codes.v2_steps.step4_llm_router import Step4LlmRouter, POSITIONAL_ANOMALY_TYPES, SEMANTIC_ANOMALY_TYPES

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


# ====== 单个异常路由测试 ======

print("=== 1. should_invoke_llm 单异常决策 ===")

# 高置信度锚定偏移 → 跳过
check("高置信锚定偏移 -> skip",
      not Step4LlmRouter.should_invoke_llm({
          "type": "anchor_shift", "severity": "medium", "confidence": 0.9
      }))

# 低置信锚定偏移 → 跳过（位置类始终跳过）
check("低置信锚定偏移 -> skip",
      not Step4LlmRouter.should_invoke_llm({
          "type": "anchor_shift", "severity": "high", "confidence": 0.3
      }))

# 弱锚定 → 跳过
check("弱锚定(weak_anchor) -> skip",
      not Step4LlmRouter.should_invoke_llm({
          "type": "weak_anchor", "severity": "medium", "confidence": 0.5
      }))

# 表头文字缺失 → 需要 LLM
check("表头文字缺失 -> need LLM",
      Step4LlmRouter.should_invoke_llm({
          "type": "header_text_missing", "severity": "high", "confidence": 0.6
      }))

# 截断表头合并 → 需要 LLM
check("截断表头合并 -> need LLM",
      Step4LlmRouter.should_invoke_llm({
          "type": "truncated_header_merged", "severity": "medium", "confidence": 0.5
      }))

# 多表合并 → 需要 LLM
check("多表合并 -> need LLM",
      Step4LlmRouter.should_invoke_llm({
          "type": "multi_table_merged", "severity": "high", "confidence": 0.4
      }))

# 数据-表头不匹配 → 需要 LLM
check("数据表头不匹配 -> need LLM",
      Step4LlmRouter.should_invoke_llm({
          "type": "data_header_mismatch", "severity": "high", "confidence": 0.5
      }))

# 孤立表头文本 → 需要 LLM
check("孤立表头文本 -> need LLM",
      Step4LlmRouter.should_invoke_llm({
          "type": "orphan_header_text", "severity": "medium", "confidence": 0.5
      }))

# 语义异常但高置信 → 跳过
check("语义异常但高置信(0.9) -> skip",
      not Step4LlmRouter.should_invoke_llm({
          "type": "header_text_missing", "severity": "medium", "confidence": 0.9
      }))

# 语义类型即使低严重度 → 仍需 LLM（规则3优先于规则4）
check("语义类型+低严重度 -> need LLM",
      Step4LlmRouter.should_invoke_llm({
          "type": "data_header_mismatch", "severity": "low", "confidence": 0.7
      }))

# 非语义非位置 + 低置信中严重度 → 规则4触发 need LLM
check("未知类型+低置信中严重度 -> need LLM",
      Step4LlmRouter.should_invoke_llm({
          "type": "unknown_issue", "severity": "medium", "confidence": 0.4
      }))

# 非语义非位置 + 高置信 → 规则1跳过
check("未知类型+高置信 -> skip",
      not Step4LlmRouter.should_invoke_llm({
          "type": "unknown_issue", "severity": "medium", "confidence": 0.85
      }))


# ====== 批量路由测试 ======

print("\n=== 2. route_anomalies 批量路由 ===")

# 混合异常场景
mixed_anomalies = [
    {"type": "anchor_shift", "severity": "medium", "confidence": 0.85, "description": "列1锚定偏移"},
    {"type": "weak_anchor", "severity": "low", "confidence": 0.7, "description": "col0弱锚定"},
    {"type": "header_text_missing", "severity": "high", "confidence": 0.5, "description": "2列缺失表头"},
    {"type": "truncated_header_merged", "severity": "medium", "confidence": 0.4, "description": "表头合并截断"},
    {"type": "multi_table_merged", "severity": "high", "confidence": 0.3, "description": "疑似多表合并"},
]

result = Step4LlmRouter.route_anomalies(mixed_anomalies, page_num=1)
check("批量路由: need_llm=3", len(result.need_llm) == 3,
      f"got {len(result.need_llm)}")
check("批量路由: skip=2", len(result.skip) == 2,
      f"got {len(result.skip)}")
check("批量路由: should_call_llm=True", result.should_call_llm)
check("批量路由: llm_saved=2", result.llm_saved_count == 2,
      f"got {result.llm_saved_count}")

# 仅位置异常（全部跳过）
pos_only = [
    {"type": "anchor_shift", "severity": "medium", "confidence": 0.7, "description": "锚定偏移1"},
    {"type": "weak_anchor", "severity": "low", "confidence": 0.5, "description": "弱锚定"},
]
result2 = Step4LlmRouter.route_anomalies(pos_only, page_num=2)
check("纯位置异常: should_call_llm=False", not result2.should_call_llm)

# 全需 LLM（语义异常）
semantic_only = [
    {"type": "header_text_missing", "severity": "high", "confidence": 0.5, "description": "缺表头"},
    {"type": "truncated_header_merged", "severity": "medium", "confidence": 0.4, "description": "合并截断"},
]
result3 = Step4LlmRouter.route_anomalies(semantic_only, page_num=3)
check("全语义异常: need_llm=2", len(result3.need_llm) == 2,
      f"got {len(result3.need_llm)}")


# ====== 跨表批量路由测试 ======

print("\n=== 3. route_across_tables 跨表路由 ===")

table_results = [
    {"page": 1, "anomalies": [
        {"type": "anchor_shift", "severity": "low", "confidence": 0.95},
        {"type": "header_text_missing", "severity": "high", "confidence": 0.5},
    ]},
    {"page": 2, "anomalies": [
        {"type": "weak_anchor", "severity": "medium", "confidence": 0.6},
    ]},
    {"page": 3, "anomalies": [
        {"type": "truncated_header_merged", "severity": "medium", "confidence": 0.45},
        {"type": "multi_table_merged", "severity": "high", "confidence": 0.35},
        {"type": "anchor_shift", "severity": "low", "confidence": 0.85},
    ]},
]

batch = Step4LlmRouter.route_across_tables(table_results)
check("跨表: total_tables=3", batch["total_tables"] == 3)
check("跨表: total_anomalies=6", batch["total_anomalies"] == 6)
check("跨表: llm_count=3", batch["llm_count"] == 3,
      f"got {batch['llm_count']} (3 semantic, 3 positional skipped)")

# ====== 汇总 ======
print(f"\n{'='*40}")
print(f"Results: {passed} PASS, {failed} FAIL")
if failed == 0:
    print("All passed!")
else:
    print(f"FAILED: {failed} tests")
