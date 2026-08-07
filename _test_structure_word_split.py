# -*- coding: utf-8 -*-
"""两表混并：结构拆分须收窄 _source_words；附注不搅乱列槽。"""

from copy import deepcopy

from codes.format_corrector.structure_pre_split import expand_tables_with_structure_split
from codes.reconstruct.grid_nucleus import apply_grid_to_table, restore_table_grid
from codes.reconstruct.grid_nucleus.word_segment import (
    assign_source_words_to_parts,
    find_year_header_cut_ys,
    row_cluster_is_footnote,
    split_source_words_by_year_bands,
    strip_footnote_rows_from_data,
    trim_trailing_footnote_words,
)
from codes.reconstruct.grid_nucleus.preprocess import preprocess_words
from codes.reconstruct.grid_nucleus.row_cluster import cluster_rows
from codes.reconstruct.grid_nucleus.column_infer import infer_column_slots


def _asset_credit_words():
    """page_016 资产质量 + 信用成本 + 附注 + 下表年头（简化几何）。"""
    rows = [
        # 资产质量表头
        (316.2, [("2025年", 290, 320), ("2024年", 352, 383), ("本年末比", 434, 471), ("2023年", 502, 533)]),
        (330.4, [("资产质量指标(%)", 85, 155), ("12月31日", 280, 320), ("12月31日", 342, 383), ("上年末增减", 425, 471), ("12月31日", 492, 533)]),
        (347.5, [("不良贷款率", 85, 131), ("0.94", 302, 320), ("0.95", 364, 369), ("下降0.01个百分点", 396, 420), ("0.95", 515, 520)]),
        (361.5, [("拨备覆盖率(1)", 85, 138), ("391.79", 292, 307), ("411.98", 354, 369), ("下降20.19个百分点", 391, 420), ("437.70", 504, 520)]),
        (375.5, [("贷款拨备率(2)", 85, 138), ("3.68", 302, 320), ("3.92", 364, 369), ("下降0.24个百分点", 396, 420), ("4.14", 515, 520)]),
        # 信用成本表头（第二段）
        (399.2, [("2025年", 290, 320), ("2024年", 352, 383), ("本年比上年增减", 406, 471), ("2023年", 502, 533)]),
        (416.4, [("信用成本(3)", 85, 128), ("0.60", 302, 320), ("0.65", 364, 369), ("下降0.05个百分点", 396, 420), ("0.74", 515, 520)]),
        (437.3, [("注：", 85, 101)]),
        (457.0, [("(1)", 85, 93), ("拨备覆盖率=贷款损失准备／不良贷款余额。", 113, 257)]),
        # 下表年头
        (528.8, [("2025年", 290, 320), ("2024年", 352, 383), ("本年末比", 434, 471), ("2023年", 502, 533)]),
    ]
    words = []
    for y0, cells in rows:
        for text, x0, x1 in cells:
            words.append({
                "text": text,
                "x0": float(x0),
                "x1": float(x1),
                "y0": float(y0),
                "y1": float(y0) + 10.0,
            })
    return words


def test_year_header_cuts_two_tables():
    words = _asset_credit_words()
    cuts = find_year_header_cut_ys(words)
    assert len(cuts) >= 1, cuts
    assert 390 < cuts[0] < 410, cuts
    segs = split_source_words_by_year_bands(words)
    assert len(segs) >= 2, len(segs)
    t0 = " ".join(w["text"] for w in segs[0])
    t1 = " ".join(w["text"] for w in segs[1])
    assert "不良贷款率" in t0 and "信用成本" not in t0, t0
    assert "信用成本" in t1 and "不良贷款率" not in t1, t1


def test_structure_split_narrows_source_words():
    words = _asset_credit_words()
    glued = {
        "page": 16,
        "type": "table",
        "cols": 4,
        "_source_words": words,
        "data": [
            ["", "2025年 2024年", "本年末比", "2023年"],
            ["资产质量指标(%)", "12月31日 12月31日", "上年末增减", "12月31日"],
            ["不良贷款率", "0.94 0.95", "下降0.01个百分点", "0.95"],
            ["拨备覆盖率(1)", "391.79 411.98", "下降20.19个百分点", "437.70"],
            ["贷款拨备率(2)", "3.68 3.92", "下降0.24个百分点", "4.14"],
            ["", "2025年 2024年", "本年比上年增减", "2023年"],
            ["信用成本(3)", "0.60 0.65", "下降0.05个百分点", "0.74"],
            ["注：", "", "", ""],
        ],
    }
    expanded, notes = expand_tables_with_structure_split([glued])
    assert len(expanded) == 2, (len(expanded), notes)
    w0 = expanded[0].get("_source_words") or []
    w1 = expanded[1].get("_source_words") or []
    assert any(w["text"] == "不良贷款率" for w in w0)
    assert not any(w["text"].startswith("信用成本") for w in w0), [w["text"] for w in w0]
    assert any(str(w.get("text", "")).startswith("信用成本") for w in w1)
    assert not any(w["text"] == "不良贷款率" for w in w1), [w["text"] for w in w1]


