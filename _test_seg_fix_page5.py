# -*- coding: utf-8 -*-
"""快速验证 ContentSegmenter 修复：page 5 是否不再丢失中间 7 行"""
import sys, io, os
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from codes.v2_steps.pipeline import V2Pipeline
from codes.content_segmenter.segmenter import ContentSegmenter
from collections import defaultdict

pdf_path = r"f:\wills\my_softwares\DocuTable\data\input_pdfs\test_subset8.pdf"

pipeline = V2Pipeline()
results = pipeline.run(pdf_path, max_pages=5)

# 按页分组显示
by_page = defaultdict(list)
for r in results:
    by_page[r.get("page", 0)].append(r)

print("=" * 60)
print("V2 Pipeline Results Summary")
print("=" * 60)
for pn in sorted(by_page):
    entries = by_page[pn]
    tables = [e for e in entries if e.get("type") == "table"]
    paragraphs = [e for e in entries if e.get("type") == "paragraph"]
    print(f"\n--- Page {pn} ---")
    print(f"  Tables: {len(tables)}, Paragraphs: {len(paragraphs)}")
    for i, t in enumerate(tables):
        data = t.get("data", [])
        rows = len(data)
        cols = len(data[0]) if data else 0
        caption = t.get("caption", "")[:60]
        print(f"  Table[{i}]: {rows} rows x {cols} cols  capt='{caption}'")
        if rows <= 12:
            for ri, row in enumerate(data[:12]):
                preview = str(row)[:80]
                print(f"    R{ri}: {preview}")
    for i, p in enumerate(paragraphs):
        text = p.get("data", p.get("text", ""))[:100]
        print(f"  Para[{i}]: '{text}'")
