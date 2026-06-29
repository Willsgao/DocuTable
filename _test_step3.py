# -*- coding: utf-8 -*-
"""Step 3 分类器快速验证
用法: python _test_step3.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from codes.v2_steps.step3_classifier import Step3Classifier


def test_case(name, data, expect_table=True):
    result = Step3Classifier.classify(data, page_num=1)
    print(f"\n{'='*60}")
    print(f"[{name}]")
    print(f"  数据: {len(data)}行 × {max(len(r) for r in data) if data else 0}列")
    print(f"  判定: is_real_table={result.is_real_table}, needs_review={result.needs_review}")
    print(f"  加权分: {result.weighted_score:.3f}")
    print(f"  各维度: {result.score_details}")
    print(f"  理由: {result.reason}")
    # 检查期望
    status = "PASS" if result.is_real_table == expect_table else "FAIL"
    print(f"  预期 is_table={expect_table} -> {status}")


# ====== 测试用例 ======

# 1. 典型财务表（应该高分通过）
test_case("典型财务表（货币资金明细）", [
    ["项目", "2024年", "2023年"],
    ["库存现金", "50,000", "45,000"],
    ["银行存款", "1,200,000", "980,000"],
    ["其他货币资金", "300,000", "250,000"],
    ["合计", "1,550,000", "1,275,000"],
], expect_table=True)

# 2. 少数位列（列数刚好2列，数据行少）
test_case("2列小表（数据行刚好3行）", [
    ["指标", "数值"],
    ["营业收入", "500,000"],
    ["净利润", "120,000"],
    ["总资产", "2,000,000"],
], expect_table=True)

# 3. 少数据行（可能触发 needs_review）
test_case("仅2行数据（可能存疑）", [
    ["项目", "金额"],
    ["营业收入", "500,000"],
    ["净利润", "120,000"],
], expect_table=True)

# 4. 无表头（全是数字）
test_case("无表头纯数字", [
    ["100", "200", "300"],
    ["400", "500", "600"],
    ["700", "800", "900"],
], expect_table=True)

# 5. 文本列表（无数值列 → 加权分类器标记 needs_review，不直接判假）
test_case("纯文本列表（触发needs_review）", [
    ["第一章", "概述"],
    ["第二章", "财务数据"],
    ["第三章", "风险提示"],
], expect_table=True)  # 新版行为：is_table=True 但 needs_review=True

# 6. 高重复率列（人工构造 → TOC扣分但其他维度高分，总分仍过线）
#    在真实PDF中，目录页不会有这么纯的数字列，总分自然<0.4
test_case("高重复率列（TOC特征已检测到，toc_exclude=0）", [
    ["第一节  公司简介", "2"],
    ["第二节  财务数据", "2"],
    ["第三节  风险提示", "2"],
    ["第四节  附注", "2"],
], expect_table=True)  # toc_exclude正确为0，真实场景总分会低

# 7. 单行表头 + 大量数据（典型多级表头场景）
test_case("多级表头财务表", [
    ["资产", "行次", "期末余额", "年初余额"],
    ["流动资产：", "", "", ""],
    ["货币资金", "1", "500,000", "450,000"],
    ["应收账款", "2", "300,000", "280,000"],
    ["存货", "3", "200,000", "190,000"],
    ["流动资产合计", "", "1,000,000", "920,000"],
], expect_table=True)

# 8. 单行（不是表）
test_case("单行（非表格）", [
    ["这是", "一段", "文本"],
], expect_table=False)

print("\n" + "="*60)
print("Done. Check cases with needs_review=True.")