def test_grid_after_split_keeps_year_columns():
    words = _asset_credit_words()
    glued = {
        "page": 16,
        "type": "table",
        "cols": 4,
        "_source_words": words,
        "data": [
            ["", "2025年 2024年", "本年末比", "2023年"],
            ["资产质量指标(%)", "12月31日 12月31日", "上年末增减", "12月31日"],
            ["不良贷款率", "0.94 0.95", "下降0.01个百分点", "0.95"],
            ["拨备覆盖率(1)", "391.79 411.98", "下降20.19个百分点", "437.70"],
            ["贷款拨备率(2)", "3.68 3.92", "下降0.24个百分点", "4.14"],
            ["", "2025年 2024年", "本年比上年增减", "2023年"],
            ["信用成本(3)", "0.60 0.65", "下降0.05个百分点", "0.74"],
        ],
    }
    expanded, _ = expand_tables_with_structure_split([deepcopy(glued)])
    assert len(expanded) == 2
    for i, part in enumerate(expanded):
        res = restore_table_grid(part)
        data = res.data or []
        assert data, (i, res.errors, res.method)
        glued_cell = any(
            "2025" in str(c) and "2024" in str(c) for r in data for c in r
        )
        assert not glued_cell, (i, data)
        header = data[0] if data else []
        years = [
            c for c in header
            if "年" in str(c) and "增减" not in str(c) and "比" not in str(c)
        ]
        assert len(years) >= 2, (i, header)
        assert (res.n_cols or 0) >= 4, (i, res.n_cols, data)


def test_wide_footnote_does_not_merge_year_slots():
    """跨多列的脚注框不得把 2025/2024 槽并掉。"""
    from codes.reconstruct.grid_nucleus.column_infer import infer_column_slots

    words = [
        {"text": "2025年", "x0": 290, "x1": 320, "y0": 400, "y1": 410},
        {"text": "2024年", "x0": 352, "x1": 383, "y0": 400, "y1": 410},
        {"text": "本年比上年增减", "x0": 406, "x1": 471, "y0": 400, "y1": 410},
        {"text": "2023年", "x0": 502, "x1": 533, "y0": 400, "y1": 410},
        {"text": "信用成本(3)", "x0": 85, "x1": 128, "y0": 416, "y1": 426},
        {"text": "0.60", "x0": 302, "x1": 320, "y0": 416, "y1": 426},
        {"text": "0.65", "x0": 364, "x1": 369, "y0": 416, "y1": 426},
        {"text": "下降0.05个百分点", "x0": 396, "x1": 420, "y0": 416, "y1": 426},
        {"text": "0.74", "x0": 515, "x1": 520, "y0": 416, "y1": 426},
        {"text": "注：", "x0": 85, "x1": 101, "y0": 437, "y1": 444},
        {
            "text": "(3) 信用成本=贷款和垫款信用减值损失／贷款和垫款总额平均值。",
            "x0": 85,
            "x1": 538,
            "y0": 497,
            "y1": 505,
        },
    ]
    rows = cluster_rows(preprocess_words(words))
    n_cols, centers = infer_column_slots(rows)
    assert n_cols >= 5, (n_cols, centers)
    # 2025 与 2024 槽心应分开（约 305 vs 367）
    yearish = sorted(centers)[1:3]
    assert yearish[1] - yearish[0] > 40, centers



def test_trim_footnote_words_keeps_credit_only():
    words = _asset_credit_words()
    segs = split_source_words_by_year_bands(words)
    assert len(segs) >= 2
    credit = segs[1]
    texts = " ".join(w["text"] for w in credit)
    assert "信用成本" in texts
    assert "注：" not in texts
    assert "拨备覆盖率=" not in texts
    trimmed = trim_trailing_footnote_words(
        [w for w in words if w["y0"] >= 395 and w["y0"] < 520]
    )
    assert "信用成本" in " ".join(w["text"] for w in trimmed)
    assert not any(w["text"].startswith("注") for w in trimmed)


def test_strip_footnote_rows_from_credit_data():
    data = [
        ["", "2025年", "2024年", "本年比上年增减", "2023年"],
        ["信用成本(3)", "0.60", "0.65", "下降0.05个百分点", "0.74"],
        ["注：", "", "", "", ""],
        ["(1)", "拨备覆盖率=贷款损失准备／不良贷款余额。", "", "", ""],
        ["(2) 贷款拨备率=贷款损失准备／贷款和垫款总额。", "", "", "", ""],
    ]
    cleaned = strip_footnote_rows_from_data(data)
    assert len(cleaned) == 2, cleaned
    flat = " ".join(str(c) for r in cleaned for c in r)
    assert "注：" not in flat
    assert "拨备覆盖率=" not in flat


