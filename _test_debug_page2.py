"""
调试 page_002：追踪表格列错乱原因
"""
import sys, os, copy, json, re
sys.path.insert(0, os.path.dirname(__file__))

from codes.liteparse_extractor.cache_manager import load_parse_result

# ===== 1. 加载 liteparse 数据 =====
pdf_path = r"F:/wills/my_softwares/DocuTable/data/input_pdfs/test_subset8.pdf"
lp_result = load_parse_result(pdf_path)
if lp_result is None:
    print("FAIL: 无法加载 liteparse 数据")
    sys.exit(1)
liteparse_data = lp_result.to_dict()

# ===== 2. 提取 pdf2docx 表格（用 PDFContext 限制页数） =====
from codes.pdf_extractor.processor import PDFProcessor, PDFContext
processor = PDFProcessor()
context = PDFContext(pdf_path)

# 提取表格（逐页docx方式，通过限制context只用page 2）
docx_tables = processor._extract_tables_via_docx_per_page(
    pdf_path=pdf_path,
    context=context,
)

page2_tables = [t for t in docx_tables if t.get("page") == 2]
print(f"Page 2 有 {len(page2_tables)} 个 pdf2docx 表格\n")

for i, dt in enumerate(page2_tables):
    data = dt.get("data", [])
    ncols = max((len(r) for r in data), default=0)
    print(f"=== Docx Table {i}: {len(data)} rows x {ncols} cols (type={dt.get('type')}) ===")
    for ri, row in enumerate(data):
        cells = [str(c)[:25] if c else "" for c in row]
        print(f"  R{ri:02d}({len(row)}): {cells}")
        if ri >= 15:
            print(f"  ... ({len(data)} rows total)")
            break
    print()

# ===== 3. 模拟 compact_empty_cells =====
print("=" * 60)
print("模拟 _compact_empty_cells 的影响")
print("=" * 60)
from codes.pdf_extractor.processor import _compact_empty_cells

for i, dt in enumerate(page2_tables):
    data = dt.get("data", [])
    if not data:
        continue
    print(f"\nDocx Table {i}:")
    d, compacted = _compact_empty_cells(copy.deepcopy(data))
    if compacted:
        print(f"  [CHANGED] Max cols: {max(len(r) for r in data)} -> {max(len(r) for r in d)}")
        print(f"  After compact:")
        for ri, row in enumerate(d):
            cells = [str(c)[:25] if c else "" for c in row]
            print(f"  R{ri:02d}({len(row)}): {cells}")
            if ri >= 10:
                print(f"  ... ({len(d)} rows)")
                break
    else:
        print(f"  [NO CHANGE]")

# ===== 4. 模拟 auto_clean_tables 完整流程 =====
print("\n" + "=" * 60)
print("模拟 _auto_clean_tables 完整流程")
print("=" * 60)

cleaned = copy.deepcopy(page2_tables)
from codes.pdf_extractor.processor import _auto_clean_tables
# _auto_clean_tables expects results with specific fields
# Actually let me just import the key functions and apply them

c = copy.deepcopy(page2_tables)
for i, dt in enumerate(c):
    data = dt.get("data", [])
    if not data:
        continue
    
    print(f"\nTable {i}: BEFORE cleaning - {len(data)} rows, {max(len(r) for r in data) if data else 0} cols")
    # First 3 rows
    for ri in range(min(3, len(data))):
        cells = [str(c)[:20] if c else "" for c in data[ri]]
        print(f"  R{ri:02d}({len(data[ri])}): {cells}")
    
    # Apply cleaning steps
    from codes.pdf_extractor.processor import (
        _clean_data_cells, _remove_spaces_data, _remove_empty_rows,
        _compact_empty_cells, _normalize_table_columns,
    )
    _clean_data_cells(data)
    cleaned_d = _remove_spaces_data(data)
    dt["data"] = cleaned_d
    _remove_empty_rows(dt["data"])
    dt["data"], compacted = _compact_empty_cells(dt["data"])
    print(f"  compact_empty_cells: changed={compacted}")
    dt["data"] = _normalize_table_columns(dt["data"])
    
    print(f"  AFTER cleaning - {len(dt['data'])} rows, {max(len(r) for r in dt['data']) if dt['data'] else 0} cols")
    for ri in range(min(5, len(dt["data"]))):
        cells = [str(c)[:20] if c else "" for c in dt["data"][ri]]
        print(f"  R{ri:02d}({len(dt['data'][ri])}): {cells}")
    if len(dt["data"]) > 5:
        print(f"  ... ({len(dt['data'])} rows total)")

