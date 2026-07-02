import json
from pathlib import Path
from codes.table_validator.hybrid_segmenter import (
    detect_table_boundaries_from_liteparse,
    _capture_gap_text_items,
    _build_table_from_liteparse_fallback,
    _build_items,
)

lp = json.loads(Path(
    r"data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024"
    r"年度资本管理第三支柱信息披露报告/liteparse/pages.json"
).read_text(encoding="utf-8"))

bounds = detect_table_boundaries_from_liteparse(lp)
enriched, gaps = _capture_gap_text_items(bounds, lp)
b10 = [x for x in enriched if x["page"] == 10][0]
print("boundary y0-y1:", b10["y0"], b10["y1"])
print("pre_header items:", len(b10.get("_pre_header_items", [])))
print("gap_description:", (b10.get("_gap_description") or "")[:120])

pre = b10.get("_pre_header_items", [])
if pre:
    for it in sorted(pre, key=lambda x: x["y0"])[:8]:
        print(f"  pre y={it['y0']:.0f} | {it['text']!r}")

p10 = [p for p in lp["pages"] if p["page_number"] == 10][0]
items = _build_items(p10["text_items"], 10)
# items in region y 320-350
for it in sorted(items, key=lambda x: x["y0"]):
    if 310 <= it["y0"] <= 400:
        print(f"  item y={it['y0']:.0f} x={it['x0']:.0f} | {it['text']!r}")

t = _build_table_from_liteparse_fallback(b10, lp)
print("table rows:", len(t["data"]))
for i, r in enumerate(t["data"][:6]):
    print(f"  row{i}: {r}")