def test_expand_peels_footnotes_from_credit_part():
    words = _asset_credit_words()
    glued = {
        "page": 16,
        "type": "table",
        "_source_words": words,
        "data": [
            ["", "2025年", "2024年", "本年末比", "2023年"],
            ["资产质量指标(%)", "12月31日", "12月31日", "上年末增减", "12月31日"],
            ["不良贷款率", "0.94", "0.95", "下降0.01个百分点", "0.95"],
            ["拨备覆盖率(1)", "391.79", "411.98", "下降20.19个百分点", "437.70"],
            ["贷款拨备率(2)", "3.68", "3.92", "下降0.24个百分点", "4.14"],
            ["", "2025年", "2024年", "本年比上年增减", "2023年"],
            ["信用成本(3)", "0.60", "0.65", "下降0.05个百分点", "0.74"],
            ["注：", "", "", "", ""],
            ["(1)", "拨备覆盖率=贷款损失准备／不良贷款余额。", "", "", ""],
        ],
    }
    expanded, notes = expand_tables_with_structure_split([deepcopy(glued)])
    assert len(expanded) == 2
    credit = expanded[1]
    data = credit.get("data") or []
    flat = " ".join(str(c) for r in data for c in r)
    assert "信用成本" in flat
    assert "注：" not in flat
    assert not any(
        str(w.get("text", "")).startswith("注")
        for w in (credit.get("_source_words") or [])
    )
    assert any("剥离表尾注释" in n for n in notes)
    res = restore_table_grid(credit)
    out = " ".join(str(c) for r in (res.data or []) for c in r)
    assert "注：" not in out
    assert "拨备覆盖率=" not in out


def test_assign_words_to_parts_order():
    words = _asset_credit_words()
    parts = [
        [["不良贷款率", "0.94"], ["拨备覆盖率(1)", "1"]],
        [["信用成本(3)", "0.60"]],
    ]
    assigned = assign_source_words_to_parts(words, parts)
    assert "不良贷款率" in " ".join(w["text"] for w in assigned[0])
    assert "信用成本" in " ".join(w["text"] for w in assigned[1])
    assert "注：" not in " ".join(w["text"] for w in assigned[1])


def test_split_parts_get_distinct_geometry_and_no_dup_after_reattach():
    """拆段后收紧 bbox；模拟 attach 大区域重挂后，后段不得重建成前表。"""
    from codes.reconstruct.liteparse_anchor import attach_liteparse_words

    words = _asset_credit_words()
    glued = {
        "page": 16,
        "type": "table",
        "bbox": [80.0, 310.0, 540.0, 540.0],
        "y0": 310.0,
        "y1": 540.0,
        "_source_words": words,
        "data": [
            ["", "2025年", "2024年", "本年末比", "2023年"],
            ["资产质量指标(%)", "12月31日", "12月31日", "上年末增减", "12月31日"],
            ["不良贷款率", "0.94", "0.95", "下降0.01个百分点", "0.95"],
            ["拨备覆盖率(1)", "391.79", "411.98", "下降20.19个百分点", "437.70"],
            ["贷款拨备率(2)", "3.68", "3.92", "下降0.24个百分点", "4.14"],
            ["", "2025年", "2024年", "本年比上年增减", "2023年"],
            ["信用成本(3)", "0.60", "0.65", "下降0.05个百分点", "0.74"],
            ["注：", "", "", "", ""],
            ["(1)", "拨备覆盖率=x", "", "", ""],
        ],
    }
    expanded, _ = expand_tables_with_structure_split([deepcopy(glued)])
    assert len(expanded) == 2
    # 几何已拆开
    assert float(expanded[0].get("y1") or 0) < float(expanded[1].get("y0") or 999)
    assert expanded[0].get("_format_structure_split")
    assert "信用成本" in " ".join(
        str(c) for r in (expanded[1].get("data") or []) for c in r
    )

    # 模拟 liteparse 页：整页字框（两表都在）
    page = {
        "text_items": [
            {
                "text": w["text"],
                "x0": w["x0"],
                "y0": w["y0"],
                "x1": w["x1"],
                "y1": w["y1"],
            }
            for w in words
        ],
        "table_regions": [
            {"x0": 80, "y0": 310, "x1": 540, "y1": 540},
        ],
    }
    for i, part in enumerate(expanded):
        attach_liteparse_words(part, liteparse_page=page)
        wtxt = [w["text"] for w in (part.get("_source_words") or [])]
        if i == 0:
            assert any("不良" in t for t in wtxt)
            assert not any(t.startswith("信用成本") for t in wtxt), wtxt
        else:
            assert any(t.startswith("信用成本") for t in wtxt), wtxt
            assert not any("不良" in t for t in wtxt), wtxt
        apply_grid_to_table(part)

    a = " ".join(str(c) for r in (expanded[0].get("data") or []) for c in r)
    b = " ".join(str(c) for r in (expanded[1].get("data") or []) for c in r)
    assert "不良贷款率" in a and "信用成本" not in a
    assert "信用成本" in b and "不良贷款率" not in b
    # 信用成本数据行仍在
    assert any(
        "0.60" in str(c) or "0 .60" in str(c)
        for r in (expanded[1].get("data") or [])
        for c in r
    )


