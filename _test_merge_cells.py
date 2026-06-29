# -*- coding: utf-8 -*-
"""测试合并单元格视觉恢复 — Step 2 优化前后对比"""
import sys
import io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import os
import json
from codes.pdf_extractor.processor import PDFProcessor
from codes.pdf_extractor.utils import load_config


def print_table(name, table_data, max_rows=25):
    """格式化打印表格"""
    print(f"\n{'─'*80}")
    print(f"  {name} ({len(table_data)} rows × {max(len(r) for r in table_data) if table_data else 0} cols)")
    print(f"{'─'*80}")
    if not table_data:
        print("  (空表格)")
        return

    for i, row in enumerate(table_data[:max_rows]):
        # 截断长文本
        display = [str(c)[:30] if c else "∅" for c in row]
        print(f"  [{i:2d}] {display}")

    if len(table_data) > max_rows:
        print(f"  ... 省略 {len(table_data) - max_rows} 行")


def print_merge_info(merge_info):
    """打印合并单元格信息"""
    if not merge_info:
        print("  → 未检测到合并单元格")
        return

    print(f"\n  🔗 检测到 {len(merge_info)} 个合并单元格:")
    for (row, col), info in sorted(merge_info.items()):
        rs = info['rowspan']
        cs = info['colspan']
        conf = info['confidence']
        tag = "🎯线条" if conf >= 0.8 else ("📝文本" if conf >= 0.6 else "🔍推测")
        print(f"     [{row},{col}] rowspan={rs} colspan={cs} 置信度={conf:.0%} {tag}")


def count_significant_diffs(before, after):
    """统计优化前后的显著差异"""
    if not before or not after:
        return 0, []

    n_before_rows = len(before)
    n_after_rows = len(after)
    n_before_cols = max(len(r) for r in before) if before else 0
    n_after_cols = max(len(r) for r in after) if after else 0

    diffs = []
    # 比较前几行（表头区域）
    check_rows = min(n_before_rows, n_after_rows, 8)
    for r in range(check_rows):
        b_row = before[r] if r < n_before_rows else []
        a_row = after[r] if r < n_after_rows else []
        max_c = max(len(b_row), len(a_row))
        for c in range(max_c):
            b_val = str(b_row[c]).strip() if c < len(b_row) else ""
            a_val = str(a_row[c]).strip() if c < len(a_row) else ""
            if b_val != a_val:
                diffs.append((r, c, b_val, a_val))

    return len(diffs), diffs