# ===== 4.5. 检查边界检测细节 =====
print("\n" + "=" * 60)
print("边界检测细节")
print("=" * 60)

from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _estimate_docx_tables_y_ranges,
)

# 检查 page 2 的 liteparse regions
pages = liteparse_data.get("pages", [])
for lp_page in pages:
    pn = lp_page.get("page_number", 0)
    if pn != 2:
        continue
    regions = lp_page.get("table_regions", [])
    text_items = lp_page.get("text_items", [])
    print(f"\nPage {pn}: {len(regions)} table_regions, {len(text_items)} text_items")
    
    for i, r in enumerate(regions):
        ctx = r.get("context_text", "")[:80]
        print(f"  Region[{i}]: y={r.get('y0')}-{r.get('y1')} x={r.get('x0')}-{r.get('x1')} conf={r.get('confidence')} ctx=\"{ctx}\"")
    
    # 找到段落文本
    print(f"\n  Text items in gap area (y=430-470):")
    for it in sorted(text_items, key=lambda x: x.get('y0', 0)):
        y0 = it.get('y0', 0)
        y1 = it.get('y1', 0)
        if 420 < y0 < 480 or 420 < y1 < 480:
            text = it.get('text', '')[:100]
            print(f"    y={y0}-{y1} type={it.get('type')} text=\"{text}\"")

# 边界检测
boundaries = detect_table_boundaries_from_liteparse(liteparse_data)
print(f"\n检测到 {len(boundaries)} 个边界:")
for b in boundaries:
    if b.get("page") == 2:
        cap = b.get("caption", "")[:80]
        print(f"  P{b['page']}: y={b['y0']:.1f}-{b['y1']:.1f} caption=\"{cap}\" src_regions={b.get('source_regions')}")

# Y 范围估算
print(f"\nDocx Table Y 范围估算:")
yranges = _estimate_docx_tables_y_ranges(c, liteparse_data)
for i, dt in enumerate(c):
    yr = yranges.get(id(dt))
    print(f"  Docx Table {i}: Y={yr} page={dt.get('page')} rows={len(dt.get('data',[]))}")

# ===== 5. Hybrid Segmenter =====
print("\n" + "=" * 60)
print("Hybrid Segmenter 输出")
print("=" * 60)

from codes.table_validator.hybrid_segmenter import hybrid_segment_tables

# Use the original (not cleaned) docx tables for hybrid, like the real pipeline does
# Actually in the real pipeline, _auto_clean_tables runs BEFORE hybrid_segment_tables
# And hybrid receives the CLEANED results
seg_tables, seg_report = hybrid_segment_tables(
    liteparse_data,
    docx_tables=c,  # Use cleaned tables
    enable_cross_page=True,
)

print(f"\nHybrid 输出: {len(seg_tables)} 个表格, method={seg_report.get('method')}")
print(f"Fusion stats: {seg_report.get('fusion_stats')}")
for i, t in enumerate(seg_tables):
    data = t.get("data", [])
    cap = t.get("caption", "")[:100]
    print(f"\n  Table {i}: P{t['page']} y0={t.get('y0')} y1={t.get('y1')} cap={cap}")
    maxc = max(len(r) for r in data) if data else 0
    is_split = t.get('is_split_from_mixed', False)
    print(f"  {len(data)} rows x {maxc} cols is_split={is_split}")
    for ri, row in enumerate(data):
        cells = [str(c)[:25] if c else "" for c in row]
        print(f"  R{ri:02d}({len(row)}): {cells}")
        if ri >= 15:
            print(f"  ... ({len(data)} rows)")
            break