def test_asset_only_data_but_words_have_credit_promotes_table():
    """data 只有资产质量，字框仍含信用成本年头带 → 提升为独立表。"""
    words = _asset_credit_words()
    asset_only = {
        "page": 16,
        "type": "table",
        "table_category": "数据表(缺表头)",
        "_anomaly": {"header_missing": True},
        "_source_words": words,  # 故意带两表字框
        "data": [
            ["", "2025年", "2024年", "本年末比", "2023年"],
            ["资产质量指标(%)", "12月31日", "12月31日", "上年末增减", "12月31日"],
            ["不良贷款率", "0.94", "0.95", "下降0.01个百分点", "0.95"],
            ["拨备覆盖率(1)", "391.79", "411.98", "下降20.19个百分点", "437.70"],
            ["贷款拨备率(2)", "3.68", "3.92", "下降0.24个百分点", "4.14"],
        ],
    }
    expanded, notes = expand_tables_with_structure_split([deepcopy(asset_only)])
    assert len(expanded) >= 2, (len(expanded), notes)
    titles = [str(t.get("title") or "") for t in expanded]
    flats = [
        " ".join(str(c) for r in (t.get("data") or []) for c in r) for t in expanded
    ]
    assert any("信用成本" in f for f in flats), (flats, notes)
    assert any("信用成本" in t for t in titles) or any(
        "信用成本" in f for f in flats
    ), titles
    assert any("字框年头带提升" in n for n in notes), notes


def test_split_sets_title_on_credit_part():
    words = _asset_credit_words()
    glued = {
        "page": 16,
        "type": "table",
        "_source_words": words,
        "data": [
            ["", "2025年", "2024年", "本年末比", "2023年"],
            ["资产质量指标(%)", "12月31日", "12月31日", "上年末增减", "12月31日"],
            ["不良贷款率", "0.94", "0.95", "下降0.01个百分点", "0.95"],
            ["拨备覆盖率(1)", "391.79", "411.98", "下降20.19个百分点", "437.70"],
            ["贷款拨备率(2)", "3.68", "3.92", "下降0.24个百分点", "4.14"],
            ["", "2025年", "2024年", "本年比上年增减", "2023年"],
            ["信用成本(3)", "0.60", "0.65", "下降0.05个百分点", "0.74"],
        ],
    }
    expanded, _ = expand_tables_with_structure_split([deepcopy(glued)])
    assert len(expanded) == 2
    assert "信用成本" in str(expanded[1].get("title") or "")


def test_skip_duplicate_asset_keep_credit():
    """页上已有资产质量时，混表拆分不得再插一份相同前段。"""
    words = _asset_credit_words()
    asset = {
        "page": 16,
        "type": "table",
        "title": "资产质量指标(%)",
        "_source_words": [w for w in words if w["y0"] < 395],
        "data": [
            ["", "2025年", "2024年", "本年末比", "2023年"],
            ["资产质量指标(%)", "12月31日", "12月31日", "上年末增减", "12月31日"],
            ["不良贷款率", "0.94", "0.95", "下降0.01个百分点", "0.95"],
            ["拨备覆盖率(1)", "391.79", "411.98", "下降20.19个百分点", "437.70"],
            ["贷款拨备率(2)", "3.68", "3.92", "下降0.24个百分点", "4.14"],
        ],
    }
    merged = {
        "page": 16,
        "type": "table",
        "_source_words": words,
        "data": [
            ["", "2025年", "2024年", "本年末比", "2023年"],
            ["资产质量指标(%)", "12月31日", "12月31日", "上年末增减", "12月31日"],
            ["不良贷款率", "0.94", "0.95", "下降0.01个百分点", "0.95"],
            ["拨备覆盖率(1)", "391.79", "411.98", "下降20.19个百分点", "437.70"],
            ["贷款拨备率(2)", "3.68", "3.92", "下降0.24个百分点", "4.14"],
            ["", "2025年", "2024年", "本年比上年增减", "2023年"],
            ["信用成本(3)", "0.60", "0.65", "下降0.05个百分点", "0.74"],
        ],
    }
    expanded, notes = expand_tables_with_structure_split(
        [deepcopy(asset), deepcopy(merged)]
    )
    assert any("重复" in n or "去重" in n for n in notes), notes
    npl_tables = [
        t for t in expanded
        if "不良贷款率" in " ".join(str(c) for r in (t.get("data") or []) for c in r)
    ]
    credit_tables = [
        t for t in expanded
        if "信用成本" in " ".join(str(c) for r in (t.get("data") or []) for c in r)
        and "不良贷款率" not in " ".join(str(c) for r in (t.get("data") or []) for c in r)
    ]
    assert len(npl_tables) == 1, (len(npl_tables), notes)
    assert len(credit_tables) == 1, (len(credit_tables), notes)
    assert "0.60" in " ".join(
        str(c) for r in (credit_tables[0].get("data") or []) for c in r
    )


