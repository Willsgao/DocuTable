"""快速验证 _refine_lower_boundary 对 page_005 的效果"""
import json
from codes.table_validator.liteparse_table_segmenter import segment_tables_from_liteparse, extract_paragraphs_from_liteparse

with open('data/mid_cache/test_subset8/liteparse/pages.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# page 5 = index 4
page5 = data['pages'][4]
print(f'Page 5: {len(page5.get("text_items", []))} text_items, {len(page5.get("table_regions", []))} regions')
for r in page5.get('table_regions', []):
    print(f'  Region bbox: ({r.get("x0")},{r.get("y0")})-({r.get("x1")},{r.get("y1")}) conf={r.get("confidence")}')

# Run segmenter
tables, meta = segment_tables_from_liteparse(data)
print(f'\n=== Tables found: {len(tables)} ===')
for t in tables:
    items = t.get("text_items", [])
    rows = t.get("rows", [])
    print(f'  Table p={t["page"]} rows={t["row_count"]} y=({t["y0"]:.0f},{t["y1"]:.0f}) items={len(items)}')
    # Print first row and last row to verify completeness
    if rows:
        last_row = rows[-1]
        texts = [it.get("text","") for it in last_row.get("items", last_row.get("texts", []))]
        print(f'    Last row texts: {texts[:80]}')

# Extract paragraphs
paras = extract_paragraphs_from_liteparse(data, tables)
table_paras = [p for p in paras if p['page'] == 5]
print(f'\n=== Paragraphs on page 5: {len(table_paras)} ===')
for p in table_paras:
    text = p['data'][:100].replace('\n', ' | ')
    print(f'  "{text}"')
