# -*- coding: utf-8 -*-
"""Reproduce exact Step1ColumnSplit behavior for page 5"""
import sys, io, statistics
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
import fitz
from codes.v2_steps.step1_column_split import Step1ColumnSplit
from codes.v2_steps.config import V2Config
from codes.content_segmenter.segmenter import ContentSegmenter

pdf_path = 'data/input_pdfs/test_subset8.pdf'
doc = fitz.open(pdf_path)
page = doc[4]  # page 5 (0-indexed)
page_rect = page.rect
page_width = page_rect.width
page_height = page_rect.height

# Extract blocks, lines, spans -> words (exact same code as Pipeline)
blocks = page.get_text("dict", flags=0)["blocks"]
words = []
for b in blocks:
    for line in b.get("lines", []):
        for span in line.get("spans", []):
            text = span["text"].strip()
            if not text:
                continue
            words.append({
                "x0": span["bbox"][0], "y0": span["bbox"][1],
                "x1": span["bbox"][2], "y1": span["bbox"][3],
                "text": text,
            })

# Extract drawings (same as Pipeline)
drawings = []
for d in page.get_drawings():
    drawings.append({
        "type": "rect" if d["fill"] is not None else "line",
        "x0": d["rect"][0], "y0": d["rect"][1],
        "x1": d["rect"][2], "y1": d["rect"][3],
        "direction": "v" if abs(d["rect"][2] - d["rect"][0]) < abs(d["rect"][3] - d["rect"][1]) else "h",
    })

print(f"Words: {len(words)}, Drawings: {len(drawings)}")

# Step 1: Detect table region
config = V2Config.STEP1_DEFAULTS
table_regions = Step1ColumnSplit._detect_table_region(drawings, page_width, page_height, config)
print(f"Drawing-based regions: {table_regions}")
if not table_regions:
    table_regions = Step1ColumnSplit._detect_table_region_by_text(words, page_width, page_height, config)
    print(f"Text-based regions: {table_regions}")

# Step 2: ContentSegmenter
segmenter = ContentSegmenter()
def _word_getter(w):
    return (w["x0"], w["x1"], w["y0"], w["y1"], w["text"])

for ri, region in enumerate(table_regions):
    rx0, ry0, rx1, ry1 = region
    print(f"\n=== Region {ri}: ({rx0:.0f},{ry0:.0f})-({rx1:.0f},{ry1:.0f}) ===")
    
    region_words = [w for w in words
                    if rx0 <= w["x0"] <= rx1 and ry0 <= w["y0"] <= ry1]
    print(f"  Words in region: {len(region_words)}")
    
    seg_result = segmenter.segment_region(
        text_items=region_words, page_width=page_width, page_height=page_height,
        page_number=5, region_bbox=region, item_getter=_word_getter,
    )
    
    for sr in seg_result.regions:
        srx0, sry0, srx1, sry1 = sr.x0, sr.y0, sr.x1, sr.y1
        diag = sr.diagnosis
        ri_list = diag.get("row_indices", [])
        print(f"\n  Sub: {sr.region_type} rows={diag.get('row_count',0)} T={diag.get('table_rows',0)} P={diag.get('paragraph_rows',0)}")
        print(f"  row_indices: {ri_list}")
        
        if sr.is_paragraph:
            continue
        
        # Simulate Step1ColumnSplit processing
        sub_region_words = [w for w in words
                            if rx0 <= w["x0"] <= rx1 and sry0 <= w["y0"] <= sry1]
        print(f"  sub_region_words: {len(sub_region_words)}")
        
        if len(sub_region_words) < 3:
            print(f"  SKIP: < 3 words")
            continue
        
        # Row detection
        row_bounds = Step1ColumnSplit._detect_horizontal_lines(
            page_width, sub_region_words, drawings, config)
        print(f"  row_bounds: {len(row_bounds)}")
        if len(row_bounds) < 2:
            print(f"  SKIP: < 2 row_bounds")
            continue
        
        # Column detection
        col_bounds = Step1ColumnSplit._detect_vertical_lines(
            page_width, sub_region_words, drawings, config)
        print(f"  col_bounds: {len(col_bounds)} -> {col_bounds}")
        
        if len(col_bounds) < 3:
            print(f"  SKIP: < 3 col_bounds")
            continue
        
        # Show threshold for debugging
        threshold = Step1ColumnSplit._compute_dynamic_y_threshold(sub_region_words, config)
        print(f"  dynamic_y_threshold: {threshold:.2f}")
        
        # Show row grouping
        groups = Step1ColumnSplit._group_words_into_rows(sub_region_words, threshold)
        print(f"  _group_words_into_rows: {len(groups)} groups")
        for gi, g in enumerate(groups[:5]):
            texts = [w["text"] for w in g[:4]]
            print(f"    group[{gi}]: {len(g)} words, texts={texts}")
        if len(groups) > 5:
            print(f"    ... ({len(groups)} total)")
        
        # Grid filling
        table_data = Step1ColumnSplit._assign_words_to_grid(
            sub_region_words, row_bounds, col_bounds, config)
        print(f"  table_data: {len(table_data)} rows x {len(table_data[0]) if table_data else 0} cols")
        for ri, row in enumerate(table_data[:5]):
            print(f"    R{ri}: {row[:4]}...")
