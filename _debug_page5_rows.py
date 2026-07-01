# -*- coding: utf-8 -*-
"""Debug Step1ColumnSplit row detection on page 5"""
import sys, io, os
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
import fitz, statistics
from codes.v2_steps.step1_column_split import Step1ColumnSplit
from codes.v2_steps.config import V2Config
from codes.content_segmenter.segmenter import ContentSegmenter

pdf_path = 'data/input_pdfs/test_subset8.pdf'
doc = fitz.open(pdf_path)
page = doc[4]
page_rect = page.rect

blocks = page.get_text('dict', flags=fitz.TEXT_PRESERVE_WHITESPACE)['blocks']
words = []
for b in blocks:
    for line in b.get('lines', []):
        for span in line.get('spans', []):
            words.append({
                'x0': span['bbox'][0], 'y0': span['bbox'][1],
                'x1': span['bbox'][2], 'y1': span['bbox'][3],
                'text': span['text']
            })

def _word_getter(w):
    return (w['x0'], w['x1'], w['y0'], w['y1'], w['text'])

segmenter = ContentSegmenter()
region = (0, 0, page_rect.width, page_rect.height)
seg_result = segmenter.segment_region(
    text_items=words, page_width=page_rect.width, page_height=page_rect.height,
    page_number=5, region_bbox=region, item_getter=_word_getter,
)

config = V2Config.STEP1_DEFAULTS
print(f"Config y_threshold_factor={config['y_threshold_factor']}")
print(f"Config y_threshold_min={config['y_threshold_min']}")
print(f"Config y_threshold_max={config['y_threshold_max']}")
print()

for sr in seg_result.regions:
    if not hasattr(sr, 'is_table') or not sr.is_table:
        continue
    
    srx0, sry0, srx1, sry1 = sr.x0, sr.y0, sr.x1, sr.y1
    sub_region_words = [w for w in words
                        if 0 <= w['x0'] <= page_rect.width and sry0 <= w['y0'] <= sry1]
    
    print(f'=== Table y=({sry0:.1f}, {sry1:.1f}), words={len(sub_region_words)} ===')
    print(f'  row_indices={sr.diagnosis.get("row_indices", [])}')
    
    threshold = Step1ColumnSplit._compute_dynamic_y_threshold(sub_region_words, config)
    print(f'  dynamic Y threshold: {threshold:.2f}')
    
    rows = Step1ColumnSplit._group_words_into_rows(sub_region_words, threshold)
    print(f'  grouped rows: {len(rows)}')
    for ri, row_words in enumerate(rows):
        y0_range = (min(w['y0'] for w in row_words), max(w['y1'] for w in row_words))
        texts = [w['text'] for w in row_words]
        print(f'    R{ri}: y=({y0_range[0]:.1f}-{y0_range[1]:.1f}) items={len(row_words)} texts={texts}')
    
    y_positions = sorted(set(w['y0'] for w in sub_region_words if w['text'].strip()))
    print(f'  unique y0: {len(y_positions)}')
    if len(y_positions) >= 5:
        gaps = []
        for i in range(len(y_positions)-1):
            gap = y_positions[i+1] - y_positions[i]
            if 0.5 < gap < 50:
                gaps.append(gap)
        print(f'  valid gaps: {len(gaps)}')
        if gaps:
            print(f'  median gap: {statistics.median(gaps):.2f}')
            print(f'  min gap: {min(gaps):.2f}, max gap: {max(gaps):.2f}')
            # Show all gaps
            for i, g in enumerate(gaps):
                print(f'    gap[{i}]: {g:.2f}')
    print()