def test_final_dedupe_exact_duplicate_pair():
    """两张内容完全相同的表，终扫后只留一张。"""
    from codes.format_corrector.structure_pre_split import dedupe_tables_keep_unique

    rowset = [
        ["", "2025年", "2024年", "本年末比", "2023年"],
        ["资产质量指标(%)", "12月31日", "12月31日", "上年末增减", "12月31日"],
        ["不良贷款率", "0.94", "0.95", "下降0.01个百分点", "0.95"],
        ["拨备覆盖率(1)", "391.79", "411.98", "下降20.19个百分点", "437.70"],
        ["贷款拨备率(2)", "3.68", "3.92", "下降0.24个百分点", "4.14"],
    ]
    a = {"page": 1, "type": "table", "title": "资产质量指标(%)", "data": deepcopy(rowset), "_source_words": [{"text": "x"}] * 20}
    b = {
        "page": 1,
        "type": "table",
        "title": "资产质量指标(%)",
        "data": deepcopy(rowset),
        "_format_structure_split": True,
        "_source_words": [{"text": "y"}] * 5,
    }
    credit = {
        "page": 1,
        "type": "table",
        "title": "信用成本(3)",
        "data": [
            ["", "2025年", "2024年", "本年比上年增减", "2023年"],
            ["信用成本(3)", "0.60", "0.65", "下降0.05个百分点", "0.74"],
        ],
    }
    out, notes = dedupe_tables_keep_unique([a, b, credit])
    assert len(out) == 2, (len(out), notes)
    flats = [" ".join(str(c) for r in (t.get("data") or []) for c in r) for t in out]
    assert sum(1 for f in flats if "不良贷款率" in f) == 1
    assert sum(1 for f in flats if "信用成本" in f) == 1
    # 应保留字框更多的原表
    kept_asset = next(t for t in out if "不良贷款率" in " ".join(str(c) for r in t["data"] for c in r))
    assert not kept_asset.get("_format_structure_split")


def test_strip_leading_page_chrome_rows():
    """表顶公司名/年报标题应剥掉，财务比率表头保留。"""
    from codes.reconstruct.grid_nucleus.word_segment import (
        strip_leading_page_chrome_rows_from_data,
        trim_leading_page_chrome_words,
    )

    data = [
        ["招商银行股份有限公司", "第二章", "会计数据和财务指标摘要", "", "", ""],
        ["2025年度报告（A股）", "", "", "", "", ""],
        ["财务比率(%)", "2025年", "", "2024年", "本年比上年增减", "2023年"],
        ["盈利能力指标", "", "", "", "", ""],
        ["净利差(1)", "1.78", "", "1.86", "下降0.08个百分点", "2.03"],
    ]
    cleaned = strip_leading_page_chrome_rows_from_data(data)
    assert cleaned[0][0] == "财务比率(%)", cleaned[:2]
    assert "招商银行" not in " ".join(str(c) for r in cleaned for c in r)

    words = [
        {"text": "招商银行股份有限公司", "x0": 80, "x1": 180, "y0": 32.8, "y1": 42},
        {"text": "第二章 会计数据和财务指标摘要", "x0": 200, "x1": 360, "y0": 32.5, "y1": 42},
        {"text": "2025年度报告（A股）", "x0": 80, "x1": 180, "y0": 43.3, "y1": 53},
        {"text": "财务比率(%)", "x0": 85, "x1": 140, "y0": 99.9, "y1": 110},
        {"text": "2025年", "x0": 290, "x1": 320, "y0": 99.7, "y1": 110},
    ]
    trimmed = trim_leading_page_chrome_words(words)
    texts = [w["text"] for w in trimmed]
    assert "财务比率(%)" in texts
    assert "2025年" in texts
    assert all("股份有限公司" not in t and "年度报告" not in t for t in texts)
    assert all(not t.startswith("第") for t in texts)


