# -*- coding: utf-8 -*-
"""测试表格→文本去重：_deduplicate_text_against_tables

核心原则：同样的数据只能出现一次，如果数据被判为表格数据，
那么就不应该出现在文本数据中。

覆盖场景：
1. 段落 Y 区间 100% 被表格覆盖 → 移除
2. 段落 Y 区间 50% 被表格覆盖（低于阈值） → 保留
3. 段落内容 token >60% 出现在表格中 → 移除
4. 注解 Y 区间被表格覆盖 → 移除
5. 段落在表格之间（无重叠） → 保留
6. 段落完全在表格外 → 保留
7. 多表+多段的混合作业
8. 段落无 content → 保留
9. 表格数据为空 → 段落全部保留
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from codes.pdf_extractor.processor import _deduplicate_text_against_tables

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  FAILED: {detail}")


# ============================================================
# Case 1: 段落 Y 区间 100% 被表格覆盖 → 移除
# ============================================================
print("\n--- Case 1: 空间完全重叠 ---")
results = [
    {
        "page": 1, "type": "table", "data": [
            ["项目", "金额"],
            ["收入", "100"],
            ["成本", "80"],
        ],
        "y0": 100, "y1": 400, "x0": 50, "x1": 500,
        "bbox": [50, 100, 500, 400],
    },
    {
        "page": 1, "type": "paragraph",
        "data": "收入100成本80",
        "bbox": [50, 150, 500, 200],
        "extractor": "liteparse_hybrid",
    },
]
_filtered = _deduplicate_text_against_tables(results)
check("1a 段落被移除", len(_filtered) == 1)
check("1b 只剩表格", _filtered[0]["type"] == "table")


# ============================================================
# Case 2: 段落 Y 区间 50% 被覆盖（低于 70% 阈值） → 保留（除非内容重叠）
# ============================================================
print("\n--- Case 2: 空间部分重叠（低于阈值） ---")
results = [
    {
        "page": 1, "type": "table", "data": [
            ["项目", "金额"],
            ["收入", "100"],
        ],
        "y0": 100, "y1": 250, "x0": 50, "x1": 500,
        "bbox": [50, 100, 500, 250],
    },
    {
        "page": 1, "type": "paragraph",
        "data": "以下为本集团主要的财务指标分析内容，涵盖收入以及成本等各方面。",
        "bbox": [50, 200, 500, 350],
        "extractor": "liteparse_hybrid",
    },
]
_filtered = _deduplicate_text_against_tables(results)
check("2a 段落保留（低空间重叠+低内容重叠）", len(_filtered) == 2)
has_para = any(r["type"] == "paragraph" for r in _filtered)
check("2b 段落还在", has_para)


# ============================================================
# Case 3: 段落内容 token >60% 出现在表格中 → 移除
# ============================================================
print("\n--- Case 3: 内容高度重叠 ---")
results = [
    {
        "page": 1, "type": "table", "data": [
            ["项目", "2024年", "2023年"],
            ["营业收入", "1,200", "1,000"],
            ["营业成本", "800", "700"],
            ["净利润", "300", "250"],
        ],
        "y0": 100, "y1": 500, "x0": 50, "x1": 500,
        "bbox": [50, 100, 500, 500],
    },
    {
        "page": 1, "type": "paragraph",
        "data": "营业收入1,200万元，营业成本800万元，净利润300万元",
        "bbox": [50, 500, 500, 550],  # Y 不重叠，但内容高度重叠
        "extractor": "liteparse_hybrid",
    },
]
_filtered = _deduplicate_text_against_tables(results)
check("3a 段落被内容重叠移除", len(_filtered) == 1)
check("3b 只剩表格", _filtered[0]["type"] == "table")


# ============================================================
# Case 4: 注解被表格覆盖 → 移除
# ============================================================
print("\n--- Case 4: 注解空间重叠 ---")
results = [
    {
        "page": 1, "type": "table", "data": [
            ["资产负债率", "45%"],
            ["流动比率", "1.2"],
        ],
        "y0": 200, "y1": 400, "x0": 50, "x1": 500,
        "bbox": [50, 200, 500, 400],
    },
    {
        "page": 1, "type": "annotation",
        "data": "注：以上数据未经审计。",
        "bbox": [50, 250, 500, 270],
        "source": "table_annotation",
    },
]
_filtered = _deduplicate_text_against_tables(results)
check("4a 注解被移除", len(_filtered) == 1)
check("4b 只剩表格", _filtered[0]["type"] == "table")


# ============================================================
# Case 5: 段落在表格之间（无重叠） → 保留
# ============================================================
print("\n--- Case 5: 表格间段落 ---")
results = [
    {
        "page": 1, "type": "table", "data": [
            ["收入", "100"],
            ["成本", "80"],
        ],
        "y0": 100, "y1": 250, "x0": 50, "x1": 500,
        "bbox": [50, 100, 500, 250],
    },
    {
        "page": 1, "type": "paragraph",
        "data": "以下为资产负债表数据，反映了集团的财务状况。",
        "bbox": [50, 280, 500, 320],
        "extractor": "liteparse_hybrid",
    },
    {
        "page": 1, "type": "table", "data": [
            ["资产", "负债"],
            ["500", "200"],
        ],
        "y0": 350, "y1": 500, "x0": 50, "x1": 500,
        "bbox": [50, 350, 500, 500],
    },
]
_filtered = _deduplicate_text_against_tables(results)
check("5a 保留所有3个条目", len(_filtered) == 3)
types = [r["type"] for r in _filtered]
check("5b 类型顺序正确", types == ["table", "paragraph", "table"])


# ============================================================
# Case 6: 段落完全在表格外 → 保留
# ============================================================
print("\n--- Case 6: 表格外段落 ---")
results = [
    {
        "page": 1, "type": "table", "data": [
            ["项目", "金额"],
            ["合计", "500"],
        ],
        "y0": 300, "y1": 500, "x0": 50, "x1": 500,
        "bbox": [50, 300, 500, 500],
    },
    {
        "page": 1, "type": "paragraph",
        "data": "这是一段位于页面顶部的独立文本，与表格无关。",
        "bbox": [50, 50, 500, 120],
        "extractor": "liteparse_hybrid",
    },
    {
        "page": 1, "type": "paragraph",
        "data": "另一段独立文本。",
        "bbox": [50, 550, 500, 580],
        "extractor": "liteparse_hybrid",
    },
]
_filtered = _deduplicate_text_against_tables(results)
check("6a 保留所有3个条目", len(_filtered) == 3)
check("6b 两个段落都保留", sum(1 for r in _filtered if r["type"] == "paragraph") == 2)


# ============================================================
# Case 7: 多表+多段混合作业
# ============================================================
print("\n--- Case 7: 混合场景 ---")
results = [
    {"page": 1, "type": "paragraph", "data": "题头：本集团财务报告", "bbox": [50, 10, 500, 40]},
    {"page": 1, "type": "table", "data": [["收入", "100"], ["成本", "80"]],
     "y0": 60, "y1": 200, "x0": 50, "x1": 500, "bbox": [50, 60, 500, 200]},
    # 这个段落在表格内部，应移除
    {"page": 1, "type": "paragraph", "data": "收入100成本80合计20", "bbox": [50, 100, 500, 130]},
    # 这个段落在表间，应保留
    {"page": 1, "type": "paragraph", "data": "以下为资产负债表", "bbox": [50, 220, 500, 250]},
    {"page": 1, "type": "table", "data": [["资产", "500"], ["负债", "200"]],
     "y0": 270, "y1": 400, "x0": 50, "x1": 500, "bbox": [50, 270, 500, 400]},
    # 注解在表格内部
    {"page": 1, "type": "annotation", "data": "注：数据已审计", "bbox": [50, 300, 500, 320]},
    # 尾部独立段落
    {"page": 1, "type": "paragraph", "data": "独立结尾文本", "bbox": [50, 420, 500, 450]},
]
_filtered = _deduplicate_text_against_tables(results)
check("7a 保留5个条目（移除2个重叠的）", len(_filtered) == 5)
types = [r["type"] for r in _filtered]
check("7b 类型顺序", types == ["paragraph", "table", "paragraph", "table", "paragraph"])

# 验证被移除的是第3和第6项
data_list = [r.get("data", "")[:20] for r in _filtered]
check("7c 题头保留", "题头" in data_list[0])
check("7d 表间段落保留", "资产负债表" in data_list[2])
check("7e 尾部段落保留", "独立结尾" in data_list[4])


# ============================================================
# Case 8: 段落无 content → 保留（不处理）
# ============================================================
print("\n--- Case 8: 空段落 ---")
results = [
    {"page": 1, "type": "table", "data": [["A", "B"]],
     "y0": 100, "y1": 200, "bbox": [50, 100, 500, 200]},
    {"page": 1, "type": "paragraph", "data": "", "bbox": [50, 100, 500, 150]},
]
_filtered = _deduplicate_text_against_tables(results)
check("8a 空段落保留", len(_filtered) == 2)


# ============================================================
# Case 9: 表格无数据 → 段落全部保留
# ============================================================
print("\n--- Case 9: 表格无数据 ---")
results = [
    {"page": 1, "type": "table", "data": [], "y0": 100, "y1": 250, "bbox": [50, 100, 500, 250]},
    {"page": 1, "type": "paragraph", "data": "一段独立的文本", "bbox": [50, 120, 500, 150]},
]
_filtered = _deduplicate_text_against_tables(results)
check("9a 段落保留", len(_filtered) == 2)
check("9b 两个条目都保留", all(r in _filtered for r in results))


# ============================================================
print(f"\n{'='*50}")
print(f"PASS: {PASS}  FAIL: {FAIL}  TOTAL: {PASS + FAIL}")
if FAIL == 0:
    print("ALL TESTS PASSED!")
else:
    print(f"SOME TESTS FAILED ({FAIL} failures)")
