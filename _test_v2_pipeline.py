# -*- coding: utf-8 -*-
"""
V2 模块化架构验证脚本

验证:
1. 所有 v2_steps 模块能否正确导入
2. Pipeline 骨架结构是否完整
3. [可选] 与 legacy 代码对比结果一致性

用法:
    python _test_v2_pipeline.py                             # 仅验证模块导入
    python _test_v2_pipeline.py <pdf_path>                  # 完整对比测试
    python _test_v2_pipeline.py <pdf_path> --pages 5        # 限制页数
"""

import os
import sys
import time
from pathlib import Path

# 修复 Windows GBK 编码问题
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ================================================================
# Test 1: 模块导入验证
# ================================================================
def test_imports():
    """验证所有 v2_steps 模块可正确导入"""
    print("=" * 60)
    print("Test 1: 模块导入验证")
    print("=" * 60)

    modules_to_check = [
        ("codes.v2_steps", "主模块"),
        ("codes.v2_steps.models", "数据模型"),
        ("codes.v2_steps.config", "配置系统"),
        ("codes.v2_steps.pipeline", "Pipeline 编排器"),
        ("codes.v2_steps.step1_column_split", "Step1 列切分"),
        ("codes.v2_steps.step2_merge_detect", "Step2 合并检测"),
        ("codes.v2_steps.step3_classifier", "Step3 分类器"),
        ("codes.v2_steps.step4_llm_router", "Step4 LLM路由"),
        ("codes.v2_steps.step5_triple_channel", "Step5 三通道"),
        ("codes.v2_steps.step6_textitem_format", "Step6 TextItem"),
        ("codes.v2_steps.step7_header_tree", "Step7 表头树"),
        ("codes.v2_steps.step8_quality_eval", "Step8 质量评估"),
    ]

    all_ok = True
    for module_name, label in modules_to_check:
        try:
            __import__(module_name)
            print(f"  ✅ {label}: {module_name}")
        except Exception as e:
            print(f"  ❌ {label}: {module_name} → {e}")
            all_ok = False

    return all_ok


# ================================================================
# Test 2: 数据结构验证
# ================================================================
def test_models():
    """验证 PipelineContext / GridResult 等数据类可实例化"""
    print("\n" + "=" * 60)
    print("Test 2: 数据模型验证")
    print("=" * 60)

    from codes.v2_steps.models import (
        PipelineContext, GridResult, MergeSpan,
        ClassifyResult, TextItem,
    )

    # PipelineContext
    ctx = PipelineContext(
        pdf_path="/test/to.pdf",
        page_num=1,
    )
    assert ctx.page_num == 1
    assert ctx.words == []
    print("  ✅ PipelineContext 实例化")

    # GridResult
    grid = GridResult(
        table_data=[["A", "B"], ["1", "2"]],
        row_bounds=[(0, 10), (10, 20)],
        col_bounds=[0, 50, 100],
        confidence=0.9,
    )
    assert grid.confidence == 0.9
    print("  ✅ GridResult 实例化")

    # MergeSpan
    span = MergeSpan(row=0, col=0, rowspan=2, colspan=1, confidence=0.8, source="lines")
    assert span.rowspan == 2
    print("  ✅ MergeSpan 实例化")

    # ClassifyResult
    cr = ClassifyResult(is_table=True, confidence=0.85, needs_review=False)
    assert cr.is_table
    print("  ✅ ClassifyResult 实例化")

    # TextItem
    ti = TextItem(text="测试", x0=10, y0=20, x1=50, y1=30, page=1, source="pymupdf")
    assert ti.text == "测试"
    print("  ✅ TextItem 实例化")

    return True


# ================================================================
# Test 3: Pipeline 结构验证
# ================================================================
def test_pipeline_structure():
    """验证 Pipeline 开关和配置功能"""
    print("\n" + "=" * 60)
    print("Test 3: Pipeline 结构验证")
    print("=" * 60)

    from codes.v2_steps.pipeline import V2Pipeline
    from codes.v2_steps.config import V2Config

    pipeline = V2Pipeline()

    # 默认状态
    assert pipeline.is_enabled("step1") == True
    assert pipeline.is_enabled("step2") == True
    assert pipeline.is_enabled("step3") == False  # 待迁移
    print("  ✅ 默认启用状态正确")

    # 开关操作
    pipeline.disable("step2")
    assert pipeline.is_enabled("step2") == False
    pipeline.enable("step2")
    assert pipeline.is_enabled("step2") == True
    print("  ✅ enable/disable 操作正确")

    # 配置系统
    cfg = V2Config()
    step1_cfg = cfg.get("step1")
    assert "y_threshold_factor" in step1_cfg
    assert "align_tolerance" in step1_cfg
    print("  ✅ 配置获取正确")

    # Step1 默认配置
    defaults = V2Config.STEP1_DEFAULTS
    assert "financial_keywords" in defaults
    print("  ✅ Step1 默认配置完整")

    return True


