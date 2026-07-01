"""验证 page_005 表格完整性（仅打印关键计数）"""
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')
from codes.table_validator.liteparse_table_segmenter import segment_tables_from_liteparse, extract_paragraphs_from_liteparse

with open('data/mid_cache/test_subset8/liteparse/pages.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 禁用详细日志
import logging
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

tables, meta = segment_tables_from_liteparse(data)
page5_tables = [t for t in tables if t.get('page') == 5]

print(f"Page 5 tables: {len(page5_tables)}")
for t in page5_tables:
    items = t.get("text_items", [])
    rows = t.get("rows", [])
    y_range = f"y=({t['y0']:.0f},{t['y1']:.0f})" if t['y0'] > 0 else ""
    print(f"  Table: rows={t['row_count']} items={len(items)} {y_range}")

paras = extract_paragraphs_from_liteparse(data, tables)
page5_paras = [p for p in paras if p['page'] == 5]
print(f"\nPage 5 paragraphs: {len(page5_paras)}")
for p in page5_paras:
    text = p['data'][:120].replace('\n', ' | ')
    print(f"  [{len(p['data'])} chars] {text}")
