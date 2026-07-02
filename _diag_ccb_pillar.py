"""诊断建设银行第三支柱报告全页拆分情况。"""
import json
from pathlib import Path
from collections import defaultdict

from codes.table_validator.hybrid_segmenter import hybrid_segment_tables
from codes.table_validator.table_content_splitter import is_pillar_disclosure_table_body

CACHE = Path(
    r"data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024"
    r"年度资本管理第三支柱信息披露报告/liteparse"
)
pages_json = CACHE / "pages.json"
data = json.loads(pages_json.read_text(encoding="utf-8"))
lp = data if "pages" in data else {"pages": data.get("pages", [])}
if isinstance(data, dict) and "pages" in data:
    liteparse_data = data
else:
    liteparse_data = json.loads(pages_json.read_text(encoding="utf-8"))

# pages.json is ParseResult.to_dict format
if "pages" not in liteparse_data:
    liteparse_data = json.loads(pages_json.read_text(encoding="utf-8"))

seg, report = hybrid_segment_tables(liteparse_data, docx_tables=[], enable_cross_page=False)

by_page = defaultdict(list)
for e in seg:
    by_page[int(e.get("page", 0))].append(e)

print(f"总页数(liteparse): {len(liteparse_data.get('pages', []))}")
print(f"拆分后条目: {len(seg)}  table={sum(1 for e in seg if e.get('type')=='table')}  "
      f"text={sum(1 for e in seg if e.get('type') in ('text','paragraph'))}")
print()
print(f"{'P':>3} {'reg':>3} {'tbl':>3} {'txt':>3} {'pillar':>6}  问题信号")
print("-" * 70)

for lp_page in sorted(liteparse_data.get("pages", []), key=lambda p: p["page_number"]):
    pn = lp_page["page_number"]
    nreg = len(lp_page.get("table_regions", []))
    entries = by_page.get(pn, [])
    nt = sum(1 for e in entries if e.get("type") == "table")
    nx = sum(1 for e in entries if e.get("type") in ("text", "paragraph"))
    flags = []
    if nreg == 1 and nt >= 3:
        flags.append(f"1region→{nt}tables")
    if nreg >= 1 and nt == 0 and nx == 0:
        flags.append("有region无输出")
    if nt >= 2 and nx >= 3:
        flags.append("过度碎片化")
    for e in entries:
        if e.get("type") == "table":
            d = e.get("data", [])
            if d and is_pillar_disclosure_table_body(d):
                flags.append("pillar表")
                break
            ss = e.get("segment_source", "")
            if ss == "table_content_split":
                flags.append("表文拆分")
                break
    if e.get("_split_from_merged") or any(
        x.get("_split_from_merged") for x in entries if x.get("type") == "table"
    ):
        flags.append("结构分裂")
    if lp_page.get("is_table_page") and not entries:
        flags.append("整页丢失")
    print(f"{pn:3d} {nreg:3d} {nt:3d} {nx:3d} {'Y' if 'pillar表' in ' '.join(flags) else 'N':>6}  {', '.join(flags) or 'ok'}")