# ================================================================
# Test 4: Step1 单元方法验证
# ================================================================
def test_step1_methods():
    """验证 Step1 核心方法可独立调用"""
    print("\n" + "=" * 60)
    print("Test 4: Step1 核心方法验证")
    print("=" * 60)

    from codes.v2_steps.step1_column_split import Step1ColumnSplit

    # 测试 _merge_consecutive
    result = Step1ColumnSplit._merge_consecutive([1, 2, 3, 5, 6, 8])
    assert result == [(1, 3), (5, 6), (8, 8)], f"Expected [(1,3),(5,6),(8,8)], got {result}"
    print("  ✅ _merge_consecutive 正确")

    # 测试 _merge_nearby_lines
    result = Step1ColumnSplit._merge_nearby_lines([100, 101, 200, 201, 300], 2.0)
    assert len(result) <= 3, f"Expected ≤3 merged lines, got {len(result)}"
    print("  ✅ _merge_nearby_lines 正确")

    # 测试 _cluster_1d
    clusters = Step1ColumnSplit._cluster_1d(
        [10.0, 10.5, 11.0, 20.0, 20.5, 21.0, 20.3],
        tolerance=2.0
    )
    assert len(clusters) >= 2, f"Expected ≥2 clusters, got {len(clusters)}"
    print("  ✅ _cluster_1d 正确")

    # 测试 _normalize_table_columns
    data = [["A", "B"], ["1"]]
    normalized = Step1ColumnSplit._normalize_table_columns(data)
    assert len(normalized[1]) == 2, f"Expected 2 cols, got {len(normalized[1])}"
    print("  ✅ _normalize_table_columns 正确")

    # 测试 _is_numeric (内嵌在 _compute_table_confidence)
    table = [["项目", "金额"], ["收入", "100.5"], ["支出", "-20.3"]]
    conf = Step1ColumnSplit._compute_table_confidence(
        table, False, [], {"confidence_col_weight": 0.35,
                            "confidence_empty_weight": 0.25,
                            "confidence_num_weight": 0.25,
                            "confidence_line_bonus": 0.15})
    assert 0 <= conf <= 1.0, f"Confidence out of range: {conf}"
    print(f"  ✅ _compute_table_confidence = {conf:.2f}")

    return True


# ================================================================
# Test 5: Step2 单元方法验证
# ================================================================
def test_step2_methods():
    """验证 Step2 核心方法可独立调用"""
    print("\n" + "=" * 60)
    print("Test 5: Step2 合并检测验证")
    print("=" * 60)

    from codes.v2_steps.step2_merge_detect import Step2MergeDetect

    # 测试 _safe_cell
    data = [["A", "B"], ["1", ""]]
    assert Step2MergeDetect._safe_cell(data, 0, 0) == "A"
    assert Step2MergeDetect._safe_cell(data, 1, 1) == ""
    assert Step2MergeDetect._safe_cell(data, 5, 5) == ""  # OOB
    print("  ✅ _safe_cell 正确")

    # 测试 _group_consecutive
    result = Step2MergeDetect._group_consecutive([1, 2, 3, 5, 7, 8])
    assert result == [(1, 3), (5, 5), (7, 8)], f"Got {result}"
    print("  ✅ _group_consecutive 正确")

    # 测试 _merge_overlapping_spans
    spans = [(0, 0, 2, 1, 0.8), (0, 0, 1, 2, 0.6)]
    merged = Step2MergeDetect._merge_overlapping_spans(spans, 3, 3)
    assert len(merged) <= 2
    print(f"  ✅ _merge_overlapping_spans: {len(spans)}→{len(merged)}")

    # 测试 _detect_from_text 空输入
    empty = Step2MergeDetect._detect_from_text([])
    assert empty == []
    print("  ✅ _detect_from_text 空输入安全")

    # 测试 _detect_from_text 有数据
    table = [["总计", "", ""], ["总计", "100", "200"]]
    text_spans = Step2MergeDetect._detect_from_text(table)
    print(f"  ✅ _detect_from_text 正常: {len(text_spans)} spans")

    return True


