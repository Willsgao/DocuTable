# -*- coding: utf-8 -*-
"""Step 8 质量评估验证
用法: python _test_step8.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from codes.v2_steps.step8_quality_eval import (
    Step8QualityEval, QualityReport, _score_structure,
    _score_content, _score_financial, _score_source,
    _score_to_grade,
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


# ====== 1. 结构完整性 ======

print("=== 1. 结构完整性 ===")

good_table = [
    ["项目", "2024年", "2023年"],
    ["货币资金", "500,000", "450,000"],
    ["应收账款", "300,000", "280,000"],
    ["存货", "200,000", "190,000"],
    ["流动资产合计", "1,000,000", "920,000"],
    ["营业收入", "2,500,000", "2,300,000"],
]
score, details, issues = _score_structure(good_table)
check("好表格结构分 >0.7", score > 0.7, f"got {score:.2f}")
check("好表格无 issues", len(issues) == 0, f"got {issues}")

# 劣质表
bad_table = [
    ["项目", ""],
    ["货币资金"],
    ["", "500"],
]
score_bad, _, issues_bad = _score_structure(bad_table)
check("劣质表结构分 <0.6", score_bad < 0.6, f"got {score_bad:.2f}")
check("劣质表有 issues", len(issues_bad) > 0)


# ====== 2. 内容一致性 ======

print("\n=== 2. 内容一致性 ===")

score_c, details_c, issues_c = _score_content(good_table)
check("内容一致性 >0.6", score_c > 0.6, f"got {score_c:.2f}")

# 全文本表
text_table = [
    ["章节", "内容"],
    ["一", "概述内容"],
    ["二", "财务说明"],
    ["三", "风险提示"],
]
score_t, _, _ = _score_content(text_table)
check("纯文本表也可评分", score_t >= 0)


# ====== 3. 财务特征 ======

print("\n=== 3. 财务特征 ===")

score_f, details_f, issues_f = _score_financial(good_table)
check("财务特征 >0.5", score_f > 0.5, f"got {score_f:.2f}")
check("命中模式", len(details_f["matched_patterns"]) > 0)

# 非财报
non_fin = [
    ["姓名", "年龄", "性别"],
    ["张三", "25", "男"],
    ["李四", "30", "女"],
]
score_nf, _, issues_nf = _score_financial(non_fin)
check("非财报特征 <0.3", score_nf < 0.3, f"got {score_nf:.2f}")
check("无匹配模式", len(issues_nf) > 0)


# ====== 4. 提取源可信度 ======

print("\n=== 4. 提取源可信度 ===")

score_s, details_s, _ = _score_source(good_table, source_channel="pymupdf")
check("pymupdf=0.95", abs(score_s - 0.95) < 0.01, f"got {score_s:.3f}")

score_s2, _, _ = _score_source(good_table, source_channel="unknown")
check("unknown=0.50", abs(score_s2 - 0.50) < 0.01, f"got {score_s2:.3f}")


# ====== 5. 综合评估 ======

print("\n=== 5. 综合评估 ===")

report = Step8QualityEval.evaluate(good_table, page_num=1, source_channel="pymupdf")
check("综合分 >0.7", report.overall_score > 0.7, f"got {report.overall_score:.2f}")
check("评级 A/B", report.grade in ("A", "B"), f"got {report.grade}")
check("is_reliable=True", report.is_reliable)
check("needs_review=False", not report.needs_review)

report2 = Step8QualityEval.evaluate(non_fin, page_num=2, source_channel="unknown")
# 非财报结构/内容可以好，但财务特征应很低
check("非财报财务特征 <0.3", report2.financial_score < 0.3,
      f"got {report2.financial_score:.2f}")
check("非财报有 issues（财务）", len(report2.issues) > 0)


# ====== 6. 评级映射 ======

print("\n=== 6. 评级映射 ===")

check("0.95→A", _score_to_grade(0.95) == "A")
check("0.80→B", _score_to_grade(0.80) == "B")
check("0.65→C", _score_to_grade(0.65) == "C")
check("0.50→D", _score_to_grade(0.50) == "D")
check("0.30→E", _score_to_grade(0.30) == "E")

# ====== 7. to_dict ======

print("\n=== 7. to_dict ===")

d = report.to_dict()
check("to_dict grade", d["grade"] == report.grade)
check("to_dict 有 issues", "issues" in d)
check("to_dict 有 suggestions", "suggestions" in d)


# ====== 汇总 ======
print(f"\n{'='*40}")
print(f"Results: {passed} PASS, {failed} FAIL")
if failed == 0:
    print("All passed!")
else:
    print(f"FAILED: {failed} tests")