def test_peek_excludes_company_name_as_header():
    """上方探视不得把公司名当表头续行。"""
    from codes.reconstruct.liteparse_anchor import (
        _looks_like_header_continuation,
        _is_page_or_section_title_above,
    )

    assert _is_page_or_section_title_above("招商银行股份有限公司")
    assert not _looks_like_header_continuation("招商银行股份有限公司")
    assert not _looks_like_header_continuation("2025年度报告（A股）")
    assert _looks_like_header_continuation("本年比上年增减")


def test_peel_chrome_from_financial_ratio_table():
    """结构预拆 peel：财务比率表顶页眉行去掉。"""
    from codes.format_corrector.structure_pre_split import _peel_table_footnotes

    t = {
        "page": 1,
        "type": "table",
        "y0": 32.0,
        "y1": 214.0,
        "x0": 56.0,
        "x1": 532.0,
        "data": [
            ["招商银行股份有限公司", "第二章", "会计数据和财务指标摘要", "", "", ""],
            ["2025年度报告（A股）", "", "", "", "", ""],
            ["财务比率(%)", "2025年", "", "2024年", "本年比上年增减", "2023年"],
            ["净利差(1)", "1.78", "", "1.86", "下降0.08个百分点", "2.03"],
        ],
        "_source_words": [
            {"text": "招商银行股份有限公司", "x0": 80, "x1": 180, "y0": 32.8, "y1": 42},
            {"text": "财务比率(%)", "x0": 85, "x1": 140, "y0": 99.9, "y1": 110},
            {"text": "2025年", "x0": 290, "x1": 320, "y0": 99.7, "y1": 110},
            {"text": "净利差(1)", "x0": 85, "x1": 130, "y0": 131, "y1": 141},
            {"text": "1.78", "x0": 300, "x1": 320, "y0": 131, "y1": 141},
        ],
    }
    out, peeled = _peel_table_footnotes(t)
    assert out["data"][0][0] == "财务比率(%)", out["data"][:2]
    assert any("招商银行" in p or "年度报告" in p for p in peeled)
    assert all("股份有限公司" not in str(w.get("text")) for w in out["_source_words"])
    assert float(out["y0"]) > 50, out["y0"]


def test_strip_leading_narrative_revenue_share():
    """营业收入占比表：表前叙述残句与「下表列出…」应剥掉。"""
    from codes.format_corrector.structure_pre_split import _peel_table_footnotes
    from codes.reconstruct.grid_nucleus.word_segment import (
        strip_leading_non_table_rows_from_data,
    )
    from codes.table_engine.split.row_classify import is_inter_table_narrative_row

    assert is_inter_table_narrative_row(["占比36.13%。", "", "", ""])
    assert is_inter_table_narrative_row(
        ["下表列出本集团近三年营业收入构成的占比情况。", "", "", ""]
    )
    assert not is_inter_table_narrative_row(["(%)", "2025年", "2024年", "2023年"])
    assert not is_inter_table_narrative_row(
        ["净利息收入", "63.87", "62.60", "63.30"]
    )

    data = [
        ["占比36.13%。", "", "", ""],
        ["下表列出本集团近三年营业收入构成的占比情况。", "", "", ""],
        ["(%)", "2025年", "2024年", "2023年"],
        ["净利息收入", "63.87", "62.60", "63.30"],
        ["净手续费及佣金收入", "22.30", "21.36", "24.80"],
        ["其他净收入", "13.83", "16.04", "11.90"],
        ["合计", "100.00", "100.00", "100.00"],
    ]
    cleaned = strip_leading_non_table_rows_from_data(data)
    assert cleaned[0][0] == "(%)", cleaned[:2]
    assert "占比36" not in " ".join(str(c) for r in cleaned for c in r)
    assert "下表列出" not in " ".join(str(c) for r in cleaned for c in r)

    t = {
        "page": 1,
        "type": "table",
        "y0": 200.0,
        "y1": 400.0,
        "x0": 50.0,
        "x1": 500.0,
        "data": data,
        "_source_words": [
            {"text": "占比36.13%。", "x0": 80, "x1": 160, "y0": 200, "y1": 212},
            {
                "text": "下表列出本集团近三年营业收入构成的占比情况。",
                "x0": 80,
                "x1": 400,
                "y0": 220,
                "y1": 232,
            },
            {"text": "(%)", "x0": 80, "x1": 100, "y0": 250, "y1": 262},
            {"text": "2025年", "x0": 180, "x1": 220, "y0": 250, "y1": 262},
            {"text": "净利息收入", "x0": 80, "x1": 140, "y0": 280, "y1": 292},
            {"text": "63.87", "x0": 180, "x1": 220, "y0": 280, "y1": 292},
        ],
    }
    out, peeled = _peel_table_footnotes(t)
    assert out["data"][0][0] == "(%)", out["data"][:2]
    assert any("占比" in p or "下表列出" in p for p in peeled)
    texts = [str(w.get("text")) for w in out["_source_words"]]
    assert all("下表列出" not in t and "占比36" not in t for t in texts), texts
    assert float(out["y0"]) >= 240, out["y0"]