def main():
    print("=" * 80)
    print("  DocuTable V2 Step 2 — 合并单元格视觉恢复 对比测试")
    print("=" * 80)

    # 查找测试 PDF
    test_pdfs = [
        "data/input_pdfs/test_subset8.pdf",
        "data/input_pdfs/test_subset7.pdf",
        "data/input_pdfs/test_subset6.pdf",
    ]
    pdf_path = None
    for p in test_pdfs:
        full = os.path.join(".", p)
        if os.path.exists(full):
            pdf_path = full
            print(f"\n📄 使用测试文件: {p}")
            break

    if not pdf_path:
        print("\n❌ 未找到测试 PDF 文件，请将 PDF 放入 data/input_pdfs/ 目录")
        return

    # 创建 processor
    processor = PDFProcessor()
    # 强制使用 v2 模式
    processor.config["extraction_version"] = "v2"

    # ---- 方案A：使用合并单元格检测（新方案） ----
    print("\n" + "=" * 80)
    print("  【方案A】启用合并单元格视觉恢复（新方案）")
    print("=" * 80)

    ctx = None
    try:
        from codes.pdf_extractor.pdf_context import PDFContext
        ctx = PDFContext(pdf_path)
    except Exception:
        import fitz
        ctx = type('obj', (object,), {'doc': fitz.open(pdf_path), 'close': lambda: None})()

    results_a = processor._extract_text_tables_v2(
        pdf_path=pdf_path, max_pages=8, context=ctx,
        progress_callback=None, progress_base=0, skip_drawings=False,
    )

    tables_a = [r for r in results_a if r.get("type") == "table"]
    print(f"\n📊 方案A 提取到 {len(tables_a)} 个表格")

    total_merge_spans_a = 0
    total_cells_merged_a = 0
    tables_with_merge = 0

    for t in tables_a:
        ms = t.get("merge_stats", {})
        mi = t.get("merge_info", {})
        if ms.get("total_spans", 0) > 0:
            tables_with_merge += 1
            total_merge_spans_a += ms["total_spans"]
            total_cells_merged_a += ms.get("cells_merged", 0)

    print(f"  → 含合并单元格的表格: {tables_with_merge}/{len(tables_a)}")
    print(f"  → 总合并 span 数: {total_merge_spans_a}")
    print(f"  → 总合并 cell 数: {total_cells_merged_a}")

    # ---- 方案B：不使用合并单元格检测（旧方案模拟） ----
    print("\n" + "=" * 80)
    print("  【方案B】不使用合并单元格检测（旧方案）")
    print("=" * 80)

    # 重新打开 PDF
    try:
        from codes.pdf_extractor.pdf_context import PDFContext
        ctx_b = PDFContext(pdf_path)
    except Exception:
        import fitz
        ctx_b = type('obj', (object,), {'doc': fitz.open(pdf_path), 'close': lambda: None})()

    results_b = processor._extract_text_tables_v2(
        pdf_path=pdf_path, max_pages=8, context=ctx_b,
        progress_callback=None, progress_base=0, skip_drawings=True,  # skip_drawings → 无线条检测
    )

    tables_b = [r for r in results_b if r.get("type") == "table"]
    print(f"\n📊 方案B 提取到 {len(tables_b)} 个表格")

    # ---- 逐表对比 ----
    print("\n" + "=" * 80)
    print("  📋 逐表对比详情")
    print("=" * 80)

    total_diffs = 0
    tables_with_diffs = 0

    for i in range(max(len(tables_a), len(tables_b))):
        ta = tables_a[i] if i < len(tables_a) else None
        tb = tables_b[i] if i < len(tables_b) else None

        if ta is None or tb is None:
            continue

        data_a = ta.get("data", [])
        data_b = tb.get("data", [])
        merge_info = ta.get("merge_info", {})
        merge_stats = ta.get("merge_stats", {})

        if merge_stats.get("total_spans", 0) == 0 and not merge_info:
            continue  # 无合并单元格，跳过详细对比

        page = ta.get("page", i + 1)
        print(f"\n{'█'*80}")
        print(f"  Page {page} — 检测到 {merge_stats.get('total_spans', 0)} 个合并单元格")
        print(f"{'█'*80}")

        # 打印合并信息
        print_merge_info(merge_info)

        # 对比前/后
        before = ta.get("table_data_before_merge", data_b)
        ndiffs, diffs = count_significant_diffs(before, data_a)
        if ndiffs > 0:
            total_diffs += ndiffs
            tables_with_diffs += 1
            print(f"\n  📝 优化前后差异 ({ndiffs} 处):")
            for r, c, b_val, a_val in diffs[:10]:  # 最多显示 10 处
                b_show = b_val[:25] if b_val else "(空)"
                a_show = a_val[:25] if a_val else "(空)"
                if b_val != a_val:
                    print(f"     [{r},{c}] 旧={b_show} → 新={a_show}")

        # 打印优化后的表格（前 10 行）
        print_table("优化后（含合并标记）", data_a, max_rows=10)

    # ---- 总结 ----
    print("\n" + "=" * 80)
    print("  📊 测试总结")
    print("=" * 80)
    print(f"  方案A（启用合并检测）: {len(tables_a)} 个表格, {tables_with_merge} 个含合并单元格")
    print(f"    → 检测到 {total_merge_spans_a} 个 merge span, 合并 {total_cells_merged_a} 个 cell")
    print(f"  方案B（禁用合并检测）: {len(tables_b)} 个表格")
    print(f"  有显著差异的表格: {tables_with_diffs}, 总差异 cell 数: {total_diffs}")

    if total_merge_spans_a > 0:
        print(f"\n  ✅ 合并单元格视觉恢复已生效")
        # 保存优化前后的对比数据
        save_comparison(tables_a, tables_b)
    else:
        print(f"\n  ⚠️ 未检测到合并单元格，可能是该 PDF 中没有有线合并表格")
        print(f"     尝试另一个包含丰富表格线的 PDF 文件")

    # 清理
    if hasattr(ctx, 'close'):
        ctx.close()
    if hasattr(ctx_b, 'close'):
        ctx_b.close()


def save_comparison(tables_a, tables_b):
    """保存对比数据到 JSON，供人工检查"""
    import json
    output = []
    for i, ta in enumerate(tables_a):
        mi = ta.get("merge_info", {})
        ms = ta.get("merge_stats", {})
        if not mi:
            continue
        tb = tables_b[i] if i < len(tables_b) else None
        output.append({
            "page": ta.get("page"),
            "merge_stats": {k: v for k, v in ms.items() if not k.startswith("_")},
            "merge_info": {
                f"({k[0]},{k[1]})": v for k, v in mi.items()
            },
            "table_before": ta.get("table_data_before_merge", [])[:5],
            "table_after": ta.get("data", [])[:5],
        })

    out_path = "files/merge_cell_test_result.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  💾 对比数据已保存到: {out_path}")


if __name__ == "__main__":
    main()