# ================================================================
# Test 6: 与 Legacy 代码对比验证（需要 PDF）
# ================================================================
def test_compare_with_legacy(pdf_path, max_pages=None):
    """对比新模块化 Pipeline 与原始 legacy 代码的输出一致性"""
    print("\n" + "=" * 60)
    print("Test 6: 输出一致性对比测试")
    print("=" * 60)

    from codes.pdf_extractor.processor import PDFProcessor

    processor = PDFProcessor()

    print(f"\n📄 测试文件: {pdf_path}")
    print(f"📄 最大页数: {max_pages or '全部'}\n")

    # ---- 运行 modular pipeline ----
    print("▶ 运行模块化 V2Pipeline...")
    t0 = time.time()
    try:
        results_modular = processor._extract_text_tables_v2(
            pdf_path=pdf_path, max_pages=max_pages,
            context=None, progress_callback=None,
            progress_base=0, skip_drawings=False,
        )
        t1 = time.time()
        print(f"  ✅ 模块化完成: {t1 - t0:.1f}s, {len(results_modular)} 个结果")
    except Exception as e:
        print(f"  ❌ 模块化失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # ---- 运行 legacy 代码 ----
    print("▶ 运行原始 Legacy 代码...")
    t0 = time.time()
    try:
        results_legacy = processor._extract_text_tables_v2_legacy(
            pdf_path=pdf_path, max_pages=max_pages,
            context=None, progress_callback=None,
            progress_base=0, skip_drawings=False,
        )
        t1 = time.time()
        print(f"  ✅ Legacy完成: {t1 - t0:.1f}s, {len(results_legacy)} 个结果")
    except Exception as e:
        print(f"  ❌ Legacy失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # ---- 对比分析 ----
    print("\n" + "-" * 40)
    print("对比分析:")
    print("-" * 40)

    # 1. 结果数量对比
    print(f"\n📊 数量对比:")
    mod_tables = [r for r in results_modular if r.get("type") == "table"]
    mod_paras = [r for r in results_modular if r.get("type") == "paragraph"]
    leg_tables = [r for r in results_legacy if r.get("type") == "table"]
    leg_paras = [r for r in results_legacy if r.get("type") == "paragraph"]

    print(f"  模块化: {len(mod_tables)} 表格 + {len(mod_paras)} 段落")
    print(f"  Legacy: {len(leg_tables)} 表格 + {len(leg_paras)} 段落")

    # 2. 逐页对比
    all_pages = sorted(set(
        [r["page"] for r in results_modular] +
        [r["page"] for r in results_legacy]
    ))

    issues = []
    for page in all_pages:
        mod_p = [r for r in results_modular if r["page"] == page]
        leg_p = [r for r in results_legacy if r["page"] == page]

        mod_t = [r for r in mod_p if r["type"] == "table"]
        leg_t = [r for r in leg_p if r["type"] == "table"]

        if len(mod_t) != len(leg_t):
            issues.append(f"Page {page}: 表格数不一致 modular={len(mod_t)} vs legacy={len(leg_t)}")

        # 对比表格内容
        for i in range(min(len(mod_t), len(leg_t))):
            mt = mod_t[i]
            lt = leg_t[i]

            if mt.get("rows") != lt.get("rows") or mt.get("cols") != lt.get("cols"):
                issues.append(
                    f"Page {page} Table {i}: "
                    f"尺寸不一致 modular={mt.get('rows')}x{mt.get('cols')} "
                    f"vs legacy={lt.get('rows')}x{lt.get('cols')}"
                )

    if issues:
        print(f"\n⚠️ 发现 {len(issues)} 个差异:")
        for issue in issues[:10]:
            print(f"  - {issue}")
        if len(issues) > 10:
            print(f"  ... 共 {len(issues)} 个差异")
        return False
    else:
        print("\n✅ 输出完全一致！模块化迁移成功。")
        return True


# ================================================================
# Main
# ================================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  DocuTable V2 — 模块化 Pipeline 验证")
    print("=" * 60)

    # ---- 基础测试（不需要 PDF）----
    results = []
    results.append(("模块导入", test_imports()))
    results.append(("数据模型", test_models()))
    results.append(("Pipeline结构", test_pipeline_structure()))
    results.append(("Step1单元方法", test_step1_methods()))
    results.append(("Step2单元方法", test_step2_methods()))

    # ---- 摘要 ----
    print("\n" + "=" * 60)
    print(" 基础测试结果汇总")
    print("=" * 60)
    all_pass = True
    for name, ok in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        if not ok:
            all_pass = False
        print(f"  {status}: {name}")

    # ---- 对比测试（如有 PDF）----
    pdf_path = None
    max_pages = None

    # 从命令行参数获取
    if len(sys.argv) >= 2:
        pdf_path = sys.argv[1]
        if len(sys.argv) >= 3 and sys.argv[2].startswith("--pages"):
            try:
                max_pages = int(sys.argv[3]) if len(sys.argv) >= 4 else None
            except (ValueError, IndexError):
                pass

    if not pdf_path:
        # 尝试常见路径
        test_pdfs = [
            "data/input_pdfs/test_subset8.pdf",
            "data/input_pdfs/test_subset7.pdf",
            "data/input_pdfs/test_subset6.pdf",
            "files/test.pdf",
        ]
        for p in test_pdfs:
            full = os.path.join(".", p)
            if os.path.exists(full):
                pdf_path = full
                print(f"\n📄 自动发现测试文件: {p}")
                break

    if pdf_path:
        compare_ok = test_compare_with_legacy(pdf_path, max_pages)
        all_pass = all_pass and compare_ok
    else:
        print("\n⚠️ 未找到测试 PDF（基础测试已通过）")
        print("  用法: python _test_v2_pipeline.py <pdf_path> [--pages N]")

    if all_pass:
        print("\n🎉 所有测试通过！")
        sys.exit(0)
    else:
        print("\n❌ 有测试失败，请检查。")
        sys.exit(1)