def test_strip_trailing_avg_belongs_to_next_table():
    """生息资产表合计后的「平均」「平均」是下一张计息负债表表头，应剥掉。"""
    from codes.format_corrector.structure_pre_split import _peel_table_footnotes
    from codes.reconstruct.grid_nucleus.word_segment import (
        strip_trailing_next_table_header_rows_from_data,
    )
    from codes.table_engine.split.row_classify import is_prependable_header_band_row

    avg_row = ["", "", "平均", "", "", "平均", ""]
    assert is_prependable_header_band_row(avg_row)

    data = [
        ["", "2025年", "", "", "2024年", "", ""],
        ["", "", "平均", "", "", "平均", ""],
        [
            "（人民币百万元，百分比除外）",
            "平均余额",
            "利息收入",
            "收益率%",
            "平均余额",
            "利息收入",
            "收益率%",
        ],
        ["生息资产", "", "", "", "", "", ""],
        ["贷款和垫款", "6,994,961", "233,547", "3.34", "6,666,187", "260,573", "3.91"],
        ["合计", "11,558,068", "351,351", "3.04", "10,686,141", "374,271", "3.50"],
        avg_row,
    ]
    cleaned = strip_trailing_next_table_header_rows_from_data(data)
    assert cleaned[-1][0] == "合计", cleaned[-2:]
    # 表头里的「平均」可保留；表体后误挂的那一行必须去掉
    assert len(cleaned) == len(data) - 1, (len(cleaned), len(data), cleaned[-3:])
    assert [str(c).strip() for c in cleaned[-1] if str(c).strip()][0] == "合计"

    t = {
        "page": 1,
        "type": "table",
        "y0": 100.0,
        "y1": 500.0,
        "x0": 50.0,
        "x1": 500.0,
        "data": data,
        "_source_words": [
            {"text": "合计", "x0": 80, "x1": 110, "y0": 400, "y1": 412},
            {"text": "11,558,068", "x0": 150, "x1": 210, "y0": 400, "y1": 412},
            {"text": "平均", "x0": 200, "x1": 230, "y0": 430, "y1": 442},
            {"text": "平均", "x0": 380, "x1": 410, "y0": 430, "y1": 442},
        ],
    }
    out, peeled = _peel_table_footnotes(t)
    assert out["data"][-1][0] == "合计", out["data"][-2:]
    assert len(out["data"]) == len(data) - 1
    # 尾部「平均」字框应裁掉（表头「平均」不在本用例字框里）
    assert all(str(w.get("text")) != "平均" for w in out["_source_words"]), out[
        "_source_words"
    ]
    assert float(out["y1"]) < 430, out["y1"]


def test_peek_rejects_prev_table_total_row():
    """上方探视不得把上一表「合计」+金额当表头续行。"""
    from codes.reconstruct.liteparse_anchor import (
        _looks_like_header_continuation,
        attach_liteparse_words,
    )

    assert not _looks_like_header_continuation("合计")
    assert not _looks_like_header_continuation("11,835,865")
    assert not _looks_like_header_continuation("2.92")
    assert _looks_like_header_continuation("本年比上年")

    words = [
        {"text": "合计", "x0": 85, "y0": 509, "x1": 110, "y1": 520},
        {"text": "11,835,865", "x0": 200, "y0": 508, "x1": 260, "y1": 520},
        {"text": "87,044", "x0": 280, "y0": 508, "x1": 320, "y1": 520},
        {"text": "年化平均", "x0": 200, "y0": 533, "x1": 240, "y1": 544},
        {"text": "年化平均", "x0": 380, "y0": 533, "x1": 420, "y1": 544},
        {"text": "平均余额", "x0": 180, "y0": 547, "x1": 220, "y1": 558},
        {"text": "利息支出", "x0": 250, "y0": 547, "x1": 290, "y1": 558},
        {"text": "成本率%", "x0": 320, "y0": 547, "x1": 360, "y1": 558},
        {"text": "客户存款", "x0": 85, "y0": 578, "x1": 140, "y1": 590},
        {"text": "9,494,649", "x0": 180, "y0": 578, "x1": 240, "y1": 590},
    ]
    table = {
        "type": "table",
        "page": 1,
        "x0": 80.0,
        "y0": 533.2,
        "x1": 533.0,
        "y1": 693.0,
        "data": [["年化平均"]],
    }
    liteparse = {
        "pages": [{
            "page_number": 1,
            "height": 842,
            "text_items": words,
            "table_regions": [
                {"x0": 71.0, "y0": 546.0, "x1": 543.0, "y1": 693.0}
            ],
        }]
    }
    attach_liteparse_words(table, liteparse_data=liteparse)
    peek = (table.get("_liteparse_anchor") or {}).get("peek_header_above") or []
    assert "合计" not in peek and "11,835,865" not in peek, peek
    texts = [str(w.get("text")) for w in table.get("_source_words") or []]
    assert "合计" not in texts and "11,835,865" not in texts, texts


