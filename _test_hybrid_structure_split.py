# -*- coding: utf-8 -*-
"""
测试混合分割器的表结构差异检测

覆盖：
  1. 两张不同财务表（股东权益 vs 存款地域分布）→ 应拆分
  2. 同一表格被拆成两个 region（继续表）→ 应合并
  3. 上下文 unit 变化 → 应拆分
  4. 空 region / 无 token 退化 → 安全处理
  5. 行级结构分裂 — 融合表中检测到新表格结构 → 拆分为二
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from codes.table_validator.hybrid_segmenter import (
    _get_region_text_tokens,
    _have_different_table_structure,
    _merge_regions_by_proximity,
    _find_structure_break_in_data,
    _split_fused_table_by_structure,
)
from codes.table_validator.hybrid_segmenter import (
    _re_estimate_subtable_y,
)
from codes.table_validator.liteparse_table_segmenter import (
    _is_numeric_cell,
    _build_items,
)


def make_text_items(texts_with_y):
    """[(text, y_mid), ...] -> liteparse-style text_items."""
    items = []
    for text, y_mid in texts_with_y:
        items.append({
            "text": text,
            "y0": y_mid - 4,
            "y1": y_mid + 4,
            "y_mid": y_mid,
            "x0": 50,
            "x1": 200,
        })
    return items


def make_region(y0, y1, context_text=""):
    return {"y0": y0, "y1": y1, "context_text": context_text}


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def case0_token_extraction():
    """Basic token extraction"""
    items = make_text_items([
        ("（人民币百万元）", 100),
        ("2024年", 100),
        ("2023年", 100),
        ("股本", 116),
        ("250,011", 116),
        ("250,011", 116),
        ("其他权益工具", 132),
        ("159,977", 132),
        ("199,968", 132),
    ])
    region = make_region(95, 140)
    tokens = _get_region_text_tokens(items, region, n_rows=3)

    assert "人民币百万元" in tokens, f"tokens={tokens}"
    assert "年" in tokens
    assert "股本" in tokens
    assert "其他权益工具" in tokens
    # Pure numbers should be stripped
    for t in tokens:
        assert t not in ("250011", "159977", "199968"), f"Numeric leak: {t}"


def case1_same_table_continuation():
    """Same table split into 2 regions → should NOT split.

    Real scenario: a table has a small internal gap (e.g. due to a note),
    but both halves share the same column headers or same context text.
    """
    items = make_text_items([
        ("（人民币百万元）", 100), ("2024年", 100), ("2023年", 100),
        ("股本", 116), ("250,011", 116), ("250,011", 116),
        ("其他权益工具", 132), ("资本公积", 148),
    ])
    items += make_text_items([
        # 续表也带同样的表头行
        ("（人民币百万元）", 170), ("2024年", 170), ("2023年", 170),
        ("未分配利润", 186), ("股东权益合计", 202),
    ])
    # gap = 170 - 155 = 15
    prev = make_region(95, 155)
    curr = make_region(165, 210)

    result = _have_different_table_structure(prev, curr, items)
    assert not result, "Same table continuation with shared headers must NOT be split"


def case2_different_tables():
    """Equity table vs Deposit distribution -> MUST split"""
    items = make_text_items([
        # Equity table
        ("（人民币百万元）", 100), ("2024年", 100), ("2023年", 100),
        ("股本", 116), ("250,011", 116), ("250,011", 116),
        ("其他权益工具", 132), ("资本公积", 148), ("盈余公积", 164),
        ("未分配利润", 180), ("股东权益总额", 196),
    ])
    items += make_text_items([
        # Deposit distribution table
        ("（人民币百万元，百分比除外）", 350),
        ("2024年12月31日", 350), ("金额", 350), ("占比(%)", 350),
        ("2023年12月31日", 350), ("金额", 350), ("占比(%)", 350),
        ("长江三角洲", 366), ("5,239,658", 366), ("18.25", 366),
        ("珠江三角洲", 382), ("环渤海地区", 398), ("西部地区", 414),
    ])

    prev = make_region(95, 200)   # Equity
    curr = make_region(345, 420)  # Deposits

    result = _have_different_table_structure(prev, curr, items)
    assert result, "Two different tables MUST be detected as different"


def case3_unit_change():
    """Context unit shift -> should split"""
    prev = make_region(95, 200, context_text="（人民币百万元）")
    curr = make_region(300, 400, context_text="（人民币百万元，百分比除外）")
    result = _have_different_table_structure(prev, curr, [])
    assert result, "Unit change must trigger split"


def case4_same_unit_no_data():
    """Same context, no items -> should NOT split"""
    prev = make_region(95, 200, context_text="（人民币百万元）")
    curr = make_region(300, 400, context_text="（人民币百万元）")
    result = _have_different_table_structure(prev, curr, [])
    assert not result, "Same unit + no items must NOT split"


def case5_merge_regions_split():
    """Integration: _merge_regions_by_proximity splits two tables"""
    items = make_text_items([
        ("（人民币百万元）", 100), ("2024年", 100),
        ("股本", 116), ("250,011", 116),
        ("资本公积", 132), ("盈余公积", 148),
        ("股东权益总额", 164),
    ])
    items += make_text_items([
        ("（人民币百万元，百分比除外）", 240),
        ("金额", 240), ("占比(%)", 240),
        ("长江三角洲", 256), ("珠江三角洲", 272), ("环渤海地区", 288),
    ])

    regions = [
        {"y0": 95, "y1": 170, "context_text": "（人民币百万元）", "confidence": 0.8},
        {"y0": 235, "y1": 295, "context_text": "（人民币百万元，百分比除外）", "confidence": 0.8},
    ]

    merged = _merge_regions_by_proximity(regions, items, page_num=1, median_row_h=12)
    assert len(merged) == 2, f"Expected 2 boundaries, got {len(merged)}"

    b1, b2 = merged
    assert "百万元" in b1["caption"] and "百分比" not in b1["caption"]
    assert "百分比" in b2["caption"]


def case6_merge_regions_continuation():
    """Integration: same table continuation merges (small gap)"""
    items = make_text_items([
        ("（人民币百万元）", 100), ("2024年", 100),
        ("股本", 116), ("资本公积", 132), ("盈余公积", 148),
    ])
    items += make_text_items([
        ("（人民币百万元）", 170), ("2024年", 170),
        ("未分配利润", 186), ("股东权益合计", 202),
    ])

    regions = [
        {"y0": 95, "y1": 155, "context_text": "（人民币百万元）", "confidence": 0.8},
        {"y0": 165, "y1": 210, "context_text": "（人民币百万元）", "confidence": 0.8},
    ]

    merged = _merge_regions_by_proximity(regions, items, page_num=1, median_row_h=12)

    assert len(merged) == 1, f"Same table should merge to 1, got {len(merged)}"


def case7_jaccard_zero():
    """Completely different tables -> Jaccard=0 -> split"""
    items = make_text_items([
        ("资产总计", 100), ("负债合计", 116), ("股东权益", 132),
    ])
    items += make_text_items([
        ("营业收入", 300), ("营业成本", 316), ("净利润", 332),
    ])
    prev = make_region(95, 140)
    curr = make_region(295, 340)
    result = _have_different_table_structure(prev, curr, items)
    assert result, "Jaccard=0 must trigger split"


def case8_jaccard_high():
    """Identical headers in both halves -> high Jaccard -> merge"""
    items = make_text_items([
        ("项目", 100), ("2024年", 100), ("2023年", 100),
        ("营业收入", 116), ("营业成本", 132),
    ])
    items += make_text_items([
        ("项目", 300), ("2024年", 300), ("2023年", 300),
        ("管理费用", 316), ("财务费用", 332),
    ])
    prev = make_region(95, 140)
    curr = make_region(295, 340)
    result = _have_different_table_structure(prev, curr, items)
    assert not result, "Identical headers must NOT split"


# ---------------------------------------------------------------------------
# Row-level structural split tests (post-fusion)
# ---------------------------------------------------------------------------

def case9_structure_break_detection():
    """Row contains "百分比"/"金额"/"占比" → detected as new table"""
    # Simulate merged table: equity rows + deposit rows
    data = [
        ["（人民币百万元）", "2024年",  "2023年",  "", ""],
        ["（人民币百万元）", "12月31日", "12月31日", "", ""],
        ["股本",          "250,011", "250,011", "", ""],
        ["其他权益工具",    "159,977", "199,968", "", ""],
        ["资本公积",       "135,736", "135,619", "", ""],
        ["盈余公积",       "402,196", "369,906", "", ""],
        ["未分配利润",     "1,781,715", "1,674,405", "", ""],
        # NEW TABLE starts here (deposit distribution)
        ["（人民币百万元，百分比除外）", "2024年12月31日", "", "2024年12月31日", "2023年12月31日"],
        ["（人民币百万元，百分比除外）", "金额", "占比(%)", "金额", "占比(%)"],
        ["长江三角洲", "5,239,658", "18.25", "5,402,635", "19.54"],
        ["珠江三角洲", "4,139,205", "14.41", "4,132,280", "14.94"],
        ["吸收存款", "28,713,870", "100.00", "27,654,011", "100.00"],
    ]

    split_at = _find_structure_break_in_data(data, caption="（人民币百万元）")
    assert split_at == 7, (
        f"Expected split at row 7 (new table header), got {split_at}"
    )
    print("  [PASS] Case9: structure break at row with '百分比'")


def case10_no_break_in_single_table():
    """Single table without structure change → no break"""
    data = [
        ["项目", "2024年", "2023年"],
        ["营业收入", "100", "90"],
        ["营业成本", "60", "55"],
        ["净利润", "40", "35"],
    ]
    split_at = _find_structure_break_in_data(data, caption="利润表")
    assert split_at == -1, f"Should be -1, got {split_at}"


def case11_no_break_when_caption_has_kw():
    """Unit keywords appear in caption → NOT a new table"""
    data = [
        ["（人民币百万元，百分比除外）", "金额", "占比(%)"],
        ["项目A", "100", "10%"],
        ["项目B", "200", "20%"],
        ["项目C", "300", "30%"],
        ["（人民币百万元，百分比除外）", "金额", "占比(%)"],  # same unit, continuation
        ["项目D", "400", "40%"],
        ["项目E", "500", "50%"],
    ]
    split_at = _find_structure_break_in_data(
        data, caption="（人民币百万元，百分比除外）"
    )
    assert split_at == -1, (
        f"Caption already has '百分比', should NOT split, got {split_at}"
    )


def case12_split_fused_table_integration():
    """Integration: _split_fused_table_by_structure splits merged table"""
    table = {
        "page": 1,
        "y0": 100, "y1": 500,
        "caption": "（人民币百万元）",
        "type": "table",
        "data": [
            ["（人民币百万元）", "2024年",  "2023年"],
            ["（人民币百万元）", "12月31日", "12月31日"],
            ["股本",          "250,011", "250,011"],
            ["资本公积",       "135,736", "135,619"],
            ["盈余公积",       "402,196", "369,906"],
            ["未分配利润",     "1,781,715", "1,674,405"],
            ["股东权益总额",   "3,343,965", "3,172,074"],
            # New table
            ["（人民币百万元，百分比除外）", "金额", "占比(%)", "金额", "占比(%)"],
            ["长江三角洲", "5,239,658", "18.25", "5,402,635", "19.54"],
            ["珠江三角洲", "4,139,205", "14.41", "4,132,280", "14.94"],
        ],
        "rows": 10,
        "cols": 5,
        "extractor": "hybrid_fusion",
    }

    result = _split_fused_table_by_structure([table])
    assert len(result) == 2, f"Expected 2 tables after split, got {len(result)}"

    t1, t2 = result
    assert t1["rows"] == 7, f"Table A should have 7 rows, got {t1['rows']}"
    assert t2["rows"] == 3, f"Table B should have 3 rows, got {t2['rows']}"
    assert t2["data"][0][0] == "（人民币百万元，百分比除外）"
    print(f"  [PASS] Case12: fused table split into {t1['rows']}+{t2['rows']} rows")


def case13_short_table_no_split():
    """Short table (< 8 rows) → no split attempted"""
    table = {
        "page": 1,
        "caption": "（人民币百万元）",
        "data": [
            ["项目", "2024年"], ["A", "100"], ["B", "200"],
        ],
        "rows": 3, "cols": 2,
        "extractor": "hybrid_fusion",
    }
    result = _split_fused_table_by_structure([table])
    assert len(result) == 1, "Short table should not be split"



def case14_y_reestimate_after_split():
    """Y coordinates re-estimated from liteparse after structural split.

    Simulates the real scenario (user's page_001):
      - pdf2docx produces 1 merged table: equity rows first, then deposit rows
      - deposit data starts with "百分比除外"/"金额"/"占比(%)" keywords → triggers split
      - fusion matched parent to equity boundary → parent y0=362
      - after split: t1=equity (y0 should be ~362), t2=deposit (y0 should be ~161)
      - after sort: deposit (y0=161) ordered before equity (y0=362) → correct physical order
    """
    # Build liteparse_data simulating page_001
    lp_items = []
    # Deposit region text items (physically above: Y=161-299)
    lp_items += make_text_items([
        ("存款地域分布", 161),
        ("（人民币百万元，百分比除外）", 165),
        ("2024年12月31日", 165), ("金额", 165), ("占比(%)", 165),
        ("2023年12月31日", 165), ("金额", 165), ("占比(%)", 165),
        ("长江三角洲", 181), ("5,239,658", 181), ("18.25", 181),
        ("吸收存款合计", 293), ("28,713,870", 293), ("100.00", 293),
    ])
    # Equity region text items (physically below: Y=362-536)
    lp_items += make_text_items([
        ("股东权益", 362),
        ("（人民币百万元）", 366),
        ("2024年", 366), ("2023年", 366),
        ("股本", 382), ("250,011", 382), ("250,011", 382),
        ("其他权益工具", 398), ("159,977", 398), ("199,968", 398),
        ("资本公积", 414), ("135,736", 414), ("135,619", 414),
        ("盈余公积", 430), ("402,196", 430), ("369,906", 430),
        ("未分配利润", 446), ("1,781,715", 446),
        ("股东权益总额", 462), ("3,343,965", 462), ("3,172,074", 462),
    ])

    liteparse_data = {
        "pages": [{
            "page_number": 1,
            "text_items": lp_items,
            "table_regions": [
                {"y0": 161, "y1": 299, "context_text": "存款地域分布", "confidence": 0.8},
                {"y0": 362, "y1": 536, "context_text": "股东权益", "confidence": 0.8},
            ],
        }],
    }

    # Parent table: pdf2docx produced 1 merged table, fusion matched to equity boundary
    # → parent y0=362 (WRONG for deposit data which is physically at Y≈161)
    table = {
        "page": 1,
        "y0": 362, "y1": 536,
        "caption": "（人民币百万元）",  # equity's caption
        "type": "table",
        "data": [
            # Equity rows (physically below on page, but first in merged data)
            # These are equity account rows — no "百分比"/"占比" keywords → no trigger
            ["股本", "250,011", "250,011", "", ""],
            ["其他权益工具", "159,977", "199,968", "", ""],
            ["资本公积", "135,736", "135,619", "", ""],
            ["盈余公积", "402,196", "369,906", "", ""],
            ["未分配利润", "1,781,715", "1,674,405", "", ""],
            ["股东权益总额", "3,343,965", "3,172,074", "", ""],
            ["一般风险准备", "282,687", "249,233", "", ""],
            # Deposit table header rows → triggers split (has "百分比"/"金额"/"占比(%)")
            ["（人民币百万元，百分比除外）", "2024年12月31日", "", "2023年12月31日", ""],
            ["", "金额", "占比(%)", "金额", "占比(%)"],
            # Deposit data rows
            ["长江三角洲", "5,239,658", "18.25", "5,402,635", "19.54"],
            ["珠江三角洲", "4,139,205", "14.41", "4,132,280", "14.94"],
            ["环渤海地区", "3,528,264", "12.28", "3,606,008", "13.04"],
        ],
        "rows": 12,
        "cols": 5,
        "extractor": "hybrid_fusion",
    }

    result = _split_fused_table_by_structure([table], liteparse_data)
    assert len(result) == 2, (
        f"Expected 2 tables after split, got {len(result)}. "
        f"Check that row with '百分比' triggers detection."
    )

    t1, t2 = result
    # After sort by (page, y0): deposit (y0≈161) MUST be before equity (y0≈362)
    assert t1["y0"] < t2["y0"], (
        f"Deposit (physically above) must have smaller y0 after sort, "
        f"got t1.y0={t1['y0']} vs t2.y0={t2['y0']}"
    )
    assert t1["y0"] < 200, (
        f"Deposit y0 should be near ~165, got {t1['y0']}"
    )
    assert t2["y0"] > 300, (
        f"Equity y0 should be near ~366, got {t2['y0']}"
    )
    # table_id should be sequential after sort
    assert t1["table_id"] == 0
    assert t2["table_id"] == 1
    print(f"  [PASS] Case14: deposit(y0={t1['y0']:.0f}) < equity(y0={t2['y0']:.0f})")


def case15_y_reestimate_single_table_pass_through():
    """Table without split keeps original y0 unchanged."""
    lp_items = make_text_items([
        ("项目", 100), ("2024年", 100),
        ("营业收入", 116), ("营业成本", 132),
    ])
    liteparse_data = {
        "pages": [{"page_number": 1, "text_items": lp_items, "table_regions": []}],
    }

    table = {
        "page": 1,
        "y0": 95, "y1": 140,
        "caption": "利润表",
        "data": [
            ["项目", "2024年"],
            ["营业收入", "100"],
            ["营业成本", "60"],
        ],
        "rows": 3, "cols": 2,
        "extractor": "hybrid_fusion",
    }

    result = _split_fused_table_by_structure([table], liteparse_data)
    assert len(result) == 1, "Short table without break should not be split"
    assert result[0]["y0"] == 95, f"Original y0 should be preserved, got {result[0]['y0']}"


def case16_merge_regions_split_no_items():
    """unit change alone (no text_items) triggers split in merge_regions"""
    prev = make_region(95, 200, context_text="（人民币百万元）")
    curr = make_region(235, 295, context_text="（人民币百万元，百分比除外）")
    # gap = 35 ≤ 60 → middle gap path
    result = _have_different_table_structure(prev, curr, [])
    assert result, "Unit change must trigger split even with no text_items"


def case17_real_scenario_deposit_then_equity():
    """Real scenario: deposit rows first, equity has year patterns not keywords.

    Simulates test_subset8.pdf page 1: fused table has deposit data (Y≈161-299)
    first, then equity data (Y≈362-536).  Equity header has "2024年"/"12月31日"
    but NO "百分比"/"占比" keywords.  Signal 3 (year pattern) must detect break.
    """
    lp_items = make_text_items([
        ("2024年12月31日", 166), ("金额", 184), ("占比(%)", 184),
        ("人民币百万元百分比除外", 184),
        ("长江三角洲", 202), ("吸收存款", 286),
        ("2024年", 378), ("人民币百万元", 394), ("12月31日", 394),
        ("股本", 430), ("股东权益总额", 530),
    ])
    liteparse_data = {
        "pages": [{"page_number": 1, "text_items": lp_items,
            "table_regions": [
                {"y0":161,"y1":299,"context_text":"存款","confidence":0.8},
                {"y0":362,"y1":536,"context_text":"股东权益","confidence":0.8},
            ],
        }],
    }
    table = {
        "page": 1, "y0": 161, "y1": 536,
        "caption": "按区域划分的存款分布情况",
        "data": [
            ["2024年12月31日","","2023年12月31日",""],
            ["（人民币百万元，百分比除外）","金额","占比(%)","金额","占比(%)"],
            ["长江三角洲","5,239,658","18.25","5,402,635","19.54"],
            ["珠江三角洲","4,139,205","14.41","4,132,280","14.94"],
            ["环渤海地区","5,387,852","18.76","5,030,828","18.19"],
            ["吸收存款","28,713,870","100.00","27,654,011","100.00"],
            ["","2024年","","2023年"],               # equity year header
            ["（人民币百万元）","12月31日","","12月31日"],
            ["股本","250,011","","250,011"],
            ["其他权益工具","159,977","","199,968"],
            ["优先股","59,977","","59,977"],
            ["永续债","100,000","","139,991"],
            ["资本公积","135,736","","135,619"],
            ["盈余公积","402,196","","369,906"],
            ["一般风险准备","534,591","","496,255"],
            ["股东权益总额","3,343,965","","3,172,074"],
        ],
        "rows": 16, "cols": 4,
    }
    result = _split_fused_table_by_structure([table], liteparse_data)
    assert len(result) == 2, (
        f"Year pattern must break deposit/equity, got {len(result)} tables"
    )
    t1, t2 = result
    assert len(t1["data"]) == 6, f"Deposit 6 rows, got {len(t1['data'])}"
    assert len(t2["data"]) == 10, f"Equity 10 rows, got {len(t2['data'])}"
    assert t1["y0"] < 200, f"Deposit y0≈166, got {t1['y0']}"
    assert t2["y0"] > 300, f"Equity y0>300, got {t2['y0']}"
    print(f"  [PASS] deposit({len(t1['data'])}r,y0={t1['y0']})"
          f" < equity({len(t2['data'])}r,y0={t2['y0']})")


def case18_column_width_jump_split():
    """Signal 0: fused table with different column widths should split.

    Simulates page 2 of test_subset8.pdf: Table 1 (9 cols, 4 data rows)
    followed by Table 2 (19 cols, 10 rows).  Column count jumps from
    9 to 19 at the boundary — Signal 0 must detect this structural change.
    """
    # 9-col table 1 data (already stripped of header rows)
    # 19-col table 2 data (pdf2docx exploded headers + data)
    data = [
        # Table 1: 理财发行 (9 cols)
        ["期数","金额","期数","金额","期数","金额","期数","金额","（人民币百万元，期数除外）"],
        ["1,100","1,499,121","676","3,315,531","781","3,215,927","995","1,598,725","建信理财"],
        ["2","79,443","","101,819","","138,974","2","42,288","本行"],
        ["1,102","1,578,564","676","3,417,350","781","3,354,901","997","1,641,013","总额"],
        # Table 2: 理财投资资产 (19 cols)
        ["2024年12月31日","","","","","","2024年12月31日","","","","","","","","","2024年12月31日 2024年12月","","",""],
        ["建信理财","","建信理财","","","建信理财","","","","","本行","本集团","","","","本集团 本集团 本行 本集团","","",""],
        ["金额","占比(%)","金额","占比(%)","占比(%)","","占比(%)","","","","金额","占比(%)","金额","占比(%)","金额","占比(%) 金额 占比(%)","","",""],
        ["现金、存款及同业存单","现金、存款及同业存单","1,008,220","60.80","20,512","34.60","1,028,732","","1,028,732","1,028,732","","","59.90","907,809","58.73","31,462","32.01","939,271","57.14"],
        ["债券","债券","440,983","26.60","5,052","8.52","8.52","446,035","446,035","25.97","478,169","30.94","7,942","8.08","486,111","29.57","","",""],
        ["权益类资产","权益类资产","1,793","0.11","25,679","43.31","43.31","27,472","27,472","1.60","23,643","1.53","11,799","12.01","35,442","2.16","","",""],
        ["非标准化债权类资产","非标准化债权类资产","5,171","0.31","8,042","13.57","13.57","13,213","13,213","0.77","11,714","0.76","43,586","44.35","55,300","3.36","","",""],
        ["其他类资产1","其他类资产1","201,987","12.18","－","－","－","201,987","201,987","11.76","124,247","8.04","3,492","3.55","127,739","7.77","","",""],
        ["总额","总额","1,658,154","100.00","59,285","100.00","1,717,439","","1,717,439","1,717,439","","","100.00","1,545,582","100.00","98,281","100.00","1,643,863","100.00"],
    ]

    # Width check
    widths = [len(r) for r in data]
    assert widths[0] == 9, f"Row 0 should be 9 cols, got {widths[0]}"
    assert widths[3] == 9, f"Row 3 should be 9 cols, got {widths[3]}"
    assert widths[4] == 19, f"Row 4 should be 19 cols, got {widths[4]}"

    split_row = _find_structure_break_in_data(data, caption="")
    assert split_row == 4, (
        f"Should split at row 4 (9 cols -> 19 cols), got {split_row}. "
        f"Signal 0 (column width jump) must fire before Signal 1."
    )
    print(f"  [PASS] Column width jump 9->19 detected at row {split_row}")


def case19_footnote_year_pattern_skipped():
    """Signal 3: footnote containing year pattern should NOT trigger split.

    A data row with "注：2024年..." should be treated as a footnote annotation
    and NOT as a new table header, preventing false-positive splits.
    """
    data = [
        ["项目","2024年","2023年"],
        ["营业收入","100,000","90,000"],
        ["营业成本","80,000","72,000"],
        # Footnote with year reference — should NOT split here
        ["注：2024年数据经审计","",""],
        ["其中：主营业务收入","85,000","78,000"],
        ["其中：其他业务收入","15,000","12,000"],
        ["营业利润","20,000","18,000"],
        ["净利润","15,000","13,000"],
    ]

    split_row = _find_structure_break_in_data(data, caption="")
    assert split_row < 0, (
        f"Footnote '注：2024年数据经审计' should NOT trigger split, "
        f"but got split_row={split_row}"
    )
    print(f"  [PASS] Footnote year pattern correctly skipped (no split)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    ("Case0: token extraction", case0_token_extraction),
    ("Case1: same table continuation", case1_same_table_continuation),
    ("Case2: different tables MUST split", case2_different_tables),
    ("Case3: unit change in context", case3_unit_change),
    ("Case4: same unit, no data", case4_same_unit_no_data),
    ("Case5: merge_regions splits", case5_merge_regions_split),
    ("Case6: merge_regions merges continuation", case6_merge_regions_continuation),
    ("Case7: Jaccard=0 edge", case7_jaccard_zero),
    ("Case8: Jaccard high (same headers)", case8_jaccard_high),
    ("Case9: structure break detection", case9_structure_break_detection),
    ("Case10: no break in single table", case10_no_break_in_single_table),
    ("Case11: no break when caption has kw", case11_no_break_when_caption_has_kw),
    ("Case12: split fused table integration", case12_split_fused_table_integration),
    ("Case13: short table no split", case13_short_table_no_split),
    ("Case14: Y reestimate after split", case14_y_reestimate_after_split),
    ("Case15: Y reestimate single table passthrough", case15_y_reestimate_single_table_pass_through),
    ("Case16: unit change no items in merge_regions", case16_merge_regions_split_no_items),
    ("Case17: real scenario deposit then equity (year pattern)", case17_real_scenario_deposit_then_equity),
    ("Case18: column width jump 9->19 detects split", case18_column_width_jump_split),
    ("Case19: footnote year pattern is skipped", case19_footnote_year_pattern_skipped),
]


if __name__ == "__main__":
    passed = 0
    failed_list = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  [PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failed_list.append(name)

    total = len(TESTS)
    print(f"\n{'='*50}")
    if passed == total:
        print(f"ALL PASS: {passed}/{total}")
    else:
        print(f"FAILED: {passed}/{total}")
        for f in failed_list:
            print(f"  - {f}")
    print(f"{'='*50}")
