# -*- coding: utf-8 -*-
"""Step 6 TextItem 格式统一验证
用法: python _test_step6.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from codes.v2_steps.models import TextItem
from codes.v2_steps.step6_textitem_format import (
    Step6TextItemFormat, CHANNEL_CONFIDENCE,
)

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        failed += 1


# ====== 1. 模型字段完整性 ======

print("=== 1. TextItem 模型字段 ===")

ti = TextItem(text="测试", x0=10, y0=20, x1=100, y1=30, page=1)
check("text 字段", ti.text == "测试")
check("bbox 字段", ti.x0 == 10 and ti.y0 == 20 and ti.x1 == 100 and ti.y1 == 30)
check("page 字段", ti.page == 1)
check("source 默认值", ti.source == "")
check("confidence 默认值", ti.confidence == 0.0)
check("font_size 默认值", ti.font_size == 0.0)
check("is_bold 默认值", ti.is_bold == False)
check("block_type 字段存在", hasattr(ti, "block_type"))


# ====== 2. from_dict / from_dicts ======

print("\n=== 2. from_dict 转换 ===")

raw = {"text": "货币资金", "x0": 50, "y0": 100, "x1": 120, "y1": 112, "source": "pymupdf"}
item = Step6TextItemFormat.from_dict(raw, source="pymupdf", page_num=3)
check("from_dict text", item.text == "货币资金")
check("from_dict page", item.page == 3)
check("from_dict source", item.source == "pymupdf")
check("from_dict confidence", item.confidence == 0.95)

# 批量
raws = [raw, {"text": "500,000", "x0": 200, "y0": 100, "x1": 260, "y1": 112}]
items = Step6TextItemFormat.from_dicts(raws, source="pymupdf", page_num=1)
check("from_dicts count", len(items) == 2)
check("from_dicts confidence", all(t.confidence == 0.95 for t in items))


# ====== 3. 通道置信度 ======

print("\n=== 3. 通道置信度基线 ===")

check("pymupdf=0.95", CHANNEL_CONFIDENCE["pymupdf"] == 0.95)
check("pdfplumber=0.85", CHANNEL_CONFIDENCE["pdfplumber"] == 0.85)
check("liteparse=0.90", CHANNEL_CONFIDENCE["liteparse"] == 0.90)
check("paddleocr=0.75", CHANNEL_CONFIDENCE["paddleocr"] == 0.75)
check("unknown=0.50", CHANNEL_CONFIDENCE["unknown"] == 0.50)


# ====== 4. pdfplumber 通道转换 ======

print("\n=== 4. pdfplumber 转换（top/bottom→y0/y1）===")

pp_words = [
    {"text": "营业收入", "x0": 50, "top": 200, "x1": 120, "bottom": 212},
    {"text": "净利润", "x0": 50, "top": 220, "x1": 110, "bottom": 232},
]
pp_items = Step6TextItemFormat.from_pdfplumber_words(pp_words, page_num=1)
check("pdfplumber count", len(pp_items) == 2)
check("pdfplumber y0 mapping", pp_items[0].y0 == 200 and pp_items[0].y1 == 212)
check("pdfplumber source", all(t.source == "pdfplumber" for t in pp_items))
check("pdfplumber confidence", all(t.confidence == 0.85 for t in pp_items))


# ====== 5. from_pymupdf_words ======

print("\n=== 5. PyMuPDF 通道 ===")

mu_words = [
    {"text": "资产", "x0": 10, "y0": 50, "x1": 50, "y1": 62, "source": "pymupdf"},
    {"text": "负债", "x0": 10, "y0": 70, "x1": 50, "y1": 82, "source": "pymupdf"},
]
mu_items = Step6TextItemFormat.from_pymupdf_words(mu_words, page_num=2)
check("pymupdf count", len(mu_items) == 2)
check("pymupdf page", all(t.page == 2 for t in mu_items))
check("pymupdf confidence", all(t.confidence == 0.95 for t in mu_items))


# ====== 6. summarize 统计 ======

print("\n=== 6. summarize 统计 ===")

mixed = [
    TextItem(text="A", x0=0, y0=0, x1=10, y1=10, page=1, source="pymupdf",
             confidence=0.95, font_size=10, is_bold=True),
    TextItem(text="B", x0=0, y0=12, x1=10, y1=22, page=1, source="pymupdf",
             confidence=0.95, font_size=10, is_bold=False),
    TextItem(text="C", x0=0, y0=24, x1=10, y1=34, page=1, source="pdfplumber",
             confidence=0.85, font_size=0, is_bold=False),
]
stats = Step6TextItemFormat.summarize(mixed)
check("summarize count=3", stats["count"] == 3)
check("summarize sources", stats["sources"] == {"pymupdf": 2, "pdfplumber": 1})
check("summarize with_font=2", stats["with_font_size"] == 2)
check("summarize with_bold=1", stats["with_bold"] == 1)

# 空列表
empty_stats = Step6TextItemFormat.summarize([])
check("空列表 summarize", empty_stats == {"count": 0})


# ====== 汇总 ======
print(f"\n{'='*40}")
print(f"Results: {passed} PASS, {failed} FAIL")
if failed == 0:
    print("All passed!")
else:
    print(f"FAILED: {failed} tests")