def test_cross_table_strip_asset_total_from_liability():
    """资产表合计不得再出现在负债表首行。"""
    from codes.format_corrector.structure_pre_split import (
        expand_tables_with_structure_split,
        strip_cross_table_leading_duplicates,
    )

    asset = {
        "page": 1,
        "type": "table",
        "title": "生息资产",
        "y0": 387.0,
        "y1": 519.0,
        "data": [
            ["", "", "2025年10-12月", "", "", "2025年7-9月", ""],
            ["生息资产", "", "", "", "", "", ""],
            ["贷款和垫款", "7,063,820", "57,138", "3.21", "7,004,000", "57,329", "3.25"],
            ["合计", "11,835,865", "87,044", "2.92", "11,672,682", "87,293", "2.97"],
        ],
        "_source_words": [{"text": "合计", "x0": 80, "y0": 509, "x1": 110, "y1": 520}],
    }
    liability = {
        "page": 1,
        "type": "table",
        "title": "计息负债",
        "y0": 533.0,
        "y1": 693.0,
        "data": [
            ["合计", "11,835,865", "87,044", "2.92", "11,672,682", "87,293", "2.97"],
            ["", "", "", "年化平均", "", "", "年化平均"],
            [
                "（人民币百万元，百分比除外）",
                "平均余额",
                "利息支出",
                "成本率%",
                "平均余额",
                "利息支出",
                "成本率%",
            ],
            ["计息负债", "", "", "", "", "", ""],
            ["客户存款", "9,494,649", "25,014", "1.05", "9,229,562", "26,336", "1.13"],
        ],
        "_source_words": [
            {"text": "合计", "x0": 80, "y0": 509, "x1": 110, "y1": 520},
            {"text": "11,835,865", "x0": 200, "y0": 508, "x1": 260, "y1": 520},
            {"text": "年化平均", "x0": 200, "y0": 533, "x1": 240, "y1": 544},
            {"text": "客户存款", "x0": 85, "y0": 578, "x1": 140, "y1": 590},
        ],
    }
    out, notes = strip_cross_table_leading_duplicates([asset, liability])
    assert any("跨表去重" in n for n in notes), notes
    assert out[1]["data"][0][3] == "年化平均" or "年化平均" in "".join(
        str(c) for c in out[1]["data"][0]
    ), out[1]["data"][:2]
    assert out[1]["data"][0][0] != "合计", out[1]["data"][:2]
    assert all(str(w.get("text")) != "合计" for w in out[1]["_source_words"]), out[1][
        "_source_words"
    ]

    exp, exp_notes = expand_tables_with_structure_split([asset, liability])
    liab = next(t for t in exp if "客户存款" in " ".join(str(c) for r in t["data"] for c in r))
    assert liab["data"][0][0] != "合计", liab["data"][:2]


if __name__ == "__main__":
    test_year_header_cuts_two_tables()
    test_structure_split_narrows_source_words()
    test_grid_after_split_keeps_year_columns()
    test_wide_footnote_does_not_merge_year_slots()
    test_trim_footnote_words_keeps_credit_only()
    test_strip_footnote_rows_from_credit_data()
    test_expand_peels_footnotes_from_credit_part()
    test_assign_words_to_parts_order()
    test_split_parts_get_distinct_geometry_and_no_dup_after_reattach()
    test_asset_only_data_but_words_have_credit_promotes_table()
    test_split_sets_title_on_credit_part()
    test_skip_duplicate_asset_keep_credit()
    test_final_dedupe_exact_duplicate_pair()
    test_strip_leading_page_chrome_rows()
    test_peek_excludes_company_name_as_header()
    test_peel_chrome_from_financial_ratio_table()
    test_strip_leading_narrative_revenue_share()
    test_strip_trailing_avg_belongs_to_next_table()
    test_peek_rejects_prev_table_total_row()
    test_cross_table_strip_asset_total_from_liability()
    print("OK")
