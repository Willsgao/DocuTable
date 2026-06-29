# -*- coding: utf-8 -*-
"""
段切优化验证脚本

用法:
    python _test_segmentation.py <pdf_path> [--page N]

功能:
    1. 提取指定PDF的表格和段落数据
    2. 自动记录优化前后对比数据到 files/optimization_logs/
    3. 生成可读的对比报告

输出目录:
    files/optimization_logs/
    ├── v1_before/<pdf_name>/page_XXX.json    # 优化前（密度法一刀切）
    ├── v2_after/<pdf_name>/page_XXX.json     # 优化后（ContentSegmenter）
    └── diff_reports/<pdf_name>/page_XXX_diff.json  # 逐页对比
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_test(pdf_path: str, target_page: int = None):
    """运行段切验证测试。

    Args:
        pdf_path: PDF 文件路径
        target_page: 可选，仅测试指定页（1-based）
    """
    print("=" * 60)
    print("DocuTable 内容分割优化 — 验证测试")
    print("=" * 60)
    print(f"PDF: {pdf_path}")
    pdf_stem = Path(pdf_path).stem
    print(f"PDF 名称: {pdf_stem}")
    print()

    # ---- 方式1: 通过 liteparse 通道测试 ----
    print("-" * 40)
    print("方式1: liteparse 通道测试")
    print("-" * 40)

    try:
        from codes.liteparse_extractor import LiteParseParser
        from codes.liteparse_extractor.region_detector import RegionDetector
        from codes.content_segmenter.segment_logger import SegmentLogger

        parser = LiteParseParser()
        detector = RegionDetector(enable_segmentation=True)

        # 解析 PDF
        parse_result = parser.parse(pdf_path)
        print(f"解析完成: {parse_result.total_pages} 页, 耗时 {parse_result.parse_time_sec:.1f}s")

        # 检测表格区域（带段切）
        pages_with_changes = 0
        total_paragraphs = 0

        for page in parse_result.pages:
            if target_page and page.page_number != target_page:
                continue

            # 记录优化前状态
            before_log = detector._build_before_log(page)

            # 执行带段切的检测
            detector.detect(page)

            # 记录优化后状态
            after_log = detector._build_after_log(page)

            # 对比
            before_count = before_log.get('region_count', 0)
            after_table_count = len(page.table_regions)
            after_paragraph_count = len(page.paragraph_regions)

            has_change = (after_paragraph_count > 0) or (after_table_count != before_count)
            if has_change:
                pages_with_changes += 1
            total_paragraphs += after_paragraph_count

            # 记录日志
            SegmentLogger.log_before(
                pdf_name=pdf_stem,
                page_number=page.page_number,
                merged_regions=before_log.get('regions', []),
                page_size={"width": page.page_width, "height": page.page_height},
                extra_info={"method": "density_only"},
            )

            SegmentLogger.log_after(
                pdf_name=pdf_stem,
                page_number=page.page_number,
                segment_result=None,  # 这里是 liteparse 通道，格式不同
                original_density_regions=before_log.get('regions', []),
                page_size={"width": page.page_width, "height": page.page_height},
                extra_info={
                    "table_count": after_table_count,
                    "paragraph_count": after_paragraph_count,
                },
            )

            if has_change:
                print(f"\n  第 {page.page_number} 页: ★ 有变化")
                print(f"    优化前: {before_count} 个区域（全部按表格处理）")
                print(f"    优化后: {after_table_count} 个表格 + {after_paragraph_count} 个段落")
                for pr in page.paragraph_regions:
                    preview = pr.text[:80].replace('\n', '↵')
                    print(f"    新段落: [{pr.y0:.0f}-{pr.y1:.0f}] \"{preview}...\"")
            else:
                dot = "." * 50
                print(f"\r  第 {page.page_number} 页: 无变化 {dot[:20]}", end="")

        print(f"\n\n总览: {pages_with_changes}/{parse_result.total_pages} 页有变化, {total_paragraphs} 个新段落")
        print()

    except Exception as e:
        print(f"liteparse 测试出错: {e}")
        import traceback
        traceback.print_exc()

    # ---- 方式2: 通过 pdf_extractor 通道测试 ----
    print("-" * 40)
    print("方式2: pdf_extractor 通道测试")
    print("-" * 40)

    try:
        import fitz
        from codes.pdf_extractor.processor import PDFProcessor

        processor = PDFProcessor()
        cfg = processor.V2_CONFIG

        doc = fitz.open(pdf_path)
        pdf_stem = Path(pdf_path).stem

        for page_num in range(doc.page_count):
            if target_page and (page_num + 1) != target_page:
                continue

            page = doc[page_num]
            page_rect = page.rect

            # 提取文字和绘图
            words = page.get_text("words")
            drawings = PDFProcessor._extract_drawings_from_page(page)

            if not words:
                continue

            # 金融关键词过滤
            full_text = " ".join(w["text"] for w in words)
            if not any(kw in full_text for kw in cfg["financial_keywords"]):
                continue

            # 密度法区域检测
            table_regions = processor._detect_table_region(
                drawings, page_rect.width, page_rect.height
            )
            if not table_regions:
                table_regions = processor._detect_table_region_by_text(
                    words, page_rect.width, page_rect.height
                )
            if not table_regions:
                continue

            print(f"\n  第 {page_num + 1} 页: 密度法检测到 {len(table_regions)} 个区域")

            # 对每个区域运行 ContentSegmenter
            from codes.content_segmenter.segmenter import ContentSegmenter
            from codes.content_segmenter.segment_logger import SegmentLogger

            segmenter = ContentSegmenter()

            for idx, region in enumerate(table_regions):
                rx0, ry0, rx1, ry1 = region
                region_words = [w for w in words
                                if rx0 <= w["x0"] <= rx1 and ry0 <= w["y0"] <= ry1]

                if len(region_words) < 3:
                    continue

                def _word_getter(w):
                    return (w["x0"], w["x1"], w["y0"], w["y1"], w["text"])

                seg_result = segmenter.segment_region(
                    text_items=region_words,
                    page_width=page_rect.width,
                    page_height=page_rect.height,
                    page_number=page_num + 1,
                    region_bbox=region,
                    item_getter=_word_getter,
                )

                # 记录对比日志
                diff = SegmentLogger.log_page_diff(
                    pdf_name=pdf_stem,
                    page_number=page_num + 1,
                    before_regions=[{
                        "bbox": [round(rx0, 2), round(ry0, 2), round(rx1, 2), round(ry1, 2)],
                        "item_count": len(region_words),
                        "text_preview": " ".join(w["text"] for w in region_words[:20]),
                    }],
                    after_segment=seg_result,
                    page_size={"width": round(page_rect.width, 2), "height": round(page_rect.height, 2)},
                )

                # 打印变化摘要
                summary = diff.get("summary", {})
                changes = summary.get("changes", [])
                if changes:
                    print(f"    区域{idx+1}: {'; '.join(changes)}")
                    for r in seg_result.paragraph_regions:
                        preview = r.text[:100].replace('\n', '↵')
                        print(f"      ├ 新段落 [{r.y0:.0f}-{r.y1:.0f}]: \"{preview}...\"")
                else:
                    print(f"    区域{idx+1}: 无变化")

        doc.close()
        print()

    except Exception as e:
        print(f"pdf_extractor 测试出错: {e}")
        import traceback
        traceback.print_exc()

    # ---- 生成总报告 ----
    print("-" * 40)
    print("数据对比日志")
    print("-" * 40)
    log_root = PROJECT_ROOT / "files" / "optimization_logs"
    print(f"日志目录: {log_root}")
    print(f"  v1_before (优化前): {(log_root / 'v1_before' / pdf_stem).exists() if pdf_stem else 'N/A'}")
    print(f"  v2_after  (优化后): {(log_root / 'v2_after' / pdf_stem).exists() if pdf_stem else 'N/A'}")
    print(f"  diff_reports (对比): {(log_root / 'diff_reports' / pdf_stem).exists() if pdf_stem else 'N/A'}")

    # 列出 diff 文件
    diff_dir = log_root / "diff_reports" / pdf_stem
    if diff_dir.exists():
        diff_files = sorted(diff_dir.glob("*.json"))
        print(f"\n  Diff 文件数: {len(diff_files)}")
        for df in diff_files:
            with open(df, "r", encoding="utf-8") as f:
                data = json.load(f)
            summary = data.get("summary", {})
            print(f"    {df.name}: "
                  f"前{summary.get('before_regions', '?')}个区域 → "
                  f"后{summary.get('after_total_regions', '?')}个区域 "
                  f"({summary.get('paragraphs_newly_found', 0)}个新段落)")

    print()
    print("=" * 60)
    print("验证完成！")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="DocuTable 段切优化验证脚本")
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument("--page", type=int, default=None, help="仅测试指定页 (1-based)")
    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"错误: 文件不存在: {args.pdf_path}")
        sys.exit(1)

    run_test(args.pdf_path, args.page)


if __name__ == "__main__":
    main()
