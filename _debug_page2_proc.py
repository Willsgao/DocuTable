"""Simulate processor path: hybrid + _extract_paragraphs_for_hybrid + dedup."""
from codes.liteparse_extractor.parser import LiteParseParser
from codes.table_validator.hybrid_segmenter import hybrid_segment_tables
from codes.pdf_extractor.processor import _extract_paragraphs_for_hybrid
from codes.table_validator.cell_differ import _normalize_for_search
import re

parser = LiteParseParser()
lp = parser.parse("data/input_pdfs/test_subset8.pdf").to_dict()
seg_tables, _ = hybrid_segment_tables(lp, docx_tables=[])
lp_paras = _extract_paragraphs_for_hybrid(lp, seg_tables)

results = list(seg_tables)

def _blob(r):
    return str(r.get("context_text") or r.get("data") or r.get("text") or "").strip()

existing_keys = set()
for r in results:
    if r.get("type") not in ("text", "paragraph", "annotation"):
        continue
    b = _blob(r)
    if b:
        existing_keys.add((r.get("page", 0), _normalize_for_search(b[:300])))

merged = []
for para in lp_paras:
    b = _blob(para)
    if not b:
        continue
    key = (para.get("page", 0), _normalize_for_search(b[:300]))
    if key in existing_keys:
        continue
    norm_new = _normalize_for_search(b)
    parts = re.findall(r"[\u4e00-\u9fff]{2,}", norm_new)
    dup = False
    for r in results:
        if r.get("type") not in ("text", "paragraph", "annotation"):
            continue
        if r.get("page") != para.get("page"):
            continue
        norm_old = _normalize_for_search(_blob(r))
        if norm_new in norm_old or norm_old in norm_new:
            dup = True
            break
        if parts:
            hit = sum(1 for p in parts if p in norm_old)
            if hit >= 3 and hit / len(parts) >= 0.6:
                dup = True
                break
    if dup:
        continue
    existing_keys.add(key)
    merged.append(para)

results.extend(merged)
results.sort(key=lambda r: (r.get("page", 0), r.get("y0", 0)))

p2 = [e for e in results if e.get("page") == 2]
print(f"Page 2 total: {len(p2)}\n")

dup_table = 0
for i, e in enumerate(p2):
    typ = e.get("type", "table")
    src = e.get("segment_source", e.get("extractor", ""))
    y0 = e.get("y0", 0)
    if typ in ("text", "paragraph"):
        txt = _blob(e)
        has_iss = "建信理财" in txt and ("1,499,121" in txt or "期数" in txt)
        has_inv = "现金、存款" in txt or ("占比" in txt and "1,008,220" in txt)
        flag = ""
        if has_iss or has_inv:
            flag = " *** TABLE DUP ***"
            dup_table += 1
        print(f"[{i}] {typ} y={y0:.0f} src={src}{flag}")
        if flag:
            print(f"     {txt[:120]}")
    else:
        print(f"[{i}] TABLE y={y0:.0f} rows={len(e.get('data',[]))} src={src}")

print(f"\nTable-in-text duplicates: {dup_table}")
