# -*- coding: utf-8 -*-
"""V2 模块化快速验证（仅基础测试，无需PDF）"""
import os, sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

passed = 0
failed = 0

def check(name, result):
    global passed, failed
    if result:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}")
        failed += 1

print("=" * 60)
print("DocuTable V2 模块化快速验证")
print("=" * 60)

# Test 1: 全部模块可导入
print("\n>>> Test 1: 模块导入")
for mod, label in [
    ("codes.v2_steps", "主模块"),
    ("codes.v2_steps.models", "数据模型"),
    ("codes.v2_steps.config", "配置系统"),
    ("codes.v2_steps.pipeline", "Pipeline"),
    ("codes.v2_steps.step1_column_split", "Step1 列切分"),
    ("codes.v2_steps.step2_merge_detect", "Step2 合并检测"),
    ("codes.v2_steps.step3_classifier", "Step3 分类器"),
    ("codes.v2_steps.step4_llm_router", "Step4 LLM"),
    ("codes.v2_steps.step5_triple_channel", "Step5 三通道"),
    ("codes.v2_steps.step6_textitem_format", "Step6 TextItem"),
    ("codes.v2_steps.step7_header_tree", "Step7 表头树"),
    ("codes.v2_steps.step8_quality_eval", "Step8 质量评估"),
]:
    try:
        __import__(mod)
        check(label, True)
    except Exception as e:
        check(label, False)
        print(f"       Error: {e}")

# Test 2: 数据模型实例化
print("\n>>> Test 2: 数据模型")
from codes.v2_steps.models import (
    PipelineContext, GridResult, MergeSpan, ClassifyResult, TextItem
)
ctx = PipelineContext(pdf_path="/test.pdf", page_num=1)
check("PipelineContext 实例化", ctx.page_num == 1)

grid = GridResult(table_data=[["A","B"]], confidence=0.9)
check("GridResult 实例化", grid.confidence == 0.9)

span = MergeSpan(row=0, col=0, rowspan=2, colspan=1, confidence=0.8, source="lines")
check("MergeSpan 实例化", span.rowspan == 2)

cr = ClassifyResult(is_table=True, confidence=0.85)
check("ClassifyResult 实例化", cr.is_table)

ti = TextItem(text="test", x0=10, y0=20, x1=50, y1=30)
check("TextItem 实例化", ti.text == "test")

# Test 3: Pipeline 结构
print("\n>>> Test 3: Pipeline 结构")
from codes.v2_steps.pipeline import V2Pipeline
from codes.v2_steps.config import V2Config
pipeline = V2Pipeline()
check("Step1 默认启用", pipeline.is_enabled("step1"))
check("Step2 默认启用", pipeline.is_enabled("step2"))
check("Step3 默认启用", pipeline.is_enabled("step3"))

pipeline.disable("step2")
check("disable/enable", not pipeline.is_enabled("step2"))
pipeline.enable("step2")
check("enable 恢复", pipeline.is_enabled("step2"))

cfg = V2Config()
s1 = cfg.get("step1")
check("Step1 配置获取", "y_threshold_factor" in s1)
check("Step1 全局默认", "align_tolerance" in V2Config.STEP1_DEFAULTS)

# Test 4: Step1 单元方法
print("\n>>> Test 4: Step1 单元方法")
from codes.v2_steps.step1_column_split import Step1ColumnSplit

r = Step1ColumnSplit._merge_consecutive([1, 2, 3, 5, 6, 8])
check("_merge_consecutive", r == [(1, 3), (5, 6), (8, 8)])

r = Step1ColumnSplit._merge_nearby_lines([100, 101, 200, 201, 300], 2.0)
check("_merge_nearby_lines", len(r) <= 3)

r = Step1ColumnSplit._cluster_1d([10, 10.5, 11, 20, 20.5, 21, 20.3], 2.0)
check("_cluster_1d", len(r) >= 2)

d = [["A", "B"], ["1"]]
n = Step1ColumnSplit._normalize_table_columns(d)
check("_normalize_table_columns", len(n[1]) == 2)

# Test 5: Step2 单元方法
print("\n>>> Test 5: Step2 单元方法")
from codes.v2_steps.step2_merge_detect import Step2MergeDetect

check("_safe_cell 正常", Step2MergeDetect._safe_cell([["A"]], 0, 0) == "A")
check("_safe_cell OOB", Step2MergeDetect._safe_cell([["A"]], 5, 5) == "")
check("_safe_cell 空", Step2MergeDetect._safe_cell([["A",""]], 0, 1) == "")

r = Step2MergeDetect._group_consecutive([1, 2, 3, 5, 7, 8])
check("_group_consecutive", r == [(1, 3), (5, 5), (7, 8)])

empty = Step2MergeDetect._detect_from_text([])
check("_detect_from_text 空", empty == [])

spans = Step2MergeDetect._merge_overlapping_spans([(0,0,2,1,0.8), (0,0,1,2,0.6)], 3, 3)
check("_merge_overlapping_spans", len(spans) <= 2)

# Test 6: processor.py 仍可调用 legacy 回退
print("\n>>> Test 6: Processor 兼容性")
try:
    from codes.pdf_extractor.processor import PDFProcessor
    p = PDFProcessor()
    check("PDFProcessor 实例化", hasattr(p, "V2_CONFIG"))
    check("_extract_text_tables_v2 存在", hasattr(p, "_extract_text_tables_v2"))
    check("_extract_text_tables_v2_legacy 存在", hasattr(p, "_extract_text_tables_v2_legacy"))
except Exception as e:
    check("PDFProcessor 导入", False)
    print(f"       Error: {e}")

# Summary
print("\n" + "=" * 60)
print(f"结果: {passed} PASS, {failed} FAIL")
print("=" * 60)
if failed == 0:
    print("PIPELINE MODULARIZATION COMPLETE - All tests passed!")
else:
    print("Some tests failed, please check.")

sys.exit(0 if failed == 0 else 1)
