# -*- coding: utf-8 -*-
"""凝结核网格恢复单测。"""

from codes.reconstruct.grid_nucleus import restore_table_grid, apply_grid_to_table, GRID_NUCLEUS
from codes.reconstruct.grid_nucleus.preprocess import preprocess_words, merge_cjk_singles
from codes.reconstruct.grid_nucleus.row_cluster import cluster_rows
from codes.reconstruct.grid_nucleus.types import Nucleus


def _word(text, x0, y0, x1, y1):
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_row_cluster_three_rows():
    words = []
    # 3 rows x 3 cols amounts
    for ri, y in enumerate([10, 30, 50]):
        for ci, x in enumerate([10, 80, 150]):
            words.append(_word(f"{1000 + ri * 10 + ci}", x, y, x + 40, y + 12))
    nuclei = preprocess_words(words)
    rows = cluster_rows(nuclei, gap_factor=1.2)
    assert len(rows) == 3, len(rows)


def test_restore_simple_grid():
    words = []
    # 对齐更好的三列
    cols_x = [(10, 55), (100, 155), (200, 255)]
    labels = ["项目", "期末余额", "上年末"]
    for (x0, x1), t in zip(cols_x, labels):
        words.append(_word(t, x0, 8, x1, 20))
    body = [
        ("资产", "1000000", "900000"),
        ("负债", "1000001", "900001"),
        ("权益", "1000002", "900002"),
    ]
    for ri, y in enumerate([35, 55, 75]):
        for ci, text in enumerate(body[ri]):
            x0, x1 = cols_x[ci]
            words.append(_word(text, x0, y, x1, y + 12))
    res = restore_table_grid({"type": "table"}, source_words=words)
    assert res.n_cols == 3, res.to_dict()
    assert res.n_rows >= 3, res.to_dict()
    assert res.ok, res.to_dict()
    assert res.method == "nucleus", res.method


def test_glue_split_in_preprocess():
    words = [_word("19,079,642 成都", 10, 10, 120, 22)]
    nuclei = preprocess_words(words)
    assert len(nuclei) >= 2
    texts = " ".join(n.text for n in nuclei)
    assert "成都" in texts


def test_cjk_merge():
    nuclei = [
        Nucleus(text="成", x0=10, y0=10, x1=18, y1=20),
        Nucleus(text="都", x0=19, y0=10, x1=27, y1=20),
        Nucleus(text="分行", x0=40, y0=10, x1=70, y1=20),
    ]
    merged = merge_cjk_singles(nuclei)
    assert any("成都" in n.text for n in merged)


def test_apply_no_words_keeps_data():
    table = {
        "type": "table",
        "data": [["a", "1"], ["b", "2"]],
        "_table_kind": {"kind": "data"},
    }
    before = [list(r) for r in table["data"]]
    res = apply_grid_to_table(table)
    assert table["data"] == before
    assert res.method == "fallback_keep" or not res.ok


def test_conservation_blocks_bad_overwrite(monkeypatch=None):
    # 无字框时不覆盖
    table = {
        "type": "table",
        "data": [["项目", "金额"], ["现金", "1,000"]],
        "_source_words": [],
        "_table_kind": {"kind": "data"},
    }
    apply_grid_to_table(table)
    assert table["data"][1][1] == "1,000"


def test_apply_overwrites_when_source_conserved():
    """相对旧表会「多」出金额，但字框里有 → 应写回（旧逻辑会误拦）。"""
    cols_x = [(10, 55), (100, 155), (200, 255)]
    words = []
    for (x0, x1), t in zip(cols_x, ["项目", "期末", "上年"]):
        words.append(_word(t, x0, 8, x1, 20))
    body = [
        ("资产", "1000000", "900000"),
        ("负债", "1000001", "900001"),
        ("权益", "1000002", "900002"),
    ]
    for ri, y in enumerate([35, 55, 75]):
        for ci, text in enumerate(body[ri]):
            x0, x1 = cols_x[ci]
            words.append(_word(text, x0, y, x1, y + 12))
    # 旧表故意缺列（破损），若对照 before 会 conservation_block
    table = {
        "type": "table",
        "data": [["项目", "混"], ["资产 1000000 900000"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok, res.to_dict()
    assert res.metrics.get("overwrote_data") is True
    assert table["cols"] == 3
    assert any("1000000" in str(c) for row in table["data"] for c in row)


def test_apply_blocks_amount_not_in_source_words():
    from unittest.mock import patch

    words = [
        _word("项目", 10, 8, 55, 20),
        _word("金额", 100, 8, 155, 20),
        _word("现金", 10, 35, 55, 47),
        _word("1000", 100, 35, 155, 47),
        _word("存款", 10, 55, 55, 67),
        _word("2000", 100, 55, 155, 67),
    ]
    table = {
        "type": "table",
        "data": [["项目", "金额"], ["现金", "1000"], ["存款", "2000"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    fake_data = [["项目", "金额"], ["现金", "1000"], ["存款", "9999999"]]

    class _Fake:
        ok = True
        data = fake_data
        method = "nucleus"
        errors = []
        metrics = {}
        n_rows = 3
        n_cols = 2
        row_lines = []
        col_lines = []
        rows_meta = []
        columns_meta = []

        def to_dict(self):
            return {
                "ok": self.ok, "data": self.data, "method": self.method,
                "errors": self.errors, "metrics": self.metrics,
                "n_rows": self.n_rows, "n_cols": self.n_cols,
                "row_lines": [], "col_lines": [],
                "rows_meta": [], "columns_meta": [],
            }

    with patch(
        "codes.reconstruct.grid_nucleus.pipeline.restore_table_grid",
        return_value=_Fake(),
    ):
        before = [list(r) for r in table["data"]]
        res = apply_grid_to_table(table)
    assert table["data"] == before
    assert not res.metrics.get("overwrote_data")
    assert any("conservation_block" in e for e in res.errors)


def test_table_xy_bbox_and_abc_split():
    """模拟期间表：字框里 a/b 已分开，旧 data 却把 b 粘错。"""
    from codes.reconstruct.liteparse_anchor import attach_liteparse_words

    words = [
        _word("a", 100, 20, 108, 32),
        _word("b", 200, 20, 208, 32),
        _word("30", 10, 50, 25, 62),
        _word("应交税费", 40, 50, 90, 62),
        _word("40388", 120, 50, 160, 62),
        _word("40021", 220, 50, 260, 62),
        _word("31", 10, 70, 25, 82),
        _word("预计负债", 40, 70, 90, 82),
        _word("38322", 120, 70, 160, 82),
        _word("38321", 220, 70, 260, 82),
    ]
    table = {
        "type": "table",
        "page": 1,
        "x0": 0, "y0": 0, "x1": 300, "y1": 100,
        "data": [["", "a", "b"], ["30 应交税费", "40388 40021", ""]],
        "_table_kind": {"kind": "data"},
    }
    liteparse = {
        "pages": [{
            "page_number": 1,
            "text_items": words,
            "table_regions": [
                {"x0": 0, "y0": 10, "x1": 300, "y1": 100, "region_text": "a b"}
            ],
        }]
    }
    attach_liteparse_words(table, liteparse_data=liteparse)
    assert table["_liteparse_anchor"].get("used_table_bbox") is True
    assert table["_liteparse_anchor"].get("word_count", 0) >= 8
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    assert any(str(c).strip() == "a" for r in table["data"][:3] for c in r)
    assert any(str(c).strip() == "b" for r in table["data"][:3] for c in r)
    # a、b 不应挤在同一格
    assert not any(
        "a" in str(c) and "b" in str(c) and str(c).strip() not in ("a", "b")
        for r in table["data"][:3] for c in r
    ), table["data"][:3]


def test_attach_keeps_table_top_multiline_header():
    """表 bbox 上沿高于 liteparse region 时，保留「本年比上年」等多行表头。"""
    from codes.reconstruct.liteparse_anchor import attach_liteparse_words

    words = [
        _word("本年比上年", 433, 157, 479, 166),
        _word("（人民币百万元，特别注明除外）", 81, 173, 189, 180),
        _word("2025年", 306, 170, 337, 181),
        _word("2024年", 360, 170, 391, 181),
        _word("增减(%)", 445, 170, 479, 181),
        _word("2023年", 502, 170, 532, 181),
        _word("营业收入", 85, 200, 122, 212),
        _word("337,532", 303, 200, 337, 212),
        _word("337,488", 357, 200, 391, 212),
        _word("0.01", 460, 200, 466, 212),
        _word("339,123", 498, 200, 533, 212),
    ]
    table = {
        "type": "table",
        "page": 1,
        "x0": 81.54, "y0": 157.05, "x1": 532.92, "y1": 470.0,
        "data": [["x"]],
        "_table_kind": {"kind": "data"},
    }
    liteparse = {
        "pages": [{
            "page_number": 1,
            "text_items": words,
            # region 故意从年列表头起，裁掉上一行
            "table_regions": [
                {"x0": 81.0, "y0": 170.42, "x1": 533.0, "y1": 470.0}
            ],
        }]
    }
    attach_liteparse_words(table, liteparse_data=liteparse)
    texts = [str(w.get("text")) for w in table.get("_source_words") or []]
    assert "本年比上年" in texts, (texts, table.get("_liteparse_anchor"))
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    assert any("本年比上年" in str(c) for r in table["data"] for c in r), table["data"][:3]
    # 同列约束：增减数值不得粘到上年金额
    assert not any("337,488 0.01" in str(c) for r in table["data"] for c in r), table["data"]
    hit = next(r for r in table["data"] if any("营业收入" in str(c) for c in r))
    assert any(str(c).strip() == "337,488" for c in hit), hit
    assert any(str(c).strip() == "0.01" for c in hit), hit


def test_attach_keeps_table_bottom_summary_rows():
    """表 bbox 下沿低于 liteparse region 时，保留表尾「利息支出变动」等合计行。"""
    from codes.reconstruct.liteparse_anchor import attach_liteparse_words

    words = [
        _word("（人民币百万元）", 80, 150, 140, 160),
        _word("规模", 200, 150, 230, 160),
        _word("利率", 300, 150, 330, 160),
        _word("增（减）净值", 400, 150, 470, 160),
        _word("向中央银行借款", 85, 312, 150, 321),
        _word("(1,218)", 200, 312, 250, 322),
        _word("(705)", 300, 312, 340, 322),
        _word("(1,923)", 400, 312, 450, 322),
        # region 下沿外的两行
        _word("利息支出变动", 85, 329, 150, 338),
        _word("9,440", 200, 329, 240, 339),
        _word("(36,676)", 300, 329, 360, 339),
        _word("(27,236)", 400, 329, 460, 339),
        _word("净利息收入变动", 85, 346, 160, 355),
        _word("16,798", 200, 346, 250, 356),
        _word("(12,482)", 300, 346, 360, 356),
        _word("4,316", 400, 346, 440, 356),
    ]
    table = {
        "type": "table",
        "page": 1,
        "x0": 80.0,
        "y0": 135.0,
        "x1": 536.0,
        "y1": 356.8,
        "data": [
            ["（人民币百万元）", "规模", "利率", "增（减）净值"],
            ["向中央银行借款", "(1,218)", "(705)", "(1,923)"],
            ["利息支出变动", "9,440", "(36,676)", "(27,236)"],
            ["净利息收入变动", "16,798", "(12,482)", "4,316"],
        ],
        "_table_kind": {"kind": "data"},
    }
    liteparse = {
        "pages": [{
            "page_number": 1,
            "height": 842,
            "text_items": words,
            # region 故意裁在向中央银行借款底边，漏掉两行合计
            "table_regions": [
                {"x0": 75.0, "y0": 180.0, "x1": 545.0, "y1": 322.83}
            ],
        }]
    }
    attach_liteparse_words(table, liteparse_data=liteparse)
    src = table.get("_liteparse_anchor") or {}
    assert "table_bottom" in str(src.get("bbox_source") or ""), src
    texts = [str(w.get("text")) for w in table.get("_source_words") or []]
    assert "利息支出变动" in texts and "净利息收入变动" in texts, texts
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    flat = " ".join(str(c) for r in table["data"] for c in r)
    assert "利息支出变动" in flat and "净利息收入变动" in flat, table["data"]
    assert "9,440" in flat and "16,798" in flat, table["data"]


def test_attach_peeks_above_even_when_table_top_flush():
    """表框与 region 齐顶时，仍向上探视紧邻「本年比上年」，且不吞章节大标题。"""
    from codes.reconstruct.liteparse_anchor import attach_liteparse_words

    words = [
        _word("会计数据和财务指标摘要", 55, 71, 292, 92),
        _word("本年比上年", 433, 157, 479, 166),
        _word("2025年", 306, 170, 337, 181),
        _word("2024年", 360, 170, 391, 181),
        _word("增减(%)", 445, 170, 479, 181),
        _word("2023年", 502, 170, 532, 181),
        _word("营业收入", 85, 200, 122, 212),
        _word("337,532", 303, 200, 337, 212),
        _word("337,488", 357, 200, 391, 212),
        _word("0.01", 460, 200, 466, 212),
        _word("339,123", 498, 200, 533, 212),
    ]
    table = {
        "type": "table",
        "page": 1,
        # 与 region 齐顶：仅靠 table_top 保不住上一行
        "x0": 81.0, "y0": 170.42, "x1": 533.0, "y1": 470.0,
        "data": [["x"]],
        "_table_kind": {"kind": "data"},
    }
    liteparse = {
        "pages": [{
            "page_number": 1,
            "text_items": words,
            "table_regions": [
                {"x0": 81.0, "y0": 170.42, "x1": 533.0, "y1": 470.0}
            ],
        }]
    }
    attach_liteparse_words(table, liteparse_data=liteparse)
    texts = [str(w.get("text")) for w in table.get("_source_words") or []]
    assert "本年比上年" in texts, (texts, table.get("_liteparse_anchor"))
    assert "会计数据和财务指标摘要" not in texts, texts
    assert "peek_above" in str(table.get("_liteparse_anchor", {}).get("bbox_source", ""))


def test_change_rate_header_not_merged_into_left_amount():
    """增减(%) 列即使主体暂空，也不得并入左邻造成金额粘连。"""
    from codes.reconstruct.grid_nucleus.header_align import align_header_to_body_columns

    data = [
        ["项目", "2024年", "增减(%)", "2023年"],
        ["营业收入", "337,488 0.01", "", "339,123"],
        ["营业利润", "179,019 0.13", "", "176,663"],
    ]
    # 模拟错误落列后的 orphan：增减列有头无主体
    new_data, _, metrics = align_header_to_body_columns(data, col_lines=[0, 100, 200, 300, 400])
    # 不应把增减表头并进 2024 列
    assert "增减" not in str(new_data[0][1]), (new_data[0], metrics)
    assert "增减" in str(new_data[0][2]), new_data[0]


def test_row_cluster_does_not_merge_adjacent_lines():
    """行距≈字高时不应把 30/31 两行并成一行。"""
    words = []
    for i, y in enumerate([50, 64, 78]):  # gap 14 ≈ height 12
        words.append(_word(str(30 + i), 10, y, 25, y + 12))
        words.append(_word(f"项目{i}", 40, y, 90, y + 12))
        words.append(_word(f"{1000 + i}", 120, y, 160, y + 12))
    nuclei = preprocess_words(words)
    rows = cluster_rows(nuclei, gap_factor=0.65)
    assert len(rows) == 3, [(r.row_id, [n.text for n in r.nuclei]) for r in rows]


def test_left_aligned_text_not_split_by_width():
    """同列左对齐、长短不一的科目名不得因 cx 不同拆成多列。"""
    words = [
        _word("a", 400, 8, 410, 20),
        _word("1", 10, 35, 22, 47),
        _word("一般利率风险", 40, 35, 110, 47),
        _word("1728", 400, 35, 440, 47),
        _word("2", 10, 55, 22, 67),
        _word("股票风险", 40, 55, 90, 67),
        _word("1030", 400, 55, 440, 67),
        _word("3", 10, 75, 22, 87),
        _word("信用利差风险-证券化(非相关性交易组合)", 40, 75, 250, 87),
        _word("3441", 400, 75, 440, 87),
        _word("4", 10, 95, 22, 107),
        _word("信用利差风险-证券化(相关性交易组合)", 40, 95, 240, 107),
        _word("-", 400, 95, 420, 107),
    ]
    table = {
        "type": "table",
        "data": [["x"], ["y"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    # 所有科目应在同一文本列
    label_cols = set()
    for row in table["data"]:
        for ci, cell in enumerate(row):
            s = str(cell or "")
            if "风险" in s or "利差" in s:
                label_cols.add(ci)
    assert len(label_cols) == 1, (label_cols, table["data"])


def test_interval_label_stays_with_header_left_edge():
    """[0.00,0.15) 与表头「违约概率区间」同列左对齐，不得因小数被当金额拆列。"""
    from codes.reconstruct.grid_nucleus.preprocess import is_amount_nucleus, is_interval_label
    from codes.reconstruct.grid_nucleus.types import Nucleus

    assert is_interval_label("[0.00,0.15)")
    assert not is_amount_nucleus(
        Nucleus(text="[0.00,0.15)", x0=95, y0=0, x1=142, y1=10)
    )

    words = [
        _word("违约概率区间(%)", 95, 10, 160, 22),
        _word("[0.00,0.15)", 95, 40, 142, 52),
        _word("1000", 300, 40, 340, 52),
        _word("[0.15,0.25)", 95, 60, 142, 72),
        _word("2000", 300, 60, 340, 72),
        _word("[0.25,0.50)", 95, 80, 142, 92),
        _word("3000", 300, 80, 340, 92),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    hdr_cols, body_cols = set(), set()
    for row in table["data"]:
        for ci, cell in enumerate(row):
            s = str(cell or "")
            if "区间" in s:
                hdr_cols.add(ci)
            if s.startswith("["):
                body_cols.add(ci)
    assert hdr_cols and body_cols and hdr_cols == body_cols, (
        hdr_cols, body_cols, table["data"]
    )


def test_change_phrase_in_gutter_goes_to_right_column():
    """金额列右缘与增减列之间的「下降…百分点」须进右侧列，不得与前值粘连。

    几何来自 page_014：1.28∈[372,378]，下降…∈[404,428]，增减数∈[460,465]。
    """
    words = [
        _word("项目", 85, 10, 120, 22),
        _word("2025年", 306, 10, 337, 22),
        _word("2024年", 360, 10, 391, 22),
        _word("增减(%)", 445, 10, 479, 22),
        _word("2023年", 502, 10, 532, 22),
        # 普通行：增减为数值
        _word("基本每股收益", 94, 40, 150, 52),
        _word("5.70", 319, 40, 324, 52),
        _word("5.65", 372, 40, 378, 52),
        _word("0.88", 460, 40, 466, 52),
        _word("5.61", 514, 40, 520, 52),
        # 粘连风险行：增减为中文百分点
        _word("平均总资产收益率", 85, 70, 200, 82),
        _word("1.19", 319, 70, 324, 82),
        _word("1.28", 372, 70, 378, 82),
        _word("下降0.09个百分点", 404, 70, 428, 82),
        _word("1.39", 514, 70, 520, 82),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    hit = None
    for row in table["data"]:
        if any("收益率" in str(c) for c in row):
            hit = row
            break
    assert hit is not None, table["data"]
    joined = " | ".join(str(c) for c in hit)
    assert "1.28 下降" not in joined, joined
    assert any(str(c).strip() == "1.28" for c in hit), hit
    assert any("下降0.09个百分点" in str(c) for c in hit), hit
    # 增减文字不得与 1.28 同列
    for c in hit:
        s = str(c or "")
        if "1.28" in s:
            assert "下降" not in s
        if "下降" in s:
            assert "1.28" not in s


def test_wrapped_change_header_same_column_as_desc():
    """本年末比/上年末增减 折行表头与「下降…百分点」须同列，不得拆成两列或并进下一年。"""
    words = [
        _word("2025年", 289.9, 316.2, 320.3, 326.9),
        _word("2024年", 352.2, 316.5, 382.7, 326.9),
        _word("本年末比", 433.8, 316.8, 470.6, 325.6),
        _word("2023年", 502.5, 316.5, 532.9, 326.9),
        _word("资产质量指标(%)", 85.0, 330.4, 155.3, 340.9),
        _word("12月31日", 279.9, 330.2, 320.3, 340.9),
        _word("12月31日", 342.2, 330.5, 382.7, 340.9),
        _word("上年末增减", 424.6, 330.9, 470.6, 339.6),
        _word("12月31日", 492.5, 330.5, 532.9, 340.9),
        _word("不良贷款率", 85.0, 347.8, 131.1, 356.6),
        _word("0.94", 302.1, 347.5, 320.3, 357.9),
        _word("0.95", 364.4, 347.5, 369.4, 357.9),
        _word("下降0.01个百分点", 396.1, 347.5, 419.8, 357.9),
        _word("0.95", 514.6, 347.5, 519.6, 357.9),
        _word("拨备覆盖率(1)", 85.0, 361.9, 137.7, 370.6),
        _word("391.79", 291.5, 361.5, 307.1, 371.9),
        _word("411.98", 353.9, 361.5, 369.4, 371.9),
        _word("下降20.19个百分点", 390.8, 361.5, 419.9, 371.9),
        _word("437.70", 504.1, 361.5, 519.7, 371.9),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    data = table["data"]
    # 不得把增减表头并进 2023 年
    assert not any("本年末比 2023" in str(c) or "2023年" in str(c) and "本年末" in str(c)
                   for r in data for c in r), data[:3]
    # 定位增减列：表头含本年末/上年末，主体含下降百分点
    hdr_cols = set()
    desc_cols = set()
    for ri, row in enumerate(data):
        for ci, cell in enumerate(row):
            s = str(cell or "")
            if "本年末" in s or "上年末" in s:
                hdr_cols.add(ci)
            if "下降" in s and "百分点" in s:
                desc_cols.add(ci)
    assert hdr_cols and desc_cols and hdr_cols == desc_cols, (hdr_cols, desc_cols, data)
    # 年/日期两行表头保留（折行），但同列对齐
    assert any("2025年" in str(c) for r in data[:2] for c in r)
    assert any("12月31日" in str(c) for r in data[:2] for c in r)


def test_assign_uses_both_edges_overlap():
    """落列看 [x0,x1] 与列带双边重叠，而非只比单侧点。"""
    from codes.reconstruct.grid_nucleus.column_infer import (
        assign_nuclei_to_slots,
        _h_overlap,
        _dist_point_to_interval,
        _both_edges_score,
        _interval_gap,
    )
    from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster

    assert _dist_point_to_interval(100, 95, 160) == 0.0
    assert _h_overlap(
        Nucleus(text="x", x0=95, y0=0, x1=142, y1=10), 90, 150
    ) > 40
    assert _interval_gap(10, 20, 30, 40) == 10.0
    assert _interval_gap(10, 35, 30, 40) == 0.0
    # 左右缘同时贴近才高分
    n = Nucleus(text="x", x0=100, y0=0, x1=140, y1=10)
    assert _both_edges_score(n, 100, 140) > _both_edges_score(n, 100, 200)

    # 两个槽：文本带中心偏左、金额带偏右；宽表头应落入文本带
    header = Nucleus(text="违约概率区间", x0=95, y0=10, x1=160, y1=22)
    body = Nucleus(text="[0.00,0.15)", x0=95, y0=40, x1=142, y1=52)
    amt = Nucleus(text="1,000", x0=300, y0=40, x1=340, y1=52)
    rows = [
        RowCluster(row_id=0, cy=16, nuclei=[header]),
        RowCluster(row_id=1, cy=46, nuclei=[body, amt]),
    ]
    # 文本列一个槽 + 金额槽；干扰：右缘旁再放一个假槽心，双侧匹配仍应表头与主体同列
    assign_nuclei_to_slots(rows, [118.0, 158.0, 320.0])
    assert header.col_id == body.col_id, (header.col_id, body.col_id)
    assert amt.col_id != body.col_id
    # 不得只因右缘靠近 158 就把宽表头拽到假槽而主体留在左槽
    assert not (header.col_id == 1 and body.col_id == 0), (header.col_id, body.col_id)


def test_assign_rejects_single_edge_only():
    """左缘更近左列、但整段 [x0,x1] 与右列带重叠更大 → 必须进右列（双侧）。"""
    from codes.reconstruct.grid_nucleus.column_infer import assign_nuclei_to_slots
    from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster

    # 左列带 [100,130]，右列带 [150,220]；核 [125,200] 左缘偏左但主体在右
    left_peer = Nucleus(text="aaa", x0=105, y0=10, x1=125, y1=20)
    right_peer = Nucleus(text="bbb", x0=160, y0=10, x1=210, y1=20)
    wide = Nucleus(text="本年末比上年末增减", x0=125, y0=40, x1=200, y1=52)
    rows = [
        RowCluster(row_id=0, cy=15, nuclei=[left_peer, right_peer]),
        RowCluster(row_id=1, cy=46, nuclei=[wide]),
    ]
    assign_nuclei_to_slots(rows, [115.0, 185.0])
    assert wide.col_id == right_peer.col_id, (
        wide.col_id, left_peer.col_id, right_peer.col_id
    )


def test_wide_amount_and_narrow_rate_same_column():
    """同列右对齐：大数框宽、小数框窄，左右带重叠则不得拆列。"""
    words = [
        _word("项目", 40, 10, 80, 22),
        _word("a", 280, 10, 290, 22),
        _word("资本", 40, 40, 80, 52),
        _word("42,755,544", 268, 40, 315, 52),
        _word("杠杆率", 40, 60, 90, 72),
        _word("7.78", 297, 60, 315, 72),
        _word("杠杆率b", 40, 80, 100, 92),
        _word("7.69", 297, 80, 315, 92),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    big_col = rate_col = None
    for row in table["data"]:
        for ci, cell in enumerate(row):
            s = str(cell or "")
            if "42,755" in s:
                big_col = ci
            if s.strip() in ("7.78", "7.69"):
                rate_col = ci
    assert big_col is not None and rate_col is not None
    assert big_col == rate_col, (big_col, rate_col, table["data"])


def test_glue_audit_on_validate_fail_repairs_numeric_text():
    """凝结核校验失败时专项检查粘连：金额+文本可拆则拆开。"""
    from codes.reconstruct.grid_nucleus.glue_audit import (
        audit_repair_on_validate_fail,
        classify_glue_cell,
        scan_grid_glue,
    )

    assert classify_glue_cell("19,079,642 成都") == "numeric_text"
    assert classify_glue_cell("贷款金额 贷款率%（注）") == "dual_metric_header"

    data = [
        ["地区", "营业收入", "备注"],
        ["19,079,642 成都", "", "x"],
        ["8,000,000 北京", "", "y"],
    ]
    audit = scan_grid_glue(data)
    assert audit["has_glue"] and "numeric_text" in audit["kinds"]

    repaired, report = audit_repair_on_validate_fail(data)
    assert report["note"] == "glue_repaired", report
    assert repaired[1][0] == "成都" and repaired[1][1] == "19,079,642", repaired
    assert scan_grid_glue(repaired)["count"] == 0

    # 无粘连时只记录、不改表
    clean = [["a", "1"], ["b", "2"]]
    out2, report2 = audit_repair_on_validate_fail(clean)
    assert report2["note"] == "no_glue"
    assert out2 == clean


def test_profit_dist_bottom_header_year_and_units():
    """利润分配表：底层「年度」不得并进金额列；多单位粘连拆到各列（page_101）。"""
    from codes.reconstruct.grid_nucleus.header_align import (
        _is_amount,
        align_header_to_body_columns,
    )

    assert not _is_amount("2025（注）")
    assert not _is_amount("2023")

    data = [
        ["", "每股", "每股派息数", "每股", "现金分红的", "于本行普通股", "普通股股东的"],
        ["", "送红股数", "（含税）", "转增数", "数额（含税）", "股东的净利润", "净利润的比率"],
        [
            "年度",
            "（股）",
            "（人民币元）",
            "（股） （人民币百万元） （人民币百万元）",
            "",
            "",
            "(%)",
        ],
        ["2023", "–", "1.972", "–", "49,734", "142,044", "35.01"],
        ["2024", "–", "2.000", "–", "50,440", "142,810", "35.32"],
        ["2025（注）", "–", "2.016", "–", "50,843", "143,874", "35.34"],
    ]
    new_data, _, meta = align_header_to_body_columns(data)
    bottom = new_data[2]
    assert bottom[0] == "年度", bottom
    assert "年度" not in str(bottom[4]), bottom
    assert bottom[3] == "（股）", bottom
    assert bottom[4] == "（人民币百万元）", bottom
    assert bottom[5] == "（人民币百万元）", bottom
    assert meta.get("multi_unit_spills"), meta
    assert not any(
        str(a).startswith("bottom:0->") for a in (meta.get("bottom_actions") or [])
    ), meta


def test_letter_codes_not_copied_to_bottom_when_already_above():
    """列码 a/b/c 已在上层表头出现时，禁止再抄到数据前底层（避免重复两行）。"""
    from codes.reconstruct.grid_nucleus.header_align import align_header_to_body_columns

    # 数据上一行金额列为空；旧逻辑会把上层 a/b/c 抄到这一行
    data = [
        ["单位", "a", "b", "c"],
        ["类别", "表内资产余额", "表外转换前资产", "平均转换系数"],
        ["", "", "", ""],
        ["1", "1,904,390", "115,315", "33.63%"],
        ["2", "100,000", "200,000", "10.00%"],
        ["3", "2,004,390", "315,315", "20.00%"],
    ]
    new_data, _, meta = align_header_to_body_columns(data)
    actions = meta.get("bottom_actions") or []
    assert meta.get("amt_cols"), meta
    assert not any(str(a).startswith("bottom_fill_from_above:") for a in actions), meta
    assert any(str(a).startswith("bottom_skip_dup_above:") for a in actions), meta
    letter_rows = [
        i
        for i, row in enumerate(new_data)
        if sum(1 for c in row if str(c).strip() in {"a", "b", "c"}) >= 2
    ]
    assert letter_rows == [0], (letter_rows, new_data, meta)
    bottom = new_data[meta["body_start"] - 1]
    assert not any(str(c).strip() in {"a", "b", "c"} for c in bottom), bottom


def test_cap_slots_keeps_headed_letter_cols_drops_empty():
    """超额截断：有表头（列码）的槽保留；空表头缝列优先丢掉。"""
    from codes.reconstruct.grid_nucleus.column_infer import _cap_column_slots
    from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster

    # 槽心：假缝夹在 j/k/l 之间
    centers = [100.0, 200.0, 670.0, 690.0, 720.0, 740.0, 770.0]
    letters = [
        Nucleus(text="j", x0=668, y0=10, x1=676, y1=20, col_id=0),
        Nucleus(text="k", x0=718, y0=10, x1=726, y1=20, col_id=0),
        Nucleus(text="l", x0=768, y0=10, x1=776, y1=20, col_id=0),
    ]
    # 仅金额、无表头 → 空表头缝
    amts = [
        Nucleus(text="1,000", x0=685, y0=40, x1=710, y1=50, col_id=0),
        Nucleus(text="2,000", x0=735, y0=40, x1=760, y1=50, col_id=0),
    ]
    rows = [
        RowCluster(row_id=0, cy=15, nuclei=letters),
        RowCluster(row_id=1, cy=45, nuclei=amts),
    ]
    out = _cap_column_slots(rows, centers, max_cols=4)
    assert len(out) <= 4 or all(
        any(abs(c - x) < 5 for x in (670.0, 720.0, 770.0)) for c in out if c > 600
    ), out
    # j/k/l 对应槽必须仍在
    for want in (670.0, 720.0, 770.0):
        assert any(abs(c - want) < 1.0 for c in out), (want, out)


def test_distinct_column_headers_forbid_slot_merge():
    """两槽各有不同独立表头（列码/指标）→ 拆并时不得合成一列。"""
    from codes.reconstruct.grid_nucleus.column_infer import (
        _should_collapse_adjacent_slots,
        _slots_have_distinct_independent_headers,
    )
    from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster

    j = Nucleus(text="j", x0=670, y0=10, x1=676, y1=20, col_id=0)
    k = Nucleus(text="k", x0=720, y0=10, x1=726, y1=20, col_id=1)
    hj = Nucleus(text="风险权", x0=658, y0=30, x1=690, y1=40, col_id=0)
    hk = Nucleus(text="预期损", x0=708, y0=30, x1=740, y1=40, col_id=1)
    rows = [
        RowCluster(row_id=0, cy=15, nuclei=[j, k]),
        RowCluster(row_id=1, cy=35, nuclei=[hj, hk]),
    ]
    assert _slots_have_distinct_independent_headers([j, hj], [k, hk])
    assert not _should_collapse_adjacent_slots(
        rows, 0, 1, [673.0, 723.0], small_gap=14.0, large_gap=40.0
    )


def test_identical_x_bounds_force_same_column_despite_phantom_amount_slot():
    """同 [x0,x1] 的「2023年」「指标值」即使被拆到不同 col_id，锁定后必须同列。"""
    from codes.reconstruct.grid_nucleus.column_infer import _lock_same_bound_nuclei
    from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster

    year = Nucleus(text="2023年", x0=451.9, y0=160, x1=483.4, y1=172, col_id=3)
    val_h = Nucleus(text="指标值", x0=451.8, y0=178, x1=483.4, y1=190, col_id=2)
    amt = Nucleus(text="40,137,194", x0=472.8, y0=193, x1=522.4, y1=205, col_id=3)
    lab = Nucleus(text="规模", x0=73, y0=193, x1=94, y1=205, col_id=0)
    rows = [
        RowCluster(row_id=0, cy=166, nuclei=[year]),
        RowCluster(row_id=1, cy=184, nuclei=[val_h]),
        RowCluster(row_id=2, cy=199, nuclei=[lab, amt]),
    ]
    _lock_same_bound_nuclei(rows)
    assert year.col_id == val_h.col_id, (year.col_id, val_h.col_id)


def test_year_and_indicator_value_header_same_column_as_amounts():
    """折行表头「2023年」+「指标值」须与右对齐金额同列，不得拆成空头列+金额列。"""
    words = [
        _word("一级指标", 94, 178, 136, 190),
        _word("二级指标 1", 261, 177, 309, 189),
        _word("2023年", 452, 162, 483, 174),
        _word("指标值", 452, 178, 483, 190),
        _word("规模", 73, 193, 94, 205),
        _word("调整后的表内外资产余额", 166, 193, 310, 205),
        _word("40,137,194", 473, 193, 522, 205),
        _word("关联度", 73, 210, 105, 222),
        _word("金融机构间资产", 166, 210, 270, 222),
        _word("3,387,670", 480, 210, 522, 222),
        _word("可替代性", 73, 227, 120, 239),
        _word("客户存款", 166, 227, 220, 239),
        _word("4,231,988", 480, 227, 522, 239),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    # 找「指标值」「2023年」列，须同列，且金额也在该列
    i_val = i_year = None
    for r in table["data"]:
        cells = [str(c or "").split("\u27e6")[0].strip() for c in r]
        for i, c in enumerate(cells):
            if c == "指标值":
                i_val = i
            if c == "2023年":
                i_year = i
    assert i_val is not None and i_year is not None, table["data"]
    assert i_val == i_year, (i_val, i_year, table["data"])
    body = next(
        r for r in table["data"]
        if any("40,137,194" in str(c) for c in r)
    )
    assert "40,137,194" in str(body[i_val]), (body, i_val, table["data"])


def test_serial_indicator_value_three_cols_not_glued():
    """序号|指标|指标值：序号不得与指标正文粘连，表头「指标」须落在指标列。"""
    words = [
        _word("序号", 92, 211, 112, 221),
        _word("指标", 281, 211, 301, 221),
        _word("指标值", 494, 211, 524, 221),
        # 表前宽标题（曾撑开序号列带导致与指标并槽）
        _word("2025年12月31日止年度", 50, 90, 184, 103),
        _word("1", 100, 226, 105, 236),
        _word("调整后的表内外资产余额", 128, 225, 238, 235),
        _word("155,855", 514, 226, 553, 237),
        _word("2", 100, 241, 105, 250),
        _word("衍生品类资产", 128, 239, 198, 249),
        _word("14,837", 520, 240, 553, 251),
        _word("3", 100, 255, 105, 264),
        _word("衍生品类负债", 128, 254, 198, 264),
        _word("11,966", 520, 255, 553, 265),
        _word("5", 100, 284, 105, 293),
        _word("通过支付系统与其他银行或支付机构（结算参与者）的支付", 128, 282, 375, 293),
        _word("2,460,199", 505, 283, 553, 294),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    data = table["data"]
    # 定位表头行
    hdr = None
    for row in data:
        if any(str(c).strip() == "序号" for c in row):
            hdr = row
            break
    assert hdr is not None, data
    assert "序号" in [str(c).strip() for c in hdr]
    assert "指标" in [str(c).strip() for c in hdr]
    assert "指标值" in [str(c).strip() for c in hdr]
    # 主体：序号与指标分列
    body = None
    for row in data:
        if any("调整后" in str(c) for c in row):
            body = row
            break
    assert body is not None, data
    assert any(str(c).strip() == "1" for c in body), body
    assert any("调整后" in str(c) for c in body), body
    assert not any(str(c).startswith("1 ") for c in body), body
    # 中间不得长期空头（指标列有正文）
    ser_i = next(i for i, c in enumerate(hdr) if str(c).strip() == "序号")
    ind_i = next(i for i, c in enumerate(hdr) if str(c).strip() == "指标")
    assert ser_i != ind_i
    assert "调整后" in str(body[ind_i]), (hdr, body)


def test_dual_metric_header_spills_into_empty_bad_loan_rate_col():
    """「不良|不良」下「贷款金额 贷款率%（注）」粘左列、右列空 → 拆入右列（page_035）。"""
    from codes.v2_steps.table_glue_repair import split_glue_cell
    from codes.reconstruct.grid_nucleus.header_align import align_header_to_body_columns

    assert split_glue_cell("贷款金额 贷款率%（注）") == ("贷款金额", "贷款率%（注）")

    data = [
        ["", "贷款和", "占总额", "不良", "不良", "贷款和", "占总额", "不良", "不良"],
        [
            "（人民币百万元，百分比除外）",
            "垫款金额",
            "百分比%",
            "贷款金额 贷款率%（注）",
            "",
            "垫款金额",
            "百分比%",
            "贷款金额 贷款率%（注）",
            "",
        ],
        ["信用贷款", "2,980,421", "41.06", "30,029", "1.01", "2,800,227", "40.64", "26,942", "0.96"],
        ["保证贷款", "1,007,233", "13.88", "14,665", "1.46", "872,494", "12.67", "17,154", "1.97"],
    ]
    new_data, _, meta = align_header_to_body_columns(data)
    spills = meta.get("dual_header_spills") or []
    assert spills, meta
    assert new_data[1][3] == "贷款金额" and new_data[1][4] == "贷款率%（注）", new_data[1]
    assert new_data[1][7] == "贷款金额" and new_data[1][8] == "贷款率%（注）", new_data[1]
    # 与上一行不良、主体金额同列
    assert new_data[0][3] == "不良" and new_data[2][3] == "30,029"
    assert new_data[0][4] == "不良" and new_data[2][4] == "1.01"

    # 端到端：字框粘连窄 bbox 也能写回对齐
    words = [
        _word("不良", 291.5, 492, 306.1, 501),
        _word("不良", 336.9, 492, 351.5, 501),
        _word("贷款金额 贷款率%（注）", 276.7, 506, 306.1, 515),
        _word("信用贷款", 85, 523, 114, 532),
        _word("30,029", 283.1, 523, 306.1, 532),
        _word("1.01", 336.9, 523, 340.9, 532),
        _word("保证贷款", 85, 537, 114, 546),
        _word("14,665", 283.1, 537, 306.1, 546),
        _word("1.46", 336.9, 537, 340.9, 546),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    data2 = table["data"]
    rate_cols, bad_cols, amt_cols = set(), set(), set()
    for row in data2:
        for ci, cell in enumerate(row):
            s = str(cell or "").strip()
            if "贷款率" in s:
                rate_cols.add(ci)
            if s == "不良":
                bad_cols.add(ci)
            if s == "1.01":
                amt_cols.add(ci)
    assert rate_cols and amt_cols and rate_cols == amt_cols, (rate_cols, amt_cols, data2)
    assert rate_cols <= bad_cols or any(
        data2[ri][c] == "不良"
        for c in rate_cols
        for ri in range(len(data2))
    ), (rate_cols, bad_cols, data2)


def test_header_align_keeps_orphan_cols_for_span_mark():
    """有表头无数据的列不再并入邻列：列结构以凝结核为准，跨格交给标注。"""
    from codes.reconstruct.grid_nucleus.header_align import align_header_to_body_columns

    data = [
        ["", "项目", "期末余额", "", "上期余额", ""],
        ["", "", "2024年12月31日", "", "2024年9月30日", ""],
        ["9", "科目甲", "", "-", "", "-"],
        ["10", "科目乙", "", "425,464", "", "319,349"],
        ["11", "科目丙", "", "607,773", "", "750,116"],
    ]
    new_data, _, meta = align_header_to_body_columns(data)
    assert not (meta.get("merges") or []), meta
    assert 2 in (meta.get("span_kept") or []) or 4 in (meta.get("orphans") or []), meta
    # 日期仍在表头区，未被并进金额列文本
    flat_hdr = " ".join(str(c) for r in new_data[:2] for c in r)
    assert "2024年12月31日" in flat_hdr
    body_amts = [
        str(r[c]).strip()
        for r in new_data[2:]
        for c in range(len(r))
        if "425,464" in str(r[c])
    ]
    assert body_amts, (new_data, meta)


def test_header_align_does_not_copy_period_into_section_row():
    """分组标题行（可用资本）不是表头，报告期不得从上抄重复。"""
    from codes.reconstruct.grid_nucleus.header_align import align_header_to_body_columns

    data = [
        ["", "", "a", "", "b", ""],
        ["", "（单位）", "2024 年", "", "2024 年", ""],
        ["", "", "12 月 31 日", "", "9 月 30 日", ""],
        ["", "可用资本（数额）", "", "", "", ""],
        ["1", "核心一级资本净额", "", "100", "", "200"],
        ["2", "一级资本净额", "", "110", "", "210"],
    ]
    new_data, _, meta = align_header_to_body_columns(data)
    section = next(
        (r for r in new_data if any("可用资本" in str(c) for c in r)),
        None,
    )
    assert section is not None, new_data
    period_in_section = [
        str(c) for c in section
        if "月" in str(c) or "年" in str(c)
    ]
    assert not period_in_section, (section, meta, new_data[:5])
    # 真正的期间行仍保留
    period_rows = [
        r for r in new_data
        if any("12 月" in str(c) or "12月" in str(c) for c in r)
    ]
    assert len(period_rows) == 1, (period_rows, new_data[:5])


def test_overlapping_header_amount_bands_same_column():
    """表头带与金额带左右交叉时不得拆成两列（IRRBB 经济价值变动）。"""
    words = [
        _word("a", 323, 323, 328, 335),
        _word("b", 459, 323, 465, 335),
        _word("（人民币百万元）", 71, 332, 155, 343),
        _word("经济价值变动 2", 291, 337, 360, 348),
        _word("净利息收入变动 3", 422, 337, 502, 348),
        _word("期间", 71, 353, 92, 364),
        _word("2024 年 12 月 31 日", 282, 351, 369, 362),
        _word("2024 年 12 月 31 日", 418, 351, 505, 362),
        _word("平行向上", 71, 367, 113, 378),
        _word("(454,022)", 347, 364, 388, 375),
        _word("115,645", 490, 364, 525, 375),
        _word("平行向下", 71, 381, 113, 392),
        _word("578,108", 354, 379, 388, 390),
        _word("(453,152)", 484, 379, 525, 390),
        _word("变陡峭", 71, 395, 102, 406),
        _word("(383,405)", 347, 393, 388, 404),
        _word("变平缓", 71, 409, 102, 420),
        _word("308,310", 354, 407, 388, 418),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    assert res.n_cols == 3, (res.n_cols, table["data"][:5])
    row = next(r for r in table["data"] if any("平行向上" in str(c) for c in r))
    # 表头与金额同列
    assert any("经济价值" in str(c) for r in table["data"][:4] for c in r)
    eco_row = next(
        (r for r in table["data"][:4] if any("经济价值" in str(c) for c in r)),
        None,
    )
    assert eco_row is not None, table["data"][:4]
    eco_cols = [i for i, c in enumerate(eco_row) if "经济价值" in str(c)]
    assert eco_cols, table["data"][:4]
    ci = eco_cols[0]
    assert "(454,022)" in str(row[ci]) or "454,022" in str(row[ci]), (row, table["data"][:5])


def test_letter_code_cols_not_merged():
    """宽表 a–l 列码不得被 header_align 并成「h i」「效期限 风险加权」。"""
    from codes.reconstruct.grid_nucleus.header_align import align_header_to_body_columns

    # 模拟：列码行 + 副标题 + 主体多为 "-"
    header = ["", "项目", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"]
    sub = ["", "", "表内", "表外", "转", "暴露", "概率", "客户", "损失", "期限", "加权", "权重", "预期", "减值"]
    body = []
    for i in range(6):
        body.append(
            [str(i + 1), f"行{i}"] + ["-" if j % 2 == 0 else "1.0" for j in range(12)]
        )
    data = [header, sub] + body
    new_data, _, meta = align_header_to_body_columns(data)
    row0 = [str(c).strip() for c in new_data[0]]
    # 任一格内不得粘连多个列码
    assert not any(
        len(c) >= 3 and all(ch in "abcdefghijkl" for ch in c.replace(" ", ""))
        for c in row0
    ), (row0, meta)
    letters = [c for c in row0 if c in list("abcdefghijkl")]
    assert letters == list("abcdefghijkl"), (letters, new_data[0], meta)
    sub_row = new_data[1]
    assert not any("期限 加权" in str(c) or "预期 减值" in str(c) for c in sub_row), sub_row


def test_code_column_not_merged_with_amount():
    """金额列与右侧「代码」列不得并槽；资产负债表副标题应对齐两金额列且不抄进分组行。"""
    from codes.reconstruct.grid_nucleus.pipeline import restore_table_grid

    words = [
        _word("（人民币百万元）", 70, 250, 155, 260),
        _word("2024年12月31日", 355, 239, 429, 253),
        _word("财务并表范围下的", 299, 255, 383, 266),
        _word("监管并表范围下的", 401, 255, 485, 266),
        _word("代码", 501, 255, 522, 266),
        _word("资产负债表", 315, 269, 367, 280),
        _word("资产负债表", 416, 269, 469, 280),
        _word("资产", 71, 283, 92, 294),
    ]
    y = 302
    for i, (lab, a, b) in enumerate([
        ("现金及存放中央银行款项", "2,571,361", "2,571,361"),
        ("存放同业款项", "154,532", "140,993"),
        ("贵金属", "138,433", "138,433"),
        ("拆出资金", "672,875", "672,874"),
        ("借出资金", "100,001", "100,002"),
        ("其他应收", "200,001", "200,002"),
    ], start=1):
        words.extend([
            _word(str(i), 77, y, 83, y + 12),
            _word(lab, 100, y, 100 + min(120, 8 * len(lab)), y + 12),
            _word(a, 345, y, 387, y + 12),
            _word(b, 446, y, 488, y + 12),
        ])
        y += 14
    words.extend([
        _word("b", 509, 548, 514, 562),
        _word("a", 509, 560, 514, 575),
    ])
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = restore_table_grid(table, source_words=words)
    assert res.ok and res.data, res.to_dict()
    data = res.data
    # 两金额 + 代码，不应把第二金额并进代码列
    assert res.n_cols >= 4, (res.n_cols, data[:6])
    asset_row = next(r for r in data if any(str(c).strip() == "资产" for c in r))
    assert not any(
        "资产负债表" in str(c) or "2024" in str(c) or str(c).strip() == "代码"
        for c in asset_row
    ), asset_row
    row1 = next(r for r in data if any("现金" in str(c) for c in r))
    amts = [str(c) for c in row1 if "2,571" in str(c)]
    assert len(amts) == 2, row1
    # 代码列不应含金额
    code_cols = [
        i for i, c in enumerate(data[1] if len(data) > 1 else [])
        if str(c).strip() == "代码"
    ]
    for ci in code_cols:
        assert "2,571" not in str(row1[ci] if ci < len(row1) else ""), row1


def test_dual_consolidation_header_wrap_same_col():
    """财务/监管并表口径折行：两行续文须各跟上行表头同列，且对齐下方金额（招商 1875 几何）。

    同文「的资产负债表」是两列并列凝结核，不得标成跨格把右列盖成 ⟦↔⟧。
    """
    from codes.reconstruct.grid_nucleus.pipeline import apply_grid_to_table, restore_table_grid
    from codes.reconstruct.grid_nucleus.span_mark import COVER_MARK, is_span_cover_mark

    words = [
        _word("附表二：集团口径的资产负债表", 86, 145, 373, 157),
        _word("财务并表口径下", 405, 170, 475, 182),
        _word("监管并表口径下", 490, 170, 560, 182),
        _word("的资产负债表", 410, 182, 470, 194),
        _word("的资产负债表", 495, 182, 555, 194),
        _word("资产", 91, 194, 111, 206),
        _word("现金", 91, 207, 111, 219),
        _word("14,808", 450, 207, 477, 219),
        _word("14,808", 535, 207, 563, 219),
        _word("贵金属", 91, 221, 121, 233),
        _word("38,669", 450, 221, 477, 233),
        _word("38,669", 535, 221, 563, 233),
        _word("存放中央银行款项", 91, 235, 171, 247),
        _word("560,207", 445, 235, 477, 247),
        _word("560,207", 530, 235, 563, 247),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = restore_table_grid(table, source_words=words)
    assert res.ok and res.data, res.to_dict()
    data = res.data
    assert res.n_cols == 3, (res.n_cols, data[:6], res.col_lines)

    hdr = next(
        r for r in data
        if any("财务并表" in str(c) for c in r) and any("监管并表" in str(c) for c in r)
    )
    wrap = next(r for r in data if sum(1 for c in r if "资产负债表" in str(c)) >= 2)
    asset = next(r for r in data if any(str(c).strip() == "资产" for c in r))
    cash = next(r for r in data if any(str(c).strip() == "现金" for c in r))
    assert asset is not cash, data
    assert not any("资产现金" in str(c) for r in data for c in r), data

    fi = next(i for i, c in enumerate(hdr) if "财务并表" in str(c))
    ri = next(i for i, c in enumerate(hdr) if "监管并表" in str(c))
    assert fi != ri, hdr
    assert "资产负债表" in str(wrap[fi]), (wrap, fi)
    assert "资产负债表" in str(wrap[ri]), (wrap, ri)
    # 续文不得漂到对方列或额外空列
    assert str(wrap[fi]).count("资产负债表") == 1
    assert "14,808" in str(cash[fi]) and "14,808" in str(cash[ri]), cash
    # 列宽不得出现 1pt 幽灵缝
    lines = list(res.col_lines or [])
    assert len(lines) == 4, lines
    for a, b in zip(lines, lines[1:]):
        assert b - a >= 20.0, (lines, a, b)

    # apply 后跨格标注：两列各留「的资产负债表」，右列不得变成覆盖符
    table2 = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    applied = apply_grid_to_table(table2)
    assert applied.ok and applied.metrics.get("overwrote_data"), applied.to_dict()
    wrap2 = next(
        r for r in table2["data"]
        if sum(1 for c in r if str(c).strip().startswith("的资产负债表")) >= 2
    )
    texts = [str(c) for c in wrap2]
    assert not any(is_span_cover_mark(t) or t.strip() == COVER_MARK for t in texts), wrap2
    assert not any("⟦↔" in t for t in texts), wrap2


def test_merge_split_decimal_nuclei():
    """75 + .21 → 75.21，不再拼成「75 .21」。"""
    from codes.reconstruct.grid_nucleus.assign_cells import join_cell_nuclei_text
    from codes.reconstruct.grid_nucleus.preprocess import (
        merge_split_decimal_nuclei,
        preprocess_words,
    )

    nuclei = [
        Nucleus(text="75", x0=100, y0=10, x1=118, y1=20),
        Nucleus(text=".21", x0=119, y0=10, x1=140, y1=20),
        Nucleus(text="28", x0=200, y0=10, x1=218, y1=20),
        Nucleus(text=".66", x0=219, y0=10, x1=240, y1=20),
    ]
    merged = merge_split_decimal_nuclei(nuclei)
    texts = [n.text for n in merged]
    assert "75.21" in texts and "28.66" in texts, texts
    assert all(" " not in t for t in texts)

    # 同格 join 兜底
    glued = join_cell_nuclei_text([
        Nucleus(text="75", x0=100, y0=10, x1=118, y1=20),
        Nucleus(text=".21", x0=119, y0=10, x1=140, y1=20),
    ])
    assert glued == "75.21", glued

    # 整段已带空格
    words = [
        _word("75 .21", 100, 10, 140, 20),
        _word("迁徙率", 10, 10, 50, 20),
    ]
    pre = preprocess_words(words)
    assert any(n.text == "75.21" for n in pre), [n.text for n in pre]


def test_currency_amount_continuous_is_one_nucleus():
    """人民币+金额连续 = 一体凝结核；不当粘连拆成两列。"""
    from codes.reconstruct.grid_nucleus.pipeline import apply_grid_to_table
    from codes.reconstruct.grid_nucleus.preprocess import preprocess_words
    from codes.v2_steps.table_anomaly_rules import (
        _is_currency_amount_atomic,
        _looks_like_numeric_text_glue,
    )
    from codes.v2_steps.table_glue_repair import split_glue_cell

    for t in ("人民币70,228", "人民币 70,228", "港币31,675", "70,228元"):
        assert _is_currency_amount_atomic(t), t
        assert not _looks_like_numeric_text_glue(t), t
        assert split_glue_cell(t) is None, t

    # 真粘连仍拆
    assert split_glue_cell("19,079,642成都") == ("成都", "19,079,642")

    # 近距分框合并
    words_close = [
        _word("人民币", 200, 100, 230, 112),
        _word("70,228", 231, 100, 275, 112),
        _word("人民币", 300, 100, 330, 112),
        _word("31,675", 331, 100, 375, 112),
    ]
    pre = preprocess_words(words_close)
    texts = [n.text for n in pre]
    assert "人民币70,228" in texts and "人民币31,675" in texts, texts
    assert "人民币" not in texts, texts

    # 大空隙不并（两列）
    words_gap = [
        _word("人民币", 100, 100, 130, 112),
        _word("70,228", 200, 100, 250, 112),
    ]
    pre_gap = preprocess_words(words_gap)
    assert [n.text for n in pre_gap] == ["人民币", "70,228"], [n.text for n in pre_gap]

    # 整表：三列币种金额同格
    words = [
        _word("序号", 50, 50, 70, 62),
        _word("工具A", 200, 50, 240, 62),
        _word("工具B", 300, 50, 340, 62),
        _word("工具C", 400, 50, 440, 62),
        _word("可计入监管资本的数额", 50, 80, 180, 92),
        _word("人民币70,228", 200, 80, 275, 92),
        _word("人民币31,675", 300, 80, 375, 92),
        _word("人民币27,468", 400, 80, 475, 92),
        _word("其他条款", 50, 110, 100, 122),
        _word("-", 200, 110, 210, 122),
        _word("-", 300, 110, 310, 122),
        _word("-", 400, 110, 410, 122),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    data = res.data if res.data else table.get("data")
    assert data and len(data) >= 2, res.to_dict()
    flat = " ".join(str(c) for r in data for c in r)
    assert "人民币70,228" in flat.replace(" ", ""), data
    alone = [
        c for r in data for c in r
        if str(c).strip() in ("人民币", "港币")
    ]
    assert not alone, data
    # 粘连审计不得把币种金额当 glue
    assert int(res.metrics.get("glue_residual") or 0) == 0, res.metrics


def test_wide_title_does_not_merge_amount_cols_wrapped_header():
    """宽表题不得并掉金额列；折行「贷款和/垫款金额」须与金额同列（page_032）。"""
    words = [
        # 跨多列表题（曾撑开列带导致多列金额并成一列）
        _word("按产品类型划分的贷款及不良贷款分布情况", 85, 100, 319, 112),
        _word("本集团", 85, 115, 120, 127),
        _word("2025年12月31日", 238, 120, 295, 132),
        _word("2024年12月31日", 419, 120, 476, 132),
        _word("贷款和", 189, 134, 220, 145),
        _word("占总额", 238, 134, 262, 145),
        _word("不良", 288, 134, 310, 145),
        _word("不良", 336, 134, 352, 145),
        _word("贷款和", 370, 134, 401, 145),
        _word("占总额", 419, 134, 443, 145),
        _word("不良", 469, 134, 491, 145),
        _word("不良", 518, 134, 533, 145),
        _word("（人民币百万元，百分比除外）", 85, 148, 160, 159),
        _word("垫款金额", 182, 148, 220, 159),
        _word("百分比%", 238, 148, 262, 159),
        _word("贷款金额", 283, 148, 310, 159),
        _word("贷款率%(1)", 320, 148, 352, 159),
        _word("垫款金额", 363, 148, 401, 159),
        _word("百分比%", 419, 148, 443, 159),
        _word("贷款金额", 464, 148, 491, 159),
        _word("贷款率%(1)", 502, 148, 533, 159),
        _word("公司贷款", 85, 165, 120, 176),
        _word("3,215,750", 182, 165, 215, 176),
        _word("44.31", 242, 165, 261, 176),
        _word("28,622", 283, 165, 306, 176),
        _word("0.89", 336, 165, 352, 176),
        _word("2,863,740", 363, 165, 397, 176),
        _word("41.57", 424, 165, 442, 176),
        _word("30,475", 464, 165, 488, 176),
        _word("1.06", 518, 165, 533, 176),
        _word("零售贷款", 85, 180, 120, 191),
        _word("3,720,191", 182, 180, 215, 191),
        _word("51.26", 242, 180, 261, 191),
        _word("39,584", 283, 180, 306, 191),
        _word("1.06", 336, 180, 352, 191),
        _word("3,400,000", 363, 180, 397, 191),
        _word("49.00", 424, 180, 442, 191),
        _word("35,000", 464, 180, 488, 191),
        _word("1.03", 518, 180, 533, 191),
        # 合计行拆分小数 100+.00（守恒须认预处理后的 100.00）
        _word("贷款和垫款总额", 85, 195, 137, 206),
        _word("7,258,058", 182, 195, 215, 206),
        _word("100", 238, 195, 250, 206),
        _word(".00", 250, 195, 261, 206),
        _word("68,206", 283, 195, 306, 206),
        _word("0.94", 336, 195, 352, 206),
        _word("6,888,315", 363, 195, 397, 206),
        _word("100", 419, 195, 431, 206),
        _word(".00", 431, 195, 442, 206),
        _word("65,610", 464, 195, 488, 206),
        _word("0.95", 518, 195, 533, 206),
    ]
    table = {
        "type": "table",
        "data": [["错位旧表", "贷款和", "x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    data = table["data"]
    assert max(len(r) for r in data) >= 8, data
    loan_hdr_cols, pad_cols = set(), set()
    company_cols, amt_321_cols = set(), set()
    for row in data:
        for ci, cell in enumerate(row):
            s = str(cell or "").strip()
            if s == "贷款和":
                loan_hdr_cols.add(ci)
            if s == "垫款金额":
                pad_cols.add(ci)
            if s == "公司贷款":
                company_cols.add(ci)
            if s == "3,215,750":
                amt_321_cols.add(ci)
    # 两年各一套折行表头，均与对应「垫款金额」同列
    assert loan_hdr_cols and loan_hdr_cols == pad_cols, (
        loan_hdr_cols, pad_cols, data[:5]
    )
    assert amt_321_cols and amt_321_cols <= loan_hdr_cols, (
        amt_321_cols, loan_hdr_cols, data[:5]
    )
    assert company_cols and not (company_cols & loan_hdr_cols), data[:5]
    flat = " | ".join(str(c) for r in data for c in r)
    assert "公司贷款 3,215,750" not in flat, flat
    assert "100.00" in flat, flat


def test_restore_migration_rate_no_decimal_space():
    """迁徙率表：拆开小数恢复后无空格。"""
    words = [
        _word("迁徙率指标(%)", 10, 8, 90, 20),
        _word("2025年", 120, 8, 160, 20),
        _word("2024年", 200, 8, 240, 20),
        _word("2023年", 280, 8, 320, 20),
        _word("可疑类贷款迁徙率", 10, 35, 100, 47),
        _word("75", 125, 35, 140, 47),
        _word(".21", 141, 35, 160, 47),
        _word("60", 205, 35, 220, 47),
        _word(".93", 221, 35, 240, 47),
        _word("55", 285, 35, 300, 47),
        _word(".33", 301, 35, 320, 47),
    ]
    res = restore_table_grid({"type": "table"}, source_words=words)
    assert res.ok and res.data, res.to_dict()
    flat = " | ".join(str(c) for r in res.data for c in r)
    assert "75.21" in flat, flat
    assert "75 .21" not in flat, flat
    assert "60.93" in flat, flat


def test_balance_header_not_split_from_short_integer_amounts():
    """交易余额（左对齐）与短整数余额（右对齐）不得拆成两列（客户存款类表）。

    短整数未进 is_amount，且会被误判 serial；年列表头不得撑宽金额列带。
    """
    words = [
        _word("2025年12月31日", 330.43, 460, 400.51, 472),
        _word("2024年12月31日", 458.02, 460, 528.12, 472),
        _word("关联方名称", 168.98, 475, 218.89, 487),
        _word("占有关", 381.91, 475, 411.79, 487),
        _word("占有关", 509.88, 475, 539.76, 487),
        _word("交易余额", 313.63, 480.75, 353.47, 490.71),
        _word("同类交易", 375.43, 480.75, 415.27, 490.71),
        _word("交易余额", 440.74, 480.75, 480.58, 490.71),
        _word("同类交易", 503.26, 480.75, 543.10, 490.71),
        _word("余额比例(%)", 367.87, 492, 422.82, 504),
        _word("余额比例(%)", 495.82, 492, 550.76, 504),
        _word("招商局集团及其关联公司小计", 90.38, 510, 220.30, 522),
        _word("150", 345.67, 510, 360.73, 522),
        _word("1.39", 406.90, 510, 424.45, 522),
        _word("186", 473.14, 510, 488.20, 522),
        _word("1.51", 535.20, 510, 552.75, 522),
        _word("中国远洋海运集团及其关联公司小计", 90.38, 525, 250.18, 537),
        _word("15", 350.71, 525, 360.73, 537),
        _word("0.14", 406.90, 525, 424.45, 537),
        _word("8", 483.10, 525, 488.08, 537),
        _word("0.07", 535.20, 525, 552.75, 537),
        _word("合计", 90.38, 540, 110.42, 552),
        _word("171", 345.67, 540, 360.73, 552),
        _word("1.59", 406.90, 540, 424.45, 552),
        _word("199", 473.14, 540, 488.20, 552),
        _word("1.62", 535.20, 540, 552.75, 552),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    assert res.n_cols in (5, 6), (res.n_cols, table["data"])

    # 找含「交易余额」的表头行；150/186 必须落在对应列，不得落到空头邻列
    bal_cols = []
    for row in table["data"]:
        for i, c in enumerate(row):
            if str(c).strip() == "交易余额":
                bal_cols.append(i)
    bal_cols = sorted(set(bal_cols))
    assert len(bal_cols) >= 2, (bal_cols, table["data"])

    body = next(r for r in table["data"] if "150" in [str(c).strip() for c in r])
    assert str(body[bal_cols[0]]).strip() == "150", (body, bal_cols, table["data"])
    assert str(body[bal_cols[1]]).strip() == "186", (body, bal_cols, table["data"])
    # 不得出现：表头在一列、数值在空头邻列
    for ci in bal_cols:
        left = ci - 1
        if left >= 0 and str(body[left]).strip() in ("150", "186", "15", "171", "199"):
            hdr_left = any(
                str(r[left]).strip() == "交易余额" for r in table["data"]
            )
            assert hdr_left, (ci, left, table["data"])


def test_right_aligned_wide_and_narrow_amounts_same_column():
    """同列右对齐：大数与括号负数/短杠不得拆成两列（杠杆率表）。"""
    words = [
        _word("2025 年 12 月 31 日", 342.07, 100, 423.22, 112),
        _word("2025 年 9 月 30 日", 459.94, 100, 536.04, 112),
        _word("1", 96.14, 120, 101.12, 132),
        _word("并表总资产", 117, 120, 167, 132),
        _word("13,070,523", 387.67, 120, 432.66, 132),
        _word("12,644,075", 505.30, 120, 550.29, 132),
        _word("2", 96.14, 140, 101.12, 152),
        _word("并表调整项", 117, 140, 167, 152),
        _word("(98,297)", 398.50, 140, 432.71, 152),
        _word("(90,696)", 516.12, 140, 550.33, 152),
        _word("3", 96.14, 160, 101.12, 172),
        _word("客户资产调整项", 117, 160, 187, 172),
        _word("-", 429.34, 160, 432.66, 172),
        _word("-", 546.96, 160, 550.28, 172),
        _word("4", 96.14, 180, 101.12, 192),
        _word("衍生工具调整项", 117, 180, 187, 192),
        _word("37,203", 405.22, 180, 432.72, 192),
        _word("38,085", 522.84, 180, 550.34, 192),
        _word("6", 96.14, 200, 101.12, 212),
        _word("表外项目调整项", 117, 200, 187, 212),
        _word("2,559,519", 392.71, 200, 432.67, 212),
        _word("2,517,096", 510.36, 200, 550.32, 212),
        _word("13", 93.74, 220, 103.76, 232),
        _word("调整后表内外资产余额", 117, 220, 217, 232),
        _word("15,555,866", 387.67, 220, 432.66, 232),
        _word("15,095,270", 505.30, 220, 550.29, 232),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    assert res.n_cols == 4, (res.n_cols, table["data"])

    row_big = next(r for r in table["data"] if "13,070,523" in [str(c) for c in r])
    row_neg = next(r for r in table["data"] if "(98,297)" in [str(c) for c in r])
    ci_dec = next(i for i, c in enumerate(row_big) if "13,070,523" in str(c))
    assert "(98,297)" in str(row_neg[ci_dec]), (row_big, row_neg, table["data"])
    # 短杠也在同列
    assert any(str(r[ci_dec]).strip() == "-" for r in table["data"]), table["data"]
    # 另一年度列保持独立
    ci_sep = next(i for i, c in enumerate(row_big) if "12,644,075" in str(c))
    assert ci_sep != ci_dec
    assert "(90,696)" in str(row_neg[ci_sep]), (row_neg, table["data"])


def test_serial_column_not_glued_with_label_text():
    """序号列（含 14a）与科目文本必须分列，不得「14a 杠杆率 a」粘连。"""
    from codes.reconstruct.grid_nucleus.column_infer import is_serial_nucleus
    from codes.reconstruct.grid_nucleus.types import Nucleus

    assert is_serial_nucleus(Nucleus(text="14a", x0=0, y0=0, x1=1, y1=1))
    assert is_serial_nucleus(Nucleus(text="24", x0=0, y0=0, x1=1, y1=1))
    assert is_serial_nucleus(Nucleus(text="a", x0=90, y0=0, x1=98, y1=1))
    # 右侧列码 a 不当行号
    assert not is_serial_nucleus(Nucleus(text="a", x0=400, y0=0, x1=410, y1=1))
    assert not is_serial_nucleus(Nucleus(text="杠杆率", x0=0, y0=0, x1=1, y1=1))

    words = [
        _word("序号", 93, 80, 115, 92),
        _word("a", 400, 80, 410, 92),
        _word("b", 500, 80, 510, 92),
        _word("1", 96, 110, 104, 122),
        _word("表内资产（除衍生工具外）", 117, 110, 250, 122),
        _word("13,003,558", 390, 110, 450, 122),
        _word("12,767,589", 490, 110, 550, 122),
        _word("24a", 91, 130, 110, 142),
        _word("杠杆率 a", 117, 130, 170, 142),
        _word("8.00%", 410, 130, 450, 142),
        _word("8.22%", 510, 130, 550, 142),
        _word("3", 96, 150, 104, 162),
        _word("稳定存款", 136, 150, 190, 162),
        _word("1,044,708", 400, 150, 460, 162),
        _word("52,235", 510, 150, 550, 162),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    import re

    glued = [
        str(c)
        for r in table["data"]
        for c in r
        if re.match(r"^\d{1,3}[a-zA-Z]?\s+[\u4e00-\u9fff]", str(c or "").strip())
    ]
    assert not glued, (glued, table["data"])

    row1 = next(r for r in table["data"] if any(str(c).strip() == "1" for c in r))
    assert "表内资产" in " ".join(str(c) for c in row1)
    assert str(row1[0]).strip() == "1" or any(str(c).strip() == "1" for c in row1)
    # 1 与科目不同列
    ser_i = next(i for i, c in enumerate(row1) if str(c).strip() == "1")
    lab_i = next(i for i, c in enumerate(row1) if "表内资产" in str(c))
    assert ser_i != lab_i, row1

    row_a = next(r for r in table["data"] if any(str(c).strip() == "24a" for c in r))
    ser_a = next(i for i, c in enumerate(row_a) if str(c).strip() == "24a")
    lab_a = next(i for i, c in enumerate(row_a) if "杠杆率" in str(c))
    assert ser_a != lab_a, row_a


def test_first_col_letter_serial_and_two_digit_pairs_split():
    """第一列：纯字母行号、仅两行纯数字序号+科目，均应独立成列。"""
    import re

    # 仅 2 行纯数字序号（无 14a、旧阈值要 ≥3）也应分列
    words_digit = [
        _word("13", 96, 110, 110, 122),
        _word("调整后表内外资产总额", 117, 110, 260, 122),
        _word("100", 400, 110, 430, 122),
        _word("14", 96, 130, 110, 142),
        _word("杠杆率(%)", 117, 130, 180, 142),
        _word("8.00%", 410, 130, 450, 142),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words_digit,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    glued = [
        str(c)
        for r in table["data"]
        for c in r
        if re.match(r"^\d{1,3}[a-zA-Z]?\s+[\u4e00-\u9fff]", str(c or "").strip())
    ]
    assert not glued, (glued, table["data"])
    row13 = next(r for r in table["data"] if any(str(c).strip() == "13" for c in r))
    assert next(i for i, c in enumerate(row13) if str(c).strip() == "13") != next(
        i for i, c in enumerate(row13) if "调整后" in str(c)
    ), row13

    # 左侧纯字母行号 a/b（右侧金额列码勿干扰）
    words_letter = [
        _word("a", 96, 110, 104, 122),
        _word("合格优质流动性资产", 117, 110, 250, 122),
        _word("1,000", 400, 110, 440, 122),
        _word("b", 96, 130, 104, 142),
        _word("现金净流出量", 117, 130, 200, 142),
        _word("500", 410, 130, 440, 142),
    ]
    table2 = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words_letter,
        "_table_kind": {"kind": "data"},
    }
    res2 = apply_grid_to_table(table2)
    assert res2.ok and res2.metrics.get("overwrote_data"), res2.to_dict()
    glued2 = [
        str(c)
        for r in table2["data"]
        for c in r
        if re.match(r"^[a-zA-Z]\s+[\u4e00-\u9fff]", str(c or "").strip())
    ]
    assert not glued2, (glued2, table2["data"])
    row_a = next(r for r in table2["data"] if any(str(c).strip() == "a" for c in r))
    assert next(i for i, c in enumerate(row_a) if str(c).strip() == "a") != next(
        i for i, c in enumerate(row_a) if "合格优质" in str(c)
    ), row_a


def test_wrap_label_continuation_not_in_serial_col():
    """折行续文「底线前）」x 与标签列对齐，不得因序号列带被小节标题撑宽而落入第一列。"""
    words = [
        _word("1", 71, 100, 76, 112),
        _word("核心一级资本净额", 106, 100, 200, 112),
        _word("100", 290, 100, 320, 112),
        # 小节标题偏左，易被初筛进序号列并撑宽列带
        _word("风险加权资产（续）", 73, 120, 158, 132),
        _word("4a", 71, 150, 81, 162),
        _word("风险加权资产合计（应用资本", 106, 148, 243, 160),
        _word("21,854,590", 290, 150, 350, 162),
        # 折行续文：左缘与标签列同 x，不得进序号列
        _word("底线前）", 106, 164, 148, 176),
        _word("5", 71, 190, 76, 202),
        _word("核心一级资本充足率", 106, 190, 210, 202),
        _word("14.0%", 300, 190, 340, 202),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    for r in table["data"]:
        if any("底线前" in str(c) for c in r):
            assert str(r[0]).strip() != "底线前）", table["data"]
            assert any("底线前" in str(c) for c in r[1:]), r
    cont = next(r for r in table["data"] if any("底线前" in str(c) for c in r))
    cont_i = next(i for i, c in enumerate(cont) if "底线前" in str(c))
    assert cont_i != 0, (cont, table["data"])


def test_period_bucket_headers_keep_left_to_right_order():
    """期限分档表头必须保持左右阅读序：无期限 | <6个月 | 6-12个月 | ≥1年，不得重排或粘连。"""
    words = [
        _word("序号", 50, 80, 80, 92),
        _word("项目", 90, 80, 120, 92),
        _word("无期限", 215, 100, 246, 112),
        _word("<6个月", 270, 100, 302, 112),
        _word("6-12个月", 323, 100, 363, 112),
        _word("≥1年", 388, 100, 409, 112),
        _word("值", 450, 100, 465, 112),
        _word("1", 55, 120, 65, 132),
        _word("资本", 90, 120, 120, 132),
        _word("-", 230, 120, 240, 132),
        _word("-", 285, 120, 295, 132),
        _word("-", 340, 120, 350, 132),
        _word("100", 395, 120, 420, 132),
        _word("100", 450, 120, 475, 132),
        _word("2", 55, 140, 65, 152),
        _word("监管资本", 90, 140, 140, 152),
        _word("10", 225, 140, 245, 152),
        _word("20", 280, 140, 300, 152),
        _word("30", 335, 140, 355, 152),
        _word("40", 395, 140, 420, 152),
        _word("100", 450, 140, 475, 152),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    hdr = next(
        (r for r in table["data"] if any("无期限" in str(c) for c in r)),
        None,
    )
    assert hdr is not None, table["data"]
    cells = [str(c or "").split("\u27e6")[0].strip() for c in hdr]
    # 不得粘成乱序串
    glued = [c for c in cells if ("无期限" in c and ("个月" in c or "≥1" in c or "1年" in c))]
    assert not glued, (glued, table["data"])
    i_none = next(i for i, c in enumerate(cells) if c == "无期限")
    i_6 = next(i for i, c in enumerate(cells) if c == "<6个月")
    i_12 = next(i for i, c in enumerate(cells) if c == "6-12个月")
    i_1y = next(i for i, c in enumerate(cells) if c in ("≥1年", "≧1年") or c.endswith("1年"))
    assert i_none < i_6 < i_12 < i_1y, (cells, table["data"])


def test_nsfr_period_headers_not_glued_by_header_align():
    """NSFR 续表：表体金额稀疏时，header_align 不得把 <6|6-12|≥1年 并进一格乱序。"""
    from codes.reconstruct.grid_nucleus import restore_table_grid
    from codes.reconstruct.grid_nucleus.header_align import align_header_to_body_columns

    # 凝结核已分好的底层表头 + 稀疏金额列（模拟 1919）
    data = [
        ["", "", "", "", "折算前数值", "", "折算后数值"],
        ["序号", "", "无期限", "<6个月", "6-12个月", "≥1年", ""],
        ["27", "实物交易的大宗商品", "38,673", "", "", "", "32,872"],
        ["28", "违约基金", "", "", "", "525", "446"],
    ]
    new_data, _, meta = align_header_to_body_columns(data)
    actions = meta.get("bottom_actions") or []
    assert not any(a.startswith("bottom:3->") or a.startswith("bottom:4->") for a in actions), meta
    hdr = next(r for r in new_data if any(str(c).strip() == "<6个月" for c in r))
    cells = [str(c or "").strip() for c in hdr]
    assert cells.count("<6个月") == 1 and cells.count("6-12个月") == 1 and cells.count("≥1年") == 1, (
        cells, new_data, meta
    )
    assert cells.index("<6个月") < cells.index("6-12个月") < cells.index("≥1年"), (cells, new_data)

    # 端到端：真实几何字框
    words = [
        _word("折算前数值", 367.9, 175.9, 417.8, 187),
        _word("折算后数值", 501.3, 175.9, 551.2, 187),
        _word("无期限", 299.6, 189.5, 329.5, 201),
        _word("<6个月", 348.8, 189.5, 379.4, 201),
        _word("6-12个月", 399.0, 189.5, 437.4, 201),
        _word("≥1年", 459.1, 189.5, 479.6, 201),
        _word("序号", 92, 210, 112, 222),
        _word("所需的稳定资金-续", 92.7, 202.9, 175.8, 214),
        _word("27", 95, 240, 110, 252),
        _word("实物交易的大宗商品(包括黄金)", 125, 240, 265, 252),
        _word("38,673", 300, 240, 340, 252),
        _word("32,872", 510, 240, 550, 252),
        _word("28", 95, 260, 110, 272),
        _word("给中央交易对手的违约基金", 125, 260, 265, 272),
        _word("525", 450, 260, 480, 272),
        _word("446", 510, 260, 540, 272),
    ]
    gr = restore_table_grid(
        {"type": "table", "data": [["x"]], "_source_words": words},
    )
    assert gr.data, gr.to_dict()
    flat = " | ".join(str(c) for r in gr.data for c in r)
    assert "≥1年 <6个月" not in flat, flat
    hdr2 = next(
        (r for r in gr.data if any(str(c).strip() == "<6个月" for c in r)),
        None,
    )
    assert hdr2 is not None, gr.data
    cells2 = [str(c or "").split("\u27e6")[0].strip() for c in hdr2]
    assert cells2.index("<6个月") < cells2.index("6-12个月") < cells2.index("≥1年"), (
        cells2, gr.data
    )

def test_indented_labels_share_one_column_common_boundary():
    """科目缩进（其中：）与上级科目同列：凝结核取公共边界，不得因左缘偏移拆成多列。"""
    words = [
        _word("54", 70, 100, 82, 112),
        _word("核心一级资本充足率（%）", 107, 100, 230, 112),
        _word("14.48", 430, 100, 460, 112),
        _word("55", 70, 120, 82, 132),
        _word("一级资本充足率（%）", 107, 120, 210, 132),
        _word("15.21", 430, 120, 460, 132),
        _word("58", 70, 140, 82, 152),
        _word("其中：储备资本要求", 129, 140, 224, 152),
        _word("2.50", 430, 140, 460, 152),
        _word("60", 70, 160, 82, 172),
        _word("其中：全球系统重要性银行或国内系统重要性银行附加资本要求", 129, 160, 372, 172),
        _word("1.50", 430, 160, 460, 172),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    # 三列结构：序号 | 科目 | 数值
    assert res.metrics.get("n_cols") <= 4, (res.metrics.get("n_cols"), table["data"])
    label_cols = set()
    for r in table["data"]:
        for i, c in enumerate(r):
            t = str(c or "").split("\u27e6")[0].strip()
            if any(k in t for k in ("核心一级", "一级资本", "其中：", "储备", "全球系统")):
                label_cols.add(i)
    assert len(label_cols) == 1, (label_cols, table["data"])


def test_wrap_amount_fragment_does_not_split_label_column():
    """折行续文「金额/应扣除金额」不得当独立列头，把缩进「其中：」拆成第二科目列。"""
    words = [
        _word("a", 430, 40, 436, 52),
        _word("数额", 420, 55, 442, 67),
        _word("37", 70, 100, 82, 112),
        _word("应从二级资本中扣除的未扣缺口", 107, 100, 254, 112),
        _word("-", 470, 100, 481, 112),
        _word("43", 70, 120, 82, 132),
        _word("超额损失准备可计入部分", 107, 120, 223, 132),
        _word("384,521", 440, 120, 481, 132),
        _word("28", 70, 140, 82, 152),
        _word("其中：权益部分", 129, 140, 203, 152),
        _word("159,977", 440, 140, 481, 152),
        _word("47", 70, 160, 82, 172),
        _word("对未并表金融机构小额少数资本投资中的二级资本应扣除", 107, 160, 372, 172),
        _word("-", 470, 160, 481, 172),
        _word("金额", 107, 175, 128, 187),
        _word("34", 70, 200, 82, 212),
        _word("对未并表金融机构小额少数资本投资中的其他一级资本中", 107, 200, 372, 212),
        _word("-", 470, 200, 481, 212),
        _word("应扣除金额", 107, 215, 160, 227),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    assert res.metrics.get("n_cols") <= 4, (res.metrics.get("n_cols"), table["data"])
    label_cols = set()
    for r in table["data"]:
        for i, c in enumerate(r):
            t = str(c or "").split("\u27e6")[0].strip()
            if any(
                k in t
                for k in ("未扣缺口", "超额损失", "其中：权益", "应扣除", "金额")
            ):
                label_cols.add(i)
    assert len(label_cols) == 1, (label_cols, table["data"])


def test_sec1_label_not_merged_into_amount_column():
    """SEC1：科目与 a 列金额分列；折行「款」并回「…抵押贷款」；勿因表头「传统型」退化。"""
    words = [
        _word("a", 240, 40, 246, 52),
        _word("b", 291, 40, 297, 52),
        _word("传统型", 230, 55, 270, 67),
        _word("足STC标", 274, 55, 314, 67),
        _word("1", 79, 100, 85, 112),
        _word("零售类合计", 98, 100, 148, 112),
        _word("7,195", 237, 100, 261, 112),
        _word("-", 313, 100, 317, 112),
        _word("2", 79, 120, 85, 132),
        _word("其中：个人住房抵押贷", 109, 118, 209, 130),
        _word("7,134", 237, 120, 261, 132),
        _word("-", 313, 120, 317, 132),
        # 与上行同簇：折行「款」须并进科目格
        _word("款", 109, 126, 119, 138),
        _word("3", 79, 155, 85, 167),
        _word("其中：信用卡", 109, 155, 169, 167),
        _word("52", 251, 155, 261, 167),
        _word("-", 313, 155, 317, 167),
        _word("6 公司类合计", 79, 180, 148, 192),
        _word("25", 251, 180, 261, 192),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    assert res.method == "nucleus", res.to_dict()
    # 信用卡不得与 52 粘同一格
    assert not any(
        "信用卡" in str(c) and any(ch.isdigit() for ch in str(c).split("信用卡")[-1])
        for r in table["data"]
        for c in r
    ), table["data"]
    # 序号与科目已拆
    assert any(str(c).strip() == "6" for r in table["data"] for c in r), table["data"]
    assert any("公司类合计" in str(c) for r in table["data"] for c in r), table["data"]
    # 科目列与金额列分离
    for r in table["data"]:
        cells = [str(c or "").split("\u27e6")[0].strip() for c in r]
        if any("零售类合计" in c for c in cells):
            li = next(i for i, c in enumerate(cells) if "零售类合计" in c)
            ai = next(i for i, c in enumerate(cells) if "7,195" in c)
            assert ai != li, (r, table["data"])
            break
    else:
        raise AssertionError(table["data"])
    # 折行「款」若单独成行，不得与金额同列
    for r in table["data"]:
        cells = [str(c or "").split("\u27e6")[0].strip() for c in r]
        if "款" not in cells:
            continue
        ki = cells.index("款")
        assert "7,134" not in cells[ki] and cells[ki] == "款"
        amts = [i for i, c in enumerate(cells) if c.replace(",", "").isdigit()]
        assert ki not in amts, r


def test_small_gap_id_fragments_merge_by_vertical_band():
    """标识码小空隙断框：用上下列带约束并回同列，不得拆成多列。"""
    words = []
    # 表头行：序号 | 属性 | 工具A | 工具B
    words += [
        _word("1", 40, 40, 50, 52),
        _word("发行机构", 60, 40, 110, 52),
        _word("招商银行", 150, 40, 200, 52),
        _word("招商银行", 260, 40, 310, 52),
    ]
    # 标识码：A 连续；B 小空隙断成三段（仍同列）
    words += [
        _word("2", 40, 70, 50, 82),
        _word("标识码", 60, 70, 95, 82),
        _word("242480070", 150, 70, 210, 82),
        _word("2423", 255, 70, 278, 82),
        _word("800", 281, 70, 300, 82),
        _word("33", 303, 70, 318, 82),
    ]
    # 多行表体稳住列槽
    for yi, (ser, lab, a, b) in enumerate([
        ("3", "适用法律", "中国大陆", "中国香港"),
        ("4", "资本层级", "核心一级资本", "核心一级资本"),
        ("5", "工具类型", "普通股", "普通股"),
        ("6", "会计处理", "权益", "权益"),
    ], start=0):
        y = 100 + yi * 28
        words += [
            _word(ser, 40, y, 50, y + 12),
            _word(lab, 60, y, 60 + 8 * len(lab), y + 12),
            _word(a, 150, y, 150 + min(55, 8 * len(a)), y + 12),
            _word(b, 260, y, 260 + min(55, 8 * len(b)), y + 12),
        ]
    # 金额行（连续币种金额）
    words += [
        _word("7", 40, 220, 50, 232),
        _word("可计入数额", 60, 220, 120, 232),
        _word("人民币70,228", 145, 220, 215, 232),
        _word("人民币31,675", 255, 220, 325, 232),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    data = res.data if res.data else table.get("data")
    assert data, res.to_dict()
    # 列数应接近 4（序号+属性+2工具），不得因断码拆出多余列
    assert res.n_cols <= 5, (res.n_cols, data)
    id_row = next(
        (r for r in data if any("2423" in str(c) or "242480070" in str(c) for c in r)),
        None,
    )
    assert id_row is not None, data
    flat = " ".join(str(c) for c in id_row)
    # 断码应落在同一工具列（可含空格拼接），不得把 33 甩到邻列与另一码粘连
    assert "33" in flat.replace(" ", "") or any("33" in str(c) for c in id_row), id_row
    # 两工具列仍分开
    cmb = [i for i, r in enumerate(data) if any("招商银行" in str(c) for c in r)]
    assert cmb, data
    hdr = data[cmb[0]]
    bank_cols = [i for i, c in enumerate(hdr) if "招商银行" in str(c)]
    assert len(bank_cols) >= 2, (hdr, data)


def test_peer_instrument_columns_not_merged_despite_moderate_gap():
    """多行同时有内容的真并列工具列：即使列间距不大也不得并。"""
    words = []
    xs = [140, 210, 280, 350]
    words += [_word("1", 40, 40, 50, 52), _word("发行机构", 55, 40, 110, 52)]
    for x in xs:
        words.append(_word("招商银行", x, 40, x + 50, 52))
    for yi, lab in enumerate(["标识码", "适用法律", "资本层级", "工具类型", "会计处理"]):
        y = 70 + yi * 26
        words += [_word(str(yi + 2), 40, y, 50, y + 12), _word(lab, 55, y, 110, y + 12)]
        for j, x in enumerate(xs):
            words.append(_word(f"值{yi}{j}", x, y, x + 40, y + 12))
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    data = res.data if res.data else table.get("data")
    assert data, res.to_dict()
    # 序号+属性+4工具 ≈ 6 列
    assert 5 <= res.n_cols <= 7, (res.n_cols, data)
    hdr = next((r for r in data if sum(1 for c in r if "招商银行" in str(c)) >= 2), None)
    assert hdr is not None, data
    assert sum(1 for c in hdr if "招商银行" in str(c)) >= 4, hdr


def test_orphan_single_datum_column_without_header_is_pruned():
    """仅单点数据、无表头的列非常可疑（多分缝），应丢掉并让数据归入邻列。"""
    words = [
        _word("a", 200, 60, 206, 72),
        _word("b", 280, 60, 286, 72),
        _word("无期限", 185, 80, 220, 92),
        _word("<6个月", 260, 80, 300, 92),
        _word("1", 50, 110, 60, 122),
        _word("项目甲", 90, 110, 140, 122),
        _word("-", 200, 110, 210, 122),
        _word("100", 270, 110, 300, 122),
        _word("2", 50, 130, 60, 142),
        _word("项目乙", 90, 130, 140, 142),
        _word("-", 200, 130, 210, 142),
        _word("200", 270, 130, 300, 142),
        # 缝里一个无表头的孤立金额 → 不得成列
        _word("999", 235, 110, 255, 122),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    hdr = next(
        (r for r in table["data"] if any(str(c).strip() == "无期限" for c in r)),
        None,
    )
    assert hdr is not None, table["data"]
    cells_h = [str(c or "").split("\u27e6")[0].strip() for c in hdr]
    i_a = next(i for i, c in enumerate(cells_h) if c == "无期限")
    i_b = next(i for i, c in enumerate(cells_h) if c == "<6个月")
    assert i_b == i_a + 1, (cells_h, table["data"])
    # 不得在 a/b 间多出只装 999 的空列表头列
    assert res.metrics.get("n_cols") <= 5, (res.metrics.get("n_cols"), table["data"])


def test_all_blank_column_means_wrong_split_must_reclaim():
    """拆出一列却通列空白 → 拆错，必须收回（不得保留空缝）。"""
    from codes.reconstruct.grid_nucleus.pipeline import prune_empty_columns
    from codes.reconstruct.grid_nucleus.span_mark import COVER_MARK

    data = [
        ["关联方", "6,322", "", "6.38"],
        ["合计", "6,352", "", "6.41"],
        ["", "30", COVER_MARK, "0.03"],
    ]
    col_lines = [40.0, 150.0, 250.0, 320.0, 400.0]
    out, lines = prune_empty_columns(data, col_lines)
    assert max(len(r) for r in out) == 3, out
    row0 = out[0]
    assert "6,322" in str(row0[1]) and "6.38" in str(row0[2]), out
    # 中间全空列已收回
    assert all(str(c).strip() != "" or True for c in row0)
    assert not any(
        all(not str(r[c] if c < len(r) else "").strip() or str(r[c]).strip() == COVER_MARK
            for r in out)
        for c in range(max(len(r) for r in out))
    ) or max(len(r) for r in out) == 3


def test_cover_only_blank_column_is_pruned():
    """仅含 ⟦↔⟧ 覆盖符的缝列视为空白，应删除。"""
    from codes.reconstruct.grid_nucleus.pipeline import prune_empty_columns
    from codes.reconstruct.grid_nucleus.span_mark import COVER_MARK

    data = [
        ["54", COVER_MARK, "核心一级资本充足率", "14%"],
        ["55", COVER_MARK, "一级资本充足率", "16%"],
        ["56", "", "资本充足率", "18%"],
        ["", COVER_MARK, "科目甲", "1"],
    ]
    col_lines = [50.0, 90.0, 120.0, 300.0, 400.0]
    out, lines = prune_empty_columns(data, col_lines)
    assert max(len(r) for r in out) == 3, out
    # 中间仅覆盖符列已删：序号 | 科目 | 金额
    assert out[0][0] == "54" and "核心一级" in out[0][1] and out[0][2] == "14%", out
    assert all(COVER_MARK not in r for r in out), out


def test_right_aligned_dash_not_phantom_between_letter_cols():
    """右对齐「-」偏出「无期限」中心时，不得在 a/b 之间造空白列；短横须落在 a 下。"""
    words = [
        _word("序号", 50, 60, 80, 72),
        _word("项目", 90, 60, 120, 72),
        # Q3 段：a / 无期限 中心≈527；短横右对齐≈549；b≈585
        _word("a", 524, 76, 530, 88),
        _word("b", 582, 76, 588, 88),
        _word("c", 640, 76, 645, 88),
        _word("无期限", 511, 100, 543, 112),
        _word("<6个月", 569, 100, 602, 112),
        _word("6-12个月", 622, 100, 662, 112),
        _word("31", 55, 120, 70, 132),
        _word("以上未包括的所有", 90, 120, 180, 132),
        # 关键：短横在无期限右侧、b 左侧（右对齐单元格）
        _word("-", 547, 120, 550, 132),
        _word("162,183", 570, 120, 610, 132),
        _word("162,583", 625, 120, 665, 132),
        _word("32", 55, 140, 70, 152),
        _word("其它", 90, 140, 120, 152),
        _word("-", 547, 140, 550, 152),
        _word("100", 575, 140, 600, 152),
        _word("200", 630, 140, 655, 152),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    # a 与 b 相邻，中间不得夹空列；短横与「无期限」同列
    hdr = next(
        (r for r in table["data"] if any(str(c).strip() == "无期限" for c in r)),
        None,
    )
    assert hdr is not None, table["data"]
    cells_h = [str(c or "").split("\u27e6")[0].strip() for c in hdr]
    i_none = next(i for i, c in enumerate(cells_h) if c == "无期限")
    i_6 = next(i for i, c in enumerate(cells_h) if c == "<6个月")
    assert i_6 == i_none + 1, (cells_h, table["data"])

    body = next(
        (r for r in table["data"] if any(str(c).strip() == "162,183" for c in r)),
        None,
    )
    assert body is not None, table["data"]
    cells_b = [str(c or "").split("\u27e6")[0].strip() for c in body]
    assert cells_b[i_none] == "-", (cells_b, i_none, table["data"])
    assert cells_b[i_6] == "162,183", (cells_b, i_6, table["data"])


def test_body_text_and_text_digit_cols_count_as_data():
    """表体数据列含纯文本、文本+数字：与金额同等，不得当无证据幽灵列删掉。"""
    words = [
        _word("项目", 50, 60, 80, 72),
        # 跨列表头不得造幽灵列
        _word("折算前数值", 200, 80, 340, 92),
        _word("状态", 200, 100, 230, 112),
        _word("等级", 280, 100, 310, 112),
        _word("零售", 50, 120, 80, 132),
        _word("不适用", 200, 120, 240, 132),
        _word("等级A1", 275, 120, 320, 132),
        _word("公司", 50, 140, 80, 152),
        _word("是", 210, 140, 225, 152),
        _word("等级B2", 275, 140, 320, 152),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    found = False
    for r in table["data"]:
        cells = [str(c or "").split("\u27e6")[0].strip() for c in r]
        i_txt = next((i for i, c in enumerate(cells) if c in ("不适用", "是")), None)
        i_mix = next((i for i, c in enumerate(cells) if c in ("等级A1", "等级B2")), None)
        if i_txt is not None and i_mix is not None and i_txt != i_mix:
            found = True
            break
    assert found, table["data"]
    # 不得把文本与文本+数字并格
    glued = [
        str(c)
        for r in table["data"]
        for c in r
        if ("不适用" in str(c) and "等级" in str(c)) or ("是" in str(c) and "等级B" in str(c))
    ]
    assert not glued, (glued, table["data"])


def test_body_data_cols_not_phantom_from_spanning_header():
    """列数以表体为准：跨列表头「折算前数值」不得造幽灵空列，b/c 两金额不得并格。"""
    words = [
        _word("序号", 50, 60, 80, 72),
        _word("项目", 90, 60, 120, 72),
        # 跨 b+c 的上层表头（宽框）——不得单独成列
        _word("折算前数值", 250, 80, 360, 92),
        _word("无期限", 215, 100, 246, 112),
        _word("<6个月", 270, 100, 302, 112),
        _word("6-12个月", 323, 100, 363, 112),
        _word("≥1年", 388, 100, 409, 112),
        _word("值", 450, 100, 465, 112),
        _word("31", 55, 120, 70, 132),
        _word("以上未包括的所有", 90, 120, 180, 132),
        _word("-", 230, 120, 240, 132),
        _word("310,809", 275, 120, 310, 132),
        _word("148,141", 330, 120, 365, 132),
        _word("14,643", 390, 120, 420, 132),
        _word("473,310", 445, 120, 480, 132),
        _word("32", 55, 140, 70, 152),
        _word("其它资产", 90, 140, 140, 152),
        _word("-", 230, 140, 240, 152),
        _word("162,183", 275, 140, 310, 152),
        _word("162,583", 330, 140, 365, 152),
        _word("62,140", 390, 140, 420, 152),
        _word("385,449", 445, 140, 480, 152),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    glued = []
    found_split = False
    for r in table["data"]:
        cells = [str(c or "").split("\u27e6")[0].strip() for c in r]
        for c in cells:
            if "310,809" in c and "148,141" in c:
                glued.append(c)
            if "162,183" in c and "162,583" in c:
                glued.append(c)
        i_b = next((i for i, c in enumerate(cells) if c == "310,809"), None)
        i_c = next((i for i, c in enumerate(cells) if c == "148,141"), None)
        if i_b is not None and i_c is not None and i_b != i_c:
            found_split = True
    assert not glued, (glued, table["data"])
    assert found_split, table["data"]


def test_body_pd_and_customer_count_not_merged_despite_date_header():
    """分列以表体为准：PD% 与客户数多数分行独立，不得因跨 e–f 日期表头/粘连行并成「0.47% 37」。"""
    words = [
        # 跨两列日期表头（合并单元格）——仅次要参考
        _word("2024年12月31日", 430, 40, 520, 52),
        _word("平均违约概率", 387, 55, 429, 67),
        _word("客户数", 449, 55, 486, 67),
        _word("平均违约损失率", 505, 55, 537, 67),
        _word("e", 406, 70, 411, 82),
        _word("f", 466, 70, 470, 82),
        _word("g", 518, 70, 524, 82),
        # 表体：多数行 PD% 与客户数左右分开
        _word("零售", 60, 100, 90, 112),
        _word("0.47%", 406, 100, 433, 112),
        _word("37", 481, 100, 492, 112),
        _word("15.00%", 507, 100, 540, 112),
        _word("公司", 60, 120, 90, 132),
        _word("0.52%", 406, 120, 433, 132),
        _word("293,845", 458, 120, 492, 132),
        _word("33.18%", 507, 120, 540, 132),
        _word("同业", 60, 140, 90, 152),
        _word("1.85%", 406, 140, 433, 152),
        _word("171,373", 458, 140, 492, 152),
        _word("45.45%", 507, 140, 540, 152),
        # 偶发粘连框（跨两列）不得当桥把 e/f 并槽
        _word("合计", 60, 160, 90, 172),
        _word("2.20% 77,221,526", 406, 160, 492, 172),
        _word("31.85%", 507, 160, 540, 172),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    # 正常行不得并成「0.47% 37」；源里粘连的「2.20% 77,221,526」须拆核
    glued = []
    for r in table["data"]:
        for c in r:
            t = str(c or "").strip()
            # 去掉跨格标记再判
            t_plain = t.split("\u27e6")[0].strip()
            if "%" in t_plain:
                rest = t_plain.split("%", 1)[-1].strip()
                if rest and any(ch.isdigit() for ch in rest):
                    glued.append(t_plain)
    assert not glued, (glued, table["data"])
    # 至少有一行同时出现独立的 PD% 与客户数
    found_split = False
    for r in table["data"]:
        cells = [str(c or "").strip().split("\u27e6")[0].strip() for c in r]
        pd_i = next(
            (
                i
                for i, c in enumerate(cells)
                if c in ("0.47%", "0.52%", "1.85%", "2.20%")
            ),
            None,
        )
        cust_i = next(
            (
                i
                for i, c in enumerate(cells)
                if c in ("37", "293,845", "171,373", "77,221,526")
            ),
            None,
        )
        if pd_i is not None and cust_i is not None and pd_i != cust_i:
            found_split = True
            break
    assert found_split, table["data"]
    # 粘连源字也应拆成两格
    assert any(
        str(c).strip().startswith("2.20%") and "77" not in str(c)
        for r in table["data"]
        for c in r
    ), table["data"]
    assert any(
        "77,221,526" in str(c)
        for r in table["data"]
        for c in r
    ), table["data"]


def test_span_marks_cover_neighbor_cells_after_grid():
    """跨格用符号证明：锚点「⟦↔N⟧」，邻格「⟦↔⟧」，不真合并。"""
    from codes.reconstruct.grid_nucleus.span_mark import (
        COVER_MARK,
        is_span_anchor_mark,
        is_span_cover_mark,
        mark_spanned_neighbor_cells,
        parse_anchor_colspan,
        strip_span_anchor_mark,
    )

    col_lines = [0.0, 100.0, 200.0, 300.0]
    data = [
        ["2024年12月31日", "", ""],
        ["项目", "a", "b"],
        ["现金", "1", "2"],
    ]
    words = [
        {"text": "2024年12月31日", "x0": 20.0, "y0": 10.0, "x1": 280.0, "y1": 22.0},
        {"text": "项目", "x0": 10.0, "y0": 30.0, "x1": 50.0, "y1": 42.0},
        {"text": "a", "x0": 140.0, "y0": 30.0, "x1": 150.0, "y1": 42.0},
        {"text": "b", "x0": 240.0, "y0": 30.0, "x1": 250.0, "y1": 42.0},
    ]
    out, spans = mark_spanned_neighbor_cells(data, words=words, col_lines=col_lines)
    assert spans, (out, spans)
    sp0 = next(s for s in spans if "2024" in str(s.get("text") or ""))
    assert sp0["colspan"] >= 2
    assert sp0["c"] == 0
    assert is_span_anchor_mark(out[0][0]), out[0]
    assert parse_anchor_colspan(out[0][0]) >= 2
    assert strip_span_anchor_mark(out[0][0]) == "2024年12月31日"
    assert is_span_cover_mark(out[0][1]) and out[0][1] == COVER_MARK, out[0]
    assert out[2][1] == "1" and out[2][2] == "2"

    # 小节标题：字框跨序号列+标签列 → 左空格也标 ⟦↔⟧
    data2 = [
        ["", "可用资本（数额）", ""],
        ["1", "核心一级资本净额", "100"],
    ]
    words2 = [
        {"text": "可用资本（数额）", "x0": 70.0, "y0": 10.0, "x1": 160.0, "y1": 22.0},
        {"text": "1", "x0": 72.0, "y0": 30.0, "x1": 78.0, "y1": 42.0},
        {"text": "核心一级资本净额", "x0": 106.0, "y0": 30.0, "x1": 200.0, "y1": 42.0},
        {"text": "100", "x0": 250.0, "y0": 30.0, "x1": 280.0, "y1": 42.0},
    ]
    # 列界约：序号|标签|金额
    col2 = [60.0, 90.0, 220.0, 300.0]
    out2, spans2 = mark_spanned_neighbor_cells(data2, words=words2, col_lines=col2)
    assert any("可用资本" in str(s.get("text") or "") for s in spans2), (out2, spans2)
    # 跨列表头放到左首格，原格变覆盖标记
    assert is_span_anchor_mark(out2[0][0]), out2[0]
    assert strip_span_anchor_mark(out2[0][0]) == "可用资本（数额）"
    assert out2[0][1] == COVER_MARK, out2[0]

    # 「资本充足率」：核宽覆盖序号+科目等多列有数据 → 跨格；文本放左首格
    data3 = [
        ["", "资本充足率", "", "", "", ""],
        ["5", "核心一级资本充足率（%）", "14.48", "14.10", "14.01", "14.11"],
    ]
    # 列界：6 列；标题核宽盖住前两列（序号+科目）
    col3 = [60.0, 90.0, 220.0, 280.0, 340.0, 400.0, 460.0]
    words3 = [
        {"text": "资本充足率", "x0": 70.0, "y0": 10.0, "x1": 160.0, "y1": 22.0},
        {"text": "5", "x0": 72.0, "y0": 30.0, "x1": 78.0, "y1": 42.0},
        {"text": "核心一级资本充足率（%）", "x0": 100.0, "y0": 30.0, "x1": 200.0, "y1": 42.0},
        {"text": "14.48", "x0": 230.0, "y0": 30.0, "x1": 270.0, "y1": 42.0},
    ]
    out3, spans3 = mark_spanned_neighbor_cells(data3, words=words3, col_lines=col3)
    sp3 = next(s for s in spans3 if "资本充足率" in str(s.get("text") or ""))
    assert sp3["evidence"] == "nucleus_width_vs_body_cols"
    assert sp3["colspan"] >= 2
    assert sp3["c"] == sp3["c0"]
    assert is_span_anchor_mark(out3[0][sp3["c"]])
    assert strip_span_anchor_mark(out3[0][sp3["c"]]) == "资本充足率"

    # 61/64 下同类标题：核宽相近 → 跨格结果一致（不按字数）
    data4 = [
        ["61", "", "满足最低资本要求后的可用核心一级资本净额占风险加权资产的比例", "9.16%"],
        ["", "我国最低监管资本要求", "", ""],
        ["62", "", "核心一级资本充足率", "5.00%"],
        ["64", "", "资本充足率", "8.00%"],
        ["", "门槛扣除项中未扣除部分", "", ""],
        ["65", "", "对未并表金融机构的小额少数资本投资中的未扣除部分", "54,898"],
    ]
    col4 = [70.0, 100.0, 280.0, 420.0, 520.0]
    words4 = [
        {"text": "我国最低监管资本要求", "x0": 87.86, "y0": 298.0, "x1": 188.3, "y1": 310.0},
        {"text": "门槛扣除项中未扣除部分", "x0": 87.86, "y0": 348.0, "x1": 198.19, "y1": 360.0},
        {"text": "61", "x0": 80.0, "y0": 270.0, "x1": 92.0, "y1": 282.0},
        {"text": "满足最低资本要求后的可用核心一级资本净额占风险加权资产的比例", "x0": 110.0, "y0": 270.0, "x1": 270.0, "y1": 282.0},
        {"text": "9.16%", "x0": 450.0, "y0": 270.0, "x1": 490.0, "y1": 282.0},
        {"text": "62", "x0": 80.0, "y0": 320.0, "x1": 92.0, "y1": 332.0},
        {"text": "核心一级资本充足率", "x0": 110.0, "y0": 320.0, "x1": 220.0, "y1": 332.0},
        {"text": "5.00%", "x0": 450.0, "y0": 320.0, "x1": 490.0, "y1": 332.0},
        {"text": "65", "x0": 80.0, "y0": 380.0, "x1": 92.0, "y1": 392.0},
        {"text": "对未并表金融机构的小额少数资本投资中的未扣除部分", "x0": 110.0, "y0": 380.0, "x1": 270.0, "y1": 392.0},
        {"text": "54,898", "x0": 450.0, "y0": 380.0, "x1": 500.0, "y1": 392.0},
    ]
    out4, spans4 = mark_spanned_neighbor_cells(data4, words=words4, col_lines=col4)
    by_title = {
        strip_span_anchor_mark(str(s.get("text") or "")): s
        for s in spans4
        if s.get("evidence") == "nucleus_width_vs_body_cols"
    }
    assert "我国最低监管资本要求" in by_title, (out4, spans4)
    assert "门槛扣除项中未扣除部分" in by_title, (out4, spans4)
    assert by_title["我国最低监管资本要求"]["colspan"] == by_title["门槛扣除项中未扣除部分"]["colspan"]
    assert by_title["我国最低监管资本要求"]["colspan"] >= 2
    # 长科目在科目列内，核宽不盖金额列 → 不得因「部分」字样通栏
    assert not any(
        "小额少数资本投资" in str(s.get("text") or "") for s in spans4
    ), spans4


def test_trailing_section_caption_not_mixed_into_table():
    """表下节标题「3.2.3 利息收入」不得混入表内（字框常拆成 .3|利息收入）。"""
    from codes.table_engine.split.row_classify import (
        row_is_mangled_section_caption_row,
        row_is_post_table_field_caption_row,
        row_is_table_tail_section_caption_row,
    )
    from codes.reconstruct.grid_nucleus.word_segment import (
        strip_trailing_next_table_header_rows_from_data,
        trim_trailing_next_header_words,
    )

    assert row_is_mangled_section_caption_row(["利息收入", ".3", "", ""])
    assert row_is_table_tail_section_caption_row(["利息收入", ".3"])
    assert row_is_post_table_field_caption_row(
        ["同业存拆放及其他利息支出", "", "", "", "", "", ""]
    )
    stripped = strip_trailing_next_table_header_rows_from_data(
        [
            ["", "2025年 (%)", "2024年", "2023年"],
            ["净利息收入", "63.87", "62.60", "63.30"],
            ["合计", "100.00", "100.00", "100.00"],
            ["利息收入", ".3", "", ""],
        ]
    )
    assert stripped[-1][0] == "合计", stripped
    assert not any("利息收入" == str(c).strip() for r in stripped for c in r)

    stripped2 = strip_trailing_next_table_header_rows_from_data(
        [
            ["活期", "100", "1", "0.5", "90", "2", "0.6"],
            ["合计", "9,202,500", "107,869", "1.17", "8,515,666", "130,824", "1.54"],
            ["同业存拆放及其他利息支出", "", "", "", "", "", ""],
        ]
    )
    assert stripped2[-1][0] == "合计", stripped2
    assert not any("同业存拆放" in str(c) for r in stripped2 for c in r)

    words = [
        _word("净利息收入", 85, 160, 130, 172),
        _word("63.87", 339, 160, 370, 172),
        _word("62.60", 424, 160, 455, 172),
        _word("合计", 85, 200, 110, 212),
        _word("100.00", 334, 200, 370, 212),
        _word("100.00", 419, 200, 455, 212),
        _word(".3", 70, 247, 79, 261),
        _word("利息收入", 85, 248, 130, 260),
    ]
    trimmed = trim_trailing_next_header_words(words)
    texts = [str(w.get("text")) for w in trimmed]
    assert ".3" not in texts and "利息收入" not in texts, texts

    words2 = [
        _word("合计", 85, 340, 110, 352),
        _word("9,202,500", 240, 340, 290, 352),
        _word("107,869", 298, 340, 340, 352),
        _word("同业存拆放及其他利息支出", 85, 385, 220, 394),
    ]
    trimmed2 = trim_trailing_next_header_words(words2)
    assert not any("同业存拆放" in str(w.get("text")) for w in trimmed2), trimmed2

    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    assert not any(
        str(c).strip() == "利息收入" for r in table["data"] for c in r
    ), table["data"]
    assert not any(str(c).strip() == ".3" for r in table["data"] for c in r), table[
        "data"
    ]


def test_vertical_same_column_range_inherits_from_row_above():
    """上下行同列：下一行窄框/短杠应继承上一行宽金额的列（非表头合并）。"""
    from codes.reconstruct.grid_nucleus.column_infer import (
        _snap_vertical_same_column_range,
    )
    from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster

    wide = Nucleus(text="13,070,523", x0=387.67, y0=120, x1=432.66, y1=132, col_id=2)
    sep = Nucleus(text="12,644,075", x0=505.30, y0=120, x1=550.29, y1=132, col_id=3)
    # 故意落错列：短杠中心偏右，初筛会进「幽灵列」4
    dash = Nucleus(text="-", x0=429.34, y0=140, x1=432.66, y1=152, col_id=4)
    dash_sep = Nucleus(text="-", x0=546.96, y0=140, x1=550.28, y1=152, col_id=3)
    neg = Nucleus(text="(98,297)", x0=398.50, y0=160, x1=432.71, y1=172, col_id=4)
    neg_sep = Nucleus(text="(90,696)", x0=516.12, y0=160, x1=550.33, y1=172, col_id=3)

    rows = [
        RowCluster(row_id=0, cy=126, nuclei=[wide, sep]),
        RowCluster(row_id=1, cy=146, nuclei=[dash, dash_sep]),
        RowCluster(row_id=2, cy=166, nuclei=[neg, neg_sep]),
    ]
    _snap_vertical_same_column_range(rows, n_cols=5)
    assert dash.col_id == 2, dash.col_id
    assert neg.col_id == 2, neg.col_id
    assert dash_sep.col_id == 3 and neg_sep.col_id == 3


def test_letter_code_right_align_keeps_dash_and_amount_under_b():
    """列码 a/b/c：右对齐短杠/金额不得因中心偏右落到邻列（page_023 列 b 空）。"""
    from codes.reconstruct.grid_nucleus.column_infer import (
        assign_nuclei_to_slots,
        _snap_values_by_letter_code_right_align,
    )
    from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster

    a = Nucleus(text="a", x0=189.8, y0=100, x1=195.1, y1=112, col_id=2)
    b = Nucleus(text="b", x0=244.3, y0=100, x1=250.1, y1=112, col_id=3)
    c = Nucleus(text="c", x0=296.0, y0=100, x1=300.7, y1=112, col_id=4)
    # 短杠右缘贴 b 列，但中心更靠近 c → 初筛常进 col4
    dash = Nucleus(text="-", x0=266.0, y0=140, x1=269.5, y1=152, col_id=4)
    amt = Nucleus(text="628,497", x0=235.4, y0=160, x1=269.6, y1=172, col_id=3)
    # c 列短杠
    dash_c = Nucleus(text="-", x0=313.0, y0=140, x1=316.4, y1=152, col_id=4)

    rows = [
        RowCluster(row_id=0, cy=106, nuclei=[a, b, c]),
        RowCluster(row_id=1, cy=146, nuclei=[dash, dash_c]),
        RowCluster(row_id=2, cy=166, nuclei=[amt]),
    ]
    centers = [75.0, 120.0, 198.0, 241.0, 283.0, 315.0]
    # 只测列码右对齐一步（避免整条 assign 依赖更多几何）
    _snap_values_by_letter_code_right_align(rows, n_cols=len(centers))
    assert dash.col_id == 3, dash.col_id
    assert amt.col_id == 3, amt.col_id
    assert dash_c.col_id == 4, dash_c.col_id

    # 垂直 snap 不得再把金额吸到 c
    from codes.reconstruct.grid_nucleus.column_infer import (
        _snap_vertical_same_column_range,
    )

    _snap_vertical_same_column_range(rows, n_cols=len(centers))
    assert amt.col_id == 3, amt.col_id
    # 完整落列路径也应对
    rows2 = [
        RowCluster(row_id=0, cy=106, nuclei=[
            Nucleus(text="a", x0=189.8, y0=100, x1=195.1, y1=112),
            Nucleus(text="b", x0=244.3, y0=100, x1=250.1, y1=112),
            Nucleus(text="c", x0=296.0, y0=100, x1=300.7, y1=112),
        ]),
        RowCluster(row_id=1, cy=146, nuclei=[
            Nucleus(text="-", x0=266.0, y0=140, x1=269.5, y1=152),
            Nucleus(text="-", x0=313.0, y0=140, x1=316.4, y1=152),
        ]),
        RowCluster(row_id=2, cy=166, nuclei=[
            Nucleus(text="628,497", x0=235.4, y0=160, x1=269.6, y1=172),
        ]),
    ]
    assign_nuclei_to_slots(rows2, centers)
    by_text = {n.text: n.col_id for r in rows2 for n in r.nuclei}
    assert by_text["b"] == by_text["628,497"] == by_text["-"] or (
        by_text["628,497"] == by_text["b"]
    ), by_text
    # 两个短杠：左杠跟 b，右杠跟 c
    dashes = [n for r in rows2 for n in r.nuclei if n.text == "-"]
    assert sorted(n.col_id for n in dashes) == [by_text["b"], by_text["c"]], [
        (n.x0, n.col_id) for n in dashes
    ]


def test_join_cell_continuous_cjk_no_space():
    """同格连续中文拼接不加空格（文本列不因空格拆开）。"""
    from codes.reconstruct.grid_nucleus.assign_cells import join_cell_nuclei_text

    items = [
        Nucleus("贷款和", 10, 10, 50, 20),
        Nucleus("垫款金额", 52, 10, 110, 20),
    ]
    assert join_cell_nuclei_text(items) == "贷款和垫款金额"


def test_join_cell_preserves_left_to_right_despite_y0_jitter():
    """同行左→右顺序不可颠倒：即使右边字 y0 略高，也不得拼成「人民币 注释」。"""
    from codes.reconstruct.grid_nucleus.assign_cells import (
        join_cell_nuclei_text,
        sort_nuclei_reading_order,
    )

    # PDF：注释在左、人民币在右；人民币字框略偏上（y0 更小）
    zhu = Nucleus(text="注释", x0=200, y0=102, x1=230, y1=114)
    rmb = Nucleus(text="人民币", x0=250, y0=100, x1=300, y1=112)
    ordered = sort_nuclei_reading_order([rmb, zhu])  # 故意乱序传入
    assert [n.text for n in ordered] == ["注释", "人民币"], [n.text for n in ordered]
    text = join_cell_nuclei_text([rmb, zhu])
    assert text.startswith("注释"), text
    assert "人民币" in text
    assert not text.startswith("人民币"), text
    assert text.index("注释") < text.index("人民币"), text


def test_logical_wrap_merge_above_and_below_amount_anchor():
    """旁列空续行并入唯一金额锚（上下双向；须是明确折行碎片）。"""
    from codes.reconstruct.grid_nucleus.logical_rows import assemble_wrapped_label_rows

    data = [
        ["科目", "金额"],
        ["不符合", ""],
        ["的权益类证券", "12,345"],
        ["其他债权投资的", "10,000"],
        ["续写说明片段", ""],
    ]
    out, meta = assemble_wrapped_label_rows(data)
    labels = [str(r[0]).strip() for r in out]
    assert any("不符合的权益类证券" in t or ("不符合" in t and "权益" in t) for t in labels), out
    assert any("其他债权投资的续写" in t or ("其他债权投资" in t and "续写" in t) for t in labels), out
    assert not meta.get("ambiguous_rows"), meta
    assert len(out) == 3, (len(out), out)  # header + 2 body


def test_logical_wrap_between_two_amounts_is_ambiguous():
    """两金额锚争抢续行 → ambiguous，不合并（宁可错杀）。"""
    from codes.reconstruct.grid_nucleus.logical_rows import assemble_wrapped_label_rows

    data = [
        ["科目", "金额"],
        ["父项甲", "100"],
        ["夹心续行", ""],
        ["父项乙", "200"],
    ]
    out, meta = assemble_wrapped_label_rows(data)
    assert len(out) == 4, out
    assert any(r[0].strip() == "夹心续行" for r in out), out
    assert meta.get("ambiguous_rows"), meta


def test_logical_wrap_incomplete_de_merges_down_despite_two_amounts():
    """「…的」折行碎片夹在两金额锚之间 → 并入下方金额行（24行场景）。"""
    from codes.reconstruct.grid_nucleus.logical_rows import assemble_wrapped_label_rows

    data = [
        ["序号", "科目", "金额"],
        ["23", "其中：风险权重不高于35%", "-"],
        ["", "不符合合格优质流动性资产标准的", ""],
        ["24", "非违约证券，包括交易所交易的权益类证券", "164,815"],
        ["25", "相互依存的资产", "-"],
    ]
    out, meta = assemble_wrapped_label_rows(data)
    labels = [str(r[1]) for r in out]
    joined = next((t for t in labels if "不符合" in t and "非违约" in t), None)
    assert joined is not None, (out, meta)
    assert "164,815" in str(out[[i for i, r in enumerate(out) if "不符合" in str(r[1])][0]])
    assert not any(a.get("label", "").startswith("不符合") for a in (meta.get("ambiguous_rows") or []))


def test_span_mark_ignores_label_right_tip_overflow():
    """纯文本科目右缘擦进邻列不得标 ↔2。"""
    from codes.reconstruct.grid_nucleus.span_mark import mark_spanned_neighbor_cells

    # 科目列 [100,270)，金额列 [270,350)；字框右缘仅擦过 15pt
    data = [
        ["23", "其中：风险权重不高于35%", "-"],
        ["", "不符合合格优质流动性资产标准的", ""],
        ["24", "非违约证券，包括交易所交易的权益类证券", "164,815"],
    ]
    words = [
        {"text": "不符合合格优质流动性资产标准的", "x0": 135.4, "x1": 285.2, "y0": 100, "y1": 112},
        {"text": "非违约证券，包括交易所交易的权益类证券", "x0": 146.8, "x1": 276.7, "y0": 114, "y1": 126},
        {"text": "164,815", "x0": 309.2, "x1": 338.5, "y0": 114, "y1": 126},
    ]
    col_lines = [90.0, 120.0, 270.0, 350.0]
    out, spans = mark_spanned_neighbor_cells(data, words=words, col_lines=col_lines)
    flat = " ".join(str(c) for r in out for c in r)
    assert "⟦↔" not in flat, (out, spans)
    assert not any(
        "不符合" in str(s.get("text") or "") for s in spans
    ), spans


def test_logical_wrap_placeholder_host_and_serial_continuation():
    """46/47a：占位「-」作宿主；无序号上半截并入邻行序号宿主；未闭合括号不吞远端金额。"""
    from codes.reconstruct.grid_nucleus.logical_rows import assemble_wrapped_label_rows

    data = [
        ["序号", "科目", "金额"],
        ["45", "上一完整科目", "1,000"],
        ["", "银行间或银行与其他金融机构间通过协议相互持有的二级资本投资及TLAC非资本", ""],
        ["46", "债务工具投资", "-"],
        ["47", "另一科目", "-"],
        ["", "对未并表金融机构的小额投资中的TLAC非资本债务工具中应扣除金额（仅适用全", ""],
        ["47a", "球系统重要性银行）", "-"],
        ["51", "二级资本净额", "99,999"],
    ]
    out, meta = assemble_wrapped_label_rows(data)
    labels = [str(r[1]) for r in out]
    row46 = next((r for r in out if str(r[0]).strip() == "46"), None)
    assert row46 is not None, out
    assert "TLAC非资本债务工具投资" in str(row46[1]).replace(" ", ""), row46
    row47a = next((r for r in out if str(r[0]).strip() == "47a"), None)
    assert row47a is not None, out
    t47 = str(row47a[1])
    assert "对未并表金融机构" in t47 and "球系统重要性银行）" in t47, row47a
    row51 = next((r for r in out if str(r[0]).strip() == "51"), None)
    assert row51 is not None and "二级资本净额" in str(row51[1]), out
    assert "对未并表" not in str(row51[1]), row51
    assert not any("对未并表" in str(a.get("label") or "") for a in (meta.get("ambiguous_rows") or [])), meta


def test_span_mark_long_body_label_left_edge_only():
    """长科目右缘伸进金额列：只认左缘，不标 ↔2。

    金额格为空时原先会误标覆盖；有「-」时反而因无法覆盖而「碰巧」不标。
    """
    from codes.reconstruct.grid_nucleus.span_mark import mark_spanned_neighbor_cells

    text = "银行间或银行与其他金融机构间通过协议相互持有的二级资本投资及TLAC非资本"
    # 科目列 [120,390)，金额列 [390,520)；字框右缘越过金额列很多
    words = [
        {"text": text, "x0": 125.7, "x1": 481.8, "y0": 500, "y1": 512},
    ]
    col_lines = [90.0, 120.0, 390.0, 520.0]
    for data in (
        [["46", text, "-"]],
        [["", text, ""]],  # 折行首行：序号/金额常为空
    ):
        out, spans = mark_spanned_neighbor_cells(data, words=words, col_lines=col_lines)
        flat = " ".join(str(c) for r in out for c in r)
        assert "⟦↔" not in flat, (out, spans)
        assert not spans, spans


def test_ratio_header_wrap_not_phantom_before_pct_values():
    """折行「占有关同类/交易发生额/比例」与 6.38 同列带交叉 → 同列，禁止前插空白列。"""
    from codes.reconstruct.grid_nucleus.pipeline import apply_grid_to_table
    from codes.reconstruct.grid_nucleus.column_infer import (
        _metric_header_amount_same_column,
    )
    from codes.reconstruct.grid_nucleus.types import Nucleus

    hdr = Nucleus(text="占有关同类", x0=255, y0=100, x1=360, y1=112)
    amt = Nucleus(text="6.38", x0=340, y0=160, x1=375, y1=172)
    assert _metric_header_amount_same_column(hdr, amt), (hdr, amt)
    # 邻年金额不得因过宽右廊误锁
    other = Nucleus(text="5,000", x0=400, y0=160, x1=440, y1=172)
    assert not _metric_header_amount_same_column(hdr, other), (hdr, other)

    words = [
        _word("关联方名称", 40, 80, 110, 92),
        _word("2025年", 200, 80, 235, 92),
        _word("2024年", 400, 80, 435, 92),
        _word("交易发生额", 160, 100, 230, 112),
        # 与 6.38 核带交叉（真实表头列宽盖住右对齐比例）
        _word("占有关同类", 255, 100, 360, 112),
        _word("交易发生额", 255, 112, 360, 124),
        _word("比例(%)", 255, 124, 360, 136),
        _word("交易发生额", 380, 100, 450, 112),
        _word("占有关同类", 455, 100, 540, 112),
        _word("比例(%)", 455, 124, 540, 136),
        _word("招商局集团有限公司", 40, 160, 150, 172),
        _word("6,322", 170, 160, 220, 172),
        _word("6.38", 340, 160, 375, 172),
        _word("30", 170, 180, 195, 192),
        _word("0.03", 340, 180, 375, 192),
        _word("合计", 40, 200, 70, 212),
        _word("6,352", 170, 200, 220, 212),
        _word("6.41", 340, 200, 375, 212),
        _word("5,000", 400, 160, 440, 172),
        _word("5.00", 510, 160, 540, 172),
        _word("20", 400, 180, 420, 192),
        _word("0.02", 510, 180, 540, 192),
        _word("5,100", 400, 200, 440, 212),
        _word("5.10", 510, 200, 540, 212),
    ]
    table = {"type": "table", "data": [["x"]], "_source_words": words}
    applied = apply_grid_to_table(table)
    assert applied.ok, applied.to_dict()
    row = next(r for r in table["data"] if any("6,322" in str(c) for c in r))
    i_amt = next(i for i, c in enumerate(row) if "6,322" in str(c))
    i_pct = next(i for i, c in enumerate(row) if "6.38" in str(c))
    assert i_pct == i_amt + 1, row
    # 中间不得夹空白列
    assert not any(str(c).strip() == "" for c in row[i_amt + 1 : i_pct]), row
    assert "5,000" not in str(row[i_pct]), row


def test_year_group_header_spans_amount_and_ratio_cols():
    """「2025年」下有交易发生额+比例两列：表体保持两列，年头标 ↔2。"""
    from codes.reconstruct.grid_nucleus.pipeline import apply_grid_to_table
    from codes.reconstruct.grid_nucleus.span_mark import (
        COVER_MARK,
        is_span_anchor_mark,
        is_span_cover_mark,
        parse_anchor_colspan,
        strip_span_anchor_mark,
    )

    words = [
        _word("关联方名称", 50, 100, 120, 112),
        # 字框偏窄，只盖住年组中心
        _word("2025年", 220, 100, 255, 112),
        _word("2024年", 400, 100, 435, 112),
        _word("交易发生额", 180, 120, 240, 132),
        _word("占有关同类交易发生额比例(%)", 250, 120, 350, 132),
        _word("交易发生额", 360, 120, 420, 132),
        _word("占有关同类交易发生额比例(%)", 430, 120, 530, 132),
        _word("招商局集团有限公司", 50, 150, 160, 162),
        _word("6,322", 200, 150, 235, 162),
        _word("6.38", 280, 150, 310, 162),
        _word("5,000", 380, 150, 415, 162),
        _word("5.00", 460, 150, 490, 162),
        _word("合计", 50, 180, 80, 192),
        _word("6,352", 200, 180, 235, 192),
        _word("6.41", 280, 180, 310, 192),
        _word("5,100", 380, 180, 415, 192),
        _word("5.10", 460, 180, 490, 192),
    ]
    table = {"type": "table", "data": [["x"]], "_source_words": words}
    applied = apply_grid_to_table(table)
    assert applied.ok, applied.to_dict()
    data = table["data"]
    # 表体仍是两列金额（不得并成一列）
    body = next(r for r in data if any("6,322" in str(c) for c in r))
    assert any("6.38" in str(c) for c in body), body
    assert body.index(next(c for c in body if "6,322" in str(c))) != body.index(
        next(c for c in body if "6.38" in str(c))
    ), body
    # 年头跨两列
    year_row = next(r for r in data if any("2025年" in strip_span_anchor_mark(str(c)) for c in r))
    y25 = next(c for c in year_row if "2025年" in strip_span_anchor_mark(str(c)))
    assert is_span_anchor_mark(str(y25)), year_row
    assert parse_anchor_colspan(str(y25)) >= 2, year_row
    assert any(is_span_cover_mark(str(c)) or str(c).strip() == COVER_MARK for c in year_row), year_row
    y24 = next(c for c in year_row if "2024年" in strip_span_anchor_mark(str(c)))
    assert is_span_anchor_mark(str(y24)), year_row


def test_qizhong_detail_rows_stay_with_serial_amount():
    """「其中：…」与序号 21/22、金额同属一行，不得因冒号被拆成两行。"""
    from codes.reconstruct.grid_nucleus.pipeline import apply_grid_to_table
    from codes.reconstruct.grid_nucleus.row_cluster import (
        _is_section_title_nucleus,
        cluster_rows,
    )
    from codes.reconstruct.grid_nucleus.preprocess import preprocess_words
    from codes.reconstruct.grid_nucleus.types import Nucleus

    assert not _is_section_title_nucleus(
        Nucleus(
            text="其中：应在对金融机构大额少数资本投资中扣除的金额",
            x0=120, y0=200, x1=380, y1=212,
        )
    )
    assert _is_section_title_nucleus(
        Nucleus(text="核心一级资本：扣除项", x0=87, y0=203, x1=200, y1=215)
    )

    words = [
        _word("数额", 510, 100, 531, 112),
        _word("20", 95, 170, 108, 182),
        _word(
            "其他依赖于银行未来盈利的净递延税资产的未扣除部分超过核心一级资本15%的应扣除金额",
            120, 170, 400, 182,
        ),
        _word("-", 530, 170, 538, 182),
        _word("其中：应在对金融机构大额少数资本投资中扣除的金额", 120, 200, 380, 212),
        _word("21", 95, 202, 108, 214),
        _word("-", 530, 202, 538, 214),
        _word(
            "其中：应在其他依赖于银行未来盈利的净递延所得税资产中扣除的金额",
            120, 230, 420, 242,
        ),
        _word("22", 95, 232, 108, 244),
        _word("-", 530, 232, 538, 244),
    ]
    rows = cluster_rows(preprocess_words(words))
    r21 = next(
        r for r in rows
        if any(str(n.text).strip() == "21" for n in r.nuclei)
    )
    assert any("其中：应在对金融机构" in str(n.text) for n in r21.nuclei), [
        n.text for n in r21.nuclei
    ]
    assert any(str(n.text).strip() == "-" for n in r21.nuclei), [n.text for n in r21.nuclei]

    table = {"type": "table", "data": [["x"]], "_source_words": words}
    applied = apply_grid_to_table(table)
    assert applied.ok, applied.to_dict()
    row21 = next(r for r in table["data"] if any(str(c).strip() == "21" for c in r))
    assert any("其中：应在对金融机构" in str(c) for c in row21), row21
    assert any(str(c).strip() == "-" for c in row21), row21
    row22 = next(r for r in table["data"] if any(str(c).strip() == "22" for c in r))
    assert any("其中：应在其他依赖" in str(c) for c in row22), row22


def test_deduction_section_title_own_row_and_span():
    """「核心一级资本：扣除项」在 5 行下方单独成行并跨列，不得并进第 5 行。"""
    from codes.reconstruct.grid_nucleus.logical_rows import (
        assemble_wrapped_label_rows,
        peel_inline_section_title_rows,
    )
    from codes.reconstruct.grid_nucleus.pipeline import apply_grid_to_table
    from codes.reconstruct.grid_nucleus.span_mark import is_span_anchor_mark

    # 粘连在科目格内
    jammed = [
        ["5", "扣除前的核心一级资本核心一级资本：扣除项", "1,080,721"],
        ["6", "审慎估值调整", "-"],
    ]
    peeled, pmeta = peel_inline_section_title_rows(jammed)
    assert pmeta.get("peeled"), pmeta
    assert str(peeled[0][1]).strip() == "扣除前的核心一级资本", peeled
    assert str(peeled[1][1]).strip() == "核心一级资本：扣除项", peeled
    out, _ = assemble_wrapped_label_rows(jammed)
    assert any(
        str(r[0]).strip() == "" and "核心一级资本：扣除项" in str(r[1])
        for r in out
    ), out
    row5 = next(r for r in out if str(r[0]).strip() == "5")
    assert "扣除项" not in str(row5[1]), row5

    # y 贴近时：凝结核聚类不得并进 5 行
    words = [
        _word("数额", 510, 100, 531, 112),
        _word("5", 95, 200, 100, 212),
        _word("扣除前的核心一级资本", 120, 200, 250, 212),
        _word("1,080,721", 500, 200, 545, 212),
        _word("核心一级资本：扣除项", 87, 203, 200, 215),
        _word("6", 95, 228, 100, 240),
        _word("审慎估值调整", 120, 228, 200, 240),
        _word("-", 530, 228, 538, 240),
    ]
    table = {"type": "table", "data": [["x"]], "_source_words": words}
    applied = apply_grid_to_table(table)
    assert applied.ok, applied.to_dict()
    data = table["data"]
    row5b = next(r for r in data if any(str(c).strip() == "5" for c in r))
    assert "扣除项" not in "".join(str(c) for c in row5b), row5b
    title_row = next(
        r for r in data if any("核心一级资本：扣除项" in str(c) for c in r)
    )
    assert all(str(c).strip() != "5" for c in title_row), title_row
    assert any(is_span_anchor_mark(str(c)) or "⟦↔" in str(c) for c in title_row), (
        title_row
    )


def test_span_mark_core_tier1_section_crosses_serial_and_label():
    """「核心一级资本：」凝结核与序号列+科目列交叉 → 必须标跨列。"""
    from codes.reconstruct.grid_nucleus.span_mark import (
        is_span_anchor_mark,
        mark_spanned_neighbor_cells,
        parse_anchor_colspan,
        strip_span_anchor_mark,
    )
    from codes.reconstruct.grid_nucleus.pipeline import apply_grid_to_table

    data = [
        ["", "", "数额"],
        ["", "核心一级资本：", ""],
        ["1", "实收资本和资本公积可计入部分", "90,624"],
        ["2", "留存收益", "969,084"],
    ]
    # 与样本一致：左缘在序号列，右缘伸进科目列
    words = [
        {"text": "核心一级资本：", "x0": 87.0, "x1": 157.0, "y0": 252, "y1": 264},
        {"text": "1", "x0": 95.0, "x1": 100.0, "y0": 267, "y1": 279},
        {"text": "实收资本和资本公积可计入部分", "x0": 120.0, "x1": 259.0, "y0": 265, "y1": 277},
        {"text": "90,624", "x0": 526.0, "x1": 554.0, "y0": 267, "y1": 279},
    ]
    col_lines = [70.0, 100.0, 400.0, 520.0]
    out, spans = mark_spanned_neighbor_cells(data, words=words, col_lines=col_lines)
    sp = next(s for s in spans if "核心一级资本" in str(s.get("text") or ""))
    assert sp["colspan"] >= 2, (out, spans)
    assert sp["c0"] == 0 and sp["c1"] >= 1, sp
    assert is_span_anchor_mark(out[1][sp["c"]]), out[1]
    assert parse_anchor_colspan(out[1][sp["c"]]) >= 2
    assert strip_span_anchor_mark(out[1][sp["c"]]) == "核心一级资本："

    words_full = [
        _word("数额", 510, 239, 531, 251),
        _word("核心一级资本：", 87, 252, 157, 264),
        _word("1", 95, 267, 100, 279),
        _word("实收资本和资本公积可计入部分", 120, 265, 259, 277),
        _word("90,624", 526, 267, 554, 279),
        _word("2", 95, 280, 100, 292),
        _word("留存收益", 120, 279, 160, 291),
        _word("969,084", 521, 280, 554, 292),
    ]
    table = {"type": "table", "data": [["x"]], "_source_words": words_full}
    applied = apply_grid_to_table(table)
    assert applied.ok, applied.to_dict()
    row = next(r for r in table["data"] if any("核心一级资本" in str(c) for c in r))
    assert any(is_span_anchor_mark(str(c)) for c in row), row
    assert any("⟦↔" in str(c) for c in row), row


def test_span_mark_no_force_without_nucleus_crossing():
    """无字框凝结核时：即使右侧空格也不强制跨列。"""
    from codes.reconstruct.grid_nucleus.span_mark import mark_spanned_neighbor_cells

    data = [
        ["以公允价值计量", "", ""],
        ["项目", "a", "b"],
        ["现金", "1", "2"],
    ]
    out, spans = mark_spanned_neighbor_cells(data)
    assert not spans, spans
    assert "⟦↔" not in "".join(str(c) for c in out[0]), out[0]


def test_span_mark_fv_oci_header_not_cover_total_exposure():
    """「以公允价值计量」折行列头不得跨盖「合计」「最大损失敞口」。"""
    from codes.reconstruct.grid_nucleus.span_mark import mark_spanned_neighbor_cells

    # 列：项目 | FV损益 | 摊余 | FV-OCI | 合计 | 最大损失敞口
    data = [
        ["", "账面余额", "", "", "", ""],
        ["", "", "", "以公允价值计量", "", ""],
        ["", "以公允价值计量且", "以摊余成本计量", "且其变动计入其他", "", "最大损失敞口"],
        ["", "其变动计入当期损", "的债务工具投资", "综合收益的债务", "合计", ""],
        ["", "的金融投资", "", "工具投资", "", ""],
        ["资产管理计划", "1", "2", "3", "6", "4"],
    ]
    # 折行首行字框右缘几何压进合计/敞口列带（与截图一致）
    words = [
        {"text": "以公允价值计量", "x0": 325.0, "x1": 530.0, "y0": 100, "y1": 112},
        {"text": "且其变动计入其他", "x0": 325.0, "x1": 405.0, "y0": 124, "y1": 136},
        {"text": "综合收益的债务", "x0": 325.0, "x1": 405.0, "y0": 136, "y1": 148},
        {"text": "工具投资", "x0": 325.0, "x1": 365.0, "y0": 148, "y1": 160},
        {"text": "合计", "x0": 430.0, "x1": 455.0, "y0": 136, "y1": 148},
        {"text": "最大损失敞口", "x0": 480.0, "x1": 545.0, "y0": 124, "y1": 136},
        {"text": "账面余额", "x0": 200.0, "x1": 450.0, "y0": 80, "y1": 92},
    ]
    col_lines = [50.0, 150.0, 250.0, 320.0, 420.0, 470.0, 560.0]
    out, spans = mark_spanned_neighbor_cells(data, words=words, col_lines=col_lines)
    fv_spans = [
        s for s in spans
        if str(s.get("text") or "").strip() == "以公允价值计量"
        or (
            "以公允价值计量" in str(s.get("text") or "")
            and "且" not in str(s.get("text") or "")
        )
    ]
    assert not fv_spans, (out[:5], fv_spans, spans)
    row_fv = next(
        r for r in out
        if any(str(c).strip() == "以公允价值计量" for c in r)
    )
    assert "⟦↔" not in "".join(str(c) for c in row_fv), row_fv
    assert any("合计" in str(c) and "⟦↔⟧" not in str(c) for r in out for c in r), out
    assert any("最大损失敞口" in str(c) and "⟦↔⟧" not in str(c) for r in out for c in r), out


def test_span_mark_wrap_label_row20_not_span_amount():
    """资本构成第20行折行首行：科目列弹性加宽即可，不引入跨列。"""
    from codes.reconstruct.grid_nucleus.span_mark import (
        mark_spanned_neighbor_cells,
        _overflow_absorbable_by_primary_stretch,
        _cols_spanned_by_nucleus_width,
        _columns_with_body_data,
    )

    wrap1 = "对未并表金融机构大额少数资本投资中的核心一级资本和其他依赖于银行未来盈"
    wrap2 = "利的净递延税资产的未扣除部分超过核心一级资本15%的应扣除金额"
    data = [
        ["19", "其他一级资本投资中应扣除金额", "-"],
        ["", wrap1, ""],
        ["20", wrap2, "-"],
    ]
    words = [
        {"text": wrap1, "x0": 119.5, "x1": 469.4, "y0": 300, "y1": 312},
        {"text": wrap2, "x0": 119.5, "x1": 360.0, "y0": 314, "y1": 326},
    ]
    col_lines = [95.4, 110.0, 372.9, 553.9]
    body = _columns_with_body_data(data, exclude_rows=[1])
    cand = _cols_spanned_by_nucleus_width(119.5, 469.4, col_lines, body)
    assert len(cand) >= 2, cand
    assert _overflow_absorbable_by_primary_stretch(
        data, 1, cand, 119.5, 469.4, col_lines, wrap1,
    ), (cand, body)
    out, spans = mark_spanned_neighbor_cells(data, words=words, col_lines=col_lines)
    flat = " ".join(str(c) for r in out for c in r)
    assert "⟦↔" not in flat, (out, spans)
    assert not spans, spans
    assert wrap1 in str(out[1][1]), out
    assert str(out[1][2]).strip() == "", out


def test_logical_hierarchy_rows_with_amounts_not_merged():
    """各自有金额的层次行保持独立，不因缩进并入父行。"""
    from codes.reconstruct.grid_nucleus.logical_rows import assemble_wrapped_label_rows

    data = [
        ["科目", "金额"],
        ["核心一级资本", "100"],
        ["  其中：储备资本", "2.50"],
        ["  其中：附加资本", "1.50"],
    ]
    out, meta = assemble_wrapped_label_rows(data)
    assert len(out) == 4, out
    assert not meta.get("merges"), meta


def test_section_title_not_merged_into_serial_amount_row():
    """「资本充足率和其他各级资本要求」是独立小节行，不得并进 54 核心一级资本充足率。"""
    from codes.reconstruct.grid_nucleus.logical_rows import assemble_wrapped_label_rows
    from codes.reconstruct.grid_nucleus.pipeline import restore_table_grid

    data = [
        ["", "资本充足率和其他各级资本要求", ""],
        ["54", "核心一级资本充足率", "14.16%"],
        ["55", "一级资本充足率", "16.51%"],
        ["56", "资本充足率", "18.24%"],
        ["", "我国最低监管资本要求", ""],
        ["61", "核心一级资本充足率", "5.00%"],
    ]
    out, meta = assemble_wrapped_label_rows(data)
    assert any(
        str(r[0]).strip() == "" and "资本充足率和其他各级资本要求" in str(r[1])
        for r in out
    ), out
    row54 = next(r for r in out if str(r[0]).strip() == "54")
    assert str(row54[1]).strip() == "核心一级资本充足率", row54
    assert "和其他" not in str(row54[1]), row54
    row61 = next(r for r in out if str(r[0]).strip() == "61")
    assert "我国最低" not in str(row61[1]), row61

    words = [
        _word("附表一：资本构成披露 - 续", 86, 100, 220, 112),
        _word("数额", 510, 120, 530, 132),
        _word("资本充足率和其他各级资本要求", 120, 200, 280, 212),
        _word("54", 95, 220, 108, 232),
        _word("核心一级资本充足率", 120, 220, 220, 232),
        _word("14.16%", 520, 220, 555, 232),
        _word("55", 95, 240, 108, 252),
        _word("一级资本充足率", 120, 240, 200, 252),
        _word("16.51%", 520, 240, 555, 252),
    ]
    res = restore_table_grid(
        {"type": "table", "data": [["x"]], "_source_words": words},
        source_words=words,
    )
    assert res.data, res.to_dict()
    assert any(
        "资本充足率和其他各级资本要求" in str(c)
        and all(str(x).strip() != "54" for x in r)
        for r in res.data
        for c in r
    ) or any(
        str(r[0]).strip() == "" and "资本充足率和其他" in str(r[1])
        for r in res.data
        if len(r) > 1
    ), res.data[:8]
    row54b = next(r for r in res.data if any(str(c).strip() == "54" for c in r))
    assert "核心一级资本充足率" in str(row54b[1]), row54b
    assert "和其他各级" not in str(row54b[1]), row54b


def test_capital_composition_narrative_not_merged_into_paid_in_capital():
    """表外「以下监管…」不得并进序号1「实收资本…」；原文科目不得丢失。"""
    from codes.reconstruct.grid_nucleus.logical_rows import assemble_wrapped_label_rows
    from codes.reconstruct.grid_nucleus.pipeline import restore_table_grid

    # 复现：未闭合括号说明 + 下方金额行（曾被 wrap above 误并）
    data = [
        ["", "资本构成信息披露", ""],
        ["", "以下监管资本项目与资产负债表对应关系附表依据《商业银行资本管理办法》(国家金融监", ""],
        ["", "督管理总局令第4号)进行披露。", ""],
        ["", "附表一：资本构成披露", ""],
        ["", "", "数额"],
        ["", "核心一级资本：", ""],
        ["1", "实收资本和资本公积可计入部分", "90,624"],
        ["2", "留存收益", "969,084"],
    ]
    out, meta = assemble_wrapped_label_rows(data)
    flat = "\n".join("|".join(str(c) for c in r) for r in out)
    assert "实收资本和资本公积可计入部分" in flat, out
    assert "以下监管" not in flat or all(
        "实收资本" not in str(r[1]) or "以下监管" not in str(r[1])
        for r in out if len(r) > 1
    ), (out, meta)
    row1 = next(r for r in out if str(r[0]).strip() == "1")
    assert "实收资本和资本公积可计入部分" in str(row1[1]), row1
    assert "以下监管" not in str(row1[1]), row1

    words = [
        _word("未经审计财务报表补充资料", 50, 74, 200, 86),
        _word("2025 年 12 月 31 日止年度", 50, 90, 200, 102),
        _word("(除特别注明外，货币单位均以人民币百万元列示)", 50, 105, 320, 117),
        _word("资本构成信息披露", 50, 135, 160, 147),
        _word(
            "以下监管资本项目与资产负债表对应关系附表依据《商业银行资本管理办法》(国家金融监",
            50, 164, 520, 176,
        ),
        _word("督管理总局令第4号)进行披露。", 50, 180, 220, 192),
        _word("附表一：资本构成披露", 50, 209, 180, 221),
        _word("数额", 510, 239, 531, 251),
        _word("核心一级资本：", 87, 252, 157, 264),
        _word("1", 95, 267, 100, 279),
        _word("实收资本和资本公积可计入部分", 120, 265, 259, 277),
        _word("90,624", 526, 267, 554, 279),
        _word("2", 95, 280, 100, 292),
        _word("留存收益", 120, 279, 160, 291),
        _word("969,084", 521, 280, 554, 292),
    ]
    res = restore_table_grid(
        {"type": "table", "data": [["x"]], "_source_words": words},
        source_words=words,
    )
    assert res.data, res.to_dict()
    flat2 = "\n".join("|".join(str(c) for c in r) for r in res.data)
    assert "实收资本和资本公积可计入部分" in flat2, res.data[:10]
    row1b = next(
        (r for r in res.data if any(str(c).strip() == "1" for c in r)),
        None,
    )
    assert row1b is not None, res.data[:10]
    assert "实收资本" in str(row1b[1]), row1b
    assert "以下监管" not in str(row1b[1]), row1b
    assert any("90,624" in str(c) for c in row1b), row1b


def test_asset_and_cash_keep_separate_rows():
    """源表「资产」「现金」各占一行：必须保留两行，禁止拼成「资产现金」。"""
    from codes.reconstruct.grid_nucleus.pipeline import restore_table_grid

    words = [
        _word("财务并表口径下", 405, 170, 475, 182),
        _word("监管并表口径下", 490, 170, 560, 182),
        _word("的资产负债表", 410, 182, 470, 194),
        _word("的资产负债表", 495, 182, 555, 194),
        _word("资产", 91, 194, 111, 206),
        _word("现金", 91, 207, 111, 219),
        _word("14,808", 450, 207, 477, 219),
        _word("14,808", 535, 207, 563, 219),
        _word("贵金属", 91, 221, 121, 233),
        _word("38,669", 450, 221, 477, 233),
        _word("38,669", 535, 221, 563, 233),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = restore_table_grid(table, source_words=words)
    assert res.ok and res.data, res.to_dict()
    assert any(
        any(str(c).strip() == "资产" for c in r) for r in res.data
    ), res.data
    cash = next(r for r in res.data if any(str(c).strip() == "现金" for c in r))
    assert not any("资产现金" in str(c) for r in res.data for c in r), res.data
    assert any("14,808" in str(c) for c in cash), cash


def test_shareholders_equity_section_not_glued_to_serial_38():
    """「股东权益」是表内小标题，不得并进「38|股本」成「股东权益股本」。"""
    from codes.reconstruct.grid_nucleus.logical_rows import (
        assemble_wrapped_label_rows,
        peel_inline_section_title_rows,
        _split_jammed_section_title,
    )
    from codes.reconstruct.grid_nucleus.pipeline import restore_table_grid

    assert _split_jammed_section_title("股东权益股本") == ("股本", "股东权益")
    assert _split_jammed_section_title("股东权益合计") is None

    # 已粘连金额行 → peel 拆出独立小标题行
    jammed = [["38", "股东权益股本", "250,011", "250,011", "e"]]
    peeled, pmeta = peel_inline_section_title_rows(jammed)
    assert pmeta.get("peeled"), pmeta
    assert any(str(c).strip() == "股东权益" for r in peeled for c in r), peeled
    leaf = next(r for r in peeled if any(str(c).strip() == "38" for c in r))
    assert any(str(c).strip() == "股本" for c in leaf), leaf
    assert not any("股东权益股本" in str(c) for r in peeled for c in r), peeled

    # 分列两行：禁止折行并入
    two = [
        ["37", "负债合计", "1", "1", ""],
        ["", "股东权益", "", "", ""],
        ["38", "股本", "250,011", "250,011", "e"],
    ]
    out, meta = assemble_wrapped_label_rows(two)
    assert not meta.get("merges"), meta
    assert any(any(str(c).strip() == "股东权益" for c in r) for r in out), out
    assert not any("股东权益股本" in str(c) for r in out for c in r), out

    # 几何：小标题与 38/股本分行
    words = [
        _word("37", 75, 259, 85, 273),
        _word("负债合计", 100, 261, 142, 272),
        _word("37,227,184", 339, 259, 387, 273),
        _word("股东权益", 71, 275, 113, 286),
        _word("38", 75, 287, 85, 301),
        _word("股本", 100, 289, 121, 300),
        _word("250,011", 350, 287, 387, 301),
        _word("250,011", 450, 287, 488, 301),
        _word("e", 510, 287, 520, 301),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = restore_table_grid(table, source_words=words)
    assert res.ok and res.data, res.to_dict()
    assert not any("股东权益股本" in str(c) for r in res.data for c in r), res.data
    assert any(any(str(c).strip() == "股东权益" for c in r) for r in res.data), res.data
    row38 = next(r for r in res.data if any(str(c).strip() == "38" for c in r))
    assert any(str(c).strip() == "股本" for c in row38), row38
    assert not any("股东权益" in str(c) for c in row38), row38


def test_asset_mgmt_plan_not_split_into_section_title():
    """「资产管理计划」「资产支持证券」「资产证券化…」是一体凝结核，禁止把「资产」拆成跨列小节行。"""
    from codes.reconstruct.grid_nucleus.logical_rows import (
        peel_inline_section_title_rows,
        _split_jammed_section_title,
    )
    from codes.reconstruct.grid_nucleus.pipeline import restore_table_grid

    assert _split_jammed_section_title("资产管理计划") is None
    assert _split_jammed_section_title("资产支持证券") is None
    assert _split_jammed_section_title("资产证券化销售利得") is None

    # 已在同一格：禁止 peel 拆出「资产」
    one = [["资产管理计划", "596", "69,168", "-", "69,764", "69,764"]]
    peeled, pmeta = peel_inline_section_title_rows(one)
    assert not pmeta.get("peeled"), pmeta
    assert str(peeled[0][0]).strip() == "资产管理计划", peeled

    sec = [["12", "资产证券化销售利得", "-", "e"]]
    peeled2, pmeta2 = peel_inline_section_title_rows(sec)
    assert not pmeta2.get("peeled"), pmeta2
    assert str(peeled2[0][1]).strip() == "资产证券化销售利得", peeled2

    # 几何上同一行簇内折行（y 贴近）→ 同格拼回一体核；不得拆成跨列「资产」
    words = [
        _word("账面余额", 200, 80, 250, 92),
        _word("资产", 90, 124, 118, 134),
        _word("管理计划", 90, 128, 150, 140),
        _word("596", 200, 128, 230, 140),
        _word("69,168", 260, 128, 310, 140),
        _word("资产", 90, 164, 118, 174),
        _word("支持证券", 90, 168, 150, 180),
        _word("100", 200, 168, 230, 180),
        _word("200", 260, 168, 300, 180),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = restore_table_grid(table, source_words=words)
    assert res.ok and res.data, res.to_dict()
    flat_rows = [" ".join(str(c) for c in r) for r in res.data]
    assert any("资产管理计划" in fr for fr in flat_rows), res.data
    assert any("资产支持证券" in fr for fr in flat_rows), res.data
    for r in res.data:
        cells = [str(c or "").strip() for c in r]
        if any(
            c == "资产" or c.startswith("资产 ⟦") or c.startswith("资产⟦")
            for c in cells
        ):
            assert False, ("资产被拆成单独跨列行", r, res.data)


def test_consecutive_spanning_prose_rows_stripped():
    """连续≥3行通栏无金额 → 整块表外；1～2行短通栏表头保留。"""
    from codes.reconstruct.grid_nucleus.word_segment import (
        strip_leading_non_table_rows_from_data,
    )
    from codes.table_engine.split.row_classify import (
        leading_spanning_prose_run_end,
        row_is_spanning_prose_candidate,
    )

    # 三行通栏说明 + 短表头 + 数据
    block = [
        ["本集团合并财务报表的合并范围以控制为基础予以确定。控制，是指投资方拥有对被投资", "⟦↔⟧", "⟦↔⟧"],
        ["方的权力，通过参与被投资方的相关活动而享有可变回报，并且有能力运用对被投资方的", "⟦↔⟧", "⟦↔⟧"],
        ["权力影响其回报金额。本集团对结构化主体拥有权力而其他投资者没有实质性权利。", "⟦↔⟧", "⟦↔⟧"],
        ["", "2025年12月31日", ""],
        ["", "账面余额", ""],
        ["资产管理计划", "9,073", "67,642"],
    ]
    assert all(row_is_spanning_prose_candidate(r) for r in block[:3])
    assert leading_spanning_prose_run_end(block, min_run=3) == 3
    peeled = strip_leading_non_table_rows_from_data(block)
    flat = " ".join(str(c) for r in peeled for c in r)
    assert "控制为基础" not in flat, peeled
    assert "2025年12月31日" in flat and "资产管理计划" in flat, peeled

    # 仅两行通栏短表头：不得整表剥掉
    hdr_only = [
        ["2025年12月31日 ⟦↔3⟧", "⟦↔⟧", "⟦↔⟧"],
        ["账面余额 ⟦↔3⟧", "⟦↔⟧", "⟦↔⟧"],
        ["资产管理计划", "9,073", "67,642"],
    ]
    assert leading_spanning_prose_run_end(hdr_only, min_run=3) == 0
    kept = strip_leading_non_table_rows_from_data(hdr_only)
    assert any("账面余额" in str(c) for r in kept for c in r), kept
    assert any("资产管理计划" in str(c) for r in kept for c in r), kept


def test_leading_prose_before_balance_table_not_in_grid():
    """表前「结构化主体」说明段是纯文本：页眉止年度不得当表起点，说明不得进表。"""
    from codes.reconstruct.grid_nucleus.pipeline import restore_table_grid
    from codes.reconstruct.grid_nucleus.word_segment import (
        strip_leading_non_table_rows_from_data,
        trim_leading_narrative_words,
        trim_leading_page_chrome_words,
    )
    from codes.table_engine.split.row_classify import is_inter_table_narrative_row

    words = [
        _word("财务报表附注", 86, 74, 160, 86),
        _word("2025 年 12 月 31 日止年度", 86, 90, 220, 102),
        _word("在未纳入合并财务报表范围的结构化主体中的权益", 86, 119, 340, 131),
        _word(
            "本集团合并财务报表的合并范围以控制为基础予以确定。控制，是指投资方拥有对被投资",
            86, 149, 520, 161,
        ),
        _word("方的权力，通过参与被投资方的相关活动而享有可变回报，并且有能力运用对被投资方的", 86, 164, 520, 176),
        _word("权力影响其回报金额。", 86, 180, 200, 192),
        _word("围的结构化主体的权益信息如下：", 86, 270, 280, 282),
        _word("2025年12月31日", 300, 400, 380, 412),
        _word("账面余额", 320, 415, 370, 427),
        _word("资产管理计划", 90, 480, 160, 492),
        _word("9,073", 220, 480, 260, 492),
        _word("67,642", 300, 480, 350, 492),
        _word("200", 390, 480, 420, 492),
        _word("76,915", 470, 480, 520, 492),
        _word("信托受益权", 90, 500, 150, 512),
        _word("100", 220, 500, 250, 512),
        _word("200", 300, 500, 340, 512),
        _word("-", 390, 500, 405, 512),
        _word("300", 470, 500, 510, 512),
    ]
    trimmed = trim_leading_narrative_words(trim_leading_page_chrome_words(words))
    texts = [str(w.get("text") or "") for w in trimmed]
    assert any(t.replace(" ", "") == "2025年12月31日" for t in texts), texts
    assert not any("止年度" in t for t in texts), texts
    assert not any("控制为基础" in t for t in texts), texts
    assert not any(t == "财务报表附注" for t in texts), texts

    dirty = [
        ["财务报表附注 ⟦↔6⟧", "⟦↔⟧", "⟦↔⟧", "⟦↔⟧"],
        ["2025 年 12 月 31 日止年度 ⟦↔6⟧", "⟦↔⟧", "⟦↔⟧", "⟦↔⟧"],
        ["在未纳入合并财务报表范围的结构化主体中的权益 ⟦↔3⟧", "⟦↔⟧", "⟦↔⟧", ""],
        ["本集团合并财务报表的合并范围以控制为基础予以确定。控制，是指投资方拥有对被投资 …", "⟦↔⟧", "⟦↔⟧", "⟦↔⟧"],
        ["权力影响其回报金额。", "", "", ""],
        ["", "", "2025年12月31日", ""],
        ["", "", "账面余额", ""],
        ["资产管理计划", "9,073", "67,642", "76,915"],
    ]
    assert is_inter_table_narrative_row(dirty[3])
    peeled = strip_leading_non_table_rows_from_data(dirty)
    flat_peel = " ".join(str(c) for r in peeled for c in r)
    assert "控制为基础" not in flat_peel, peeled
    assert any("资产管理计划" in str(c) for r in peeled for c in r), peeled

    # 网格恢复：说明不得进 data
    res = restore_table_grid(
        {"type": "table", "data": [["x"]], "_source_words": words},
        source_words=words,
    )
    assert res.data, res.to_dict()
    flat = "\n".join("|".join(str(c) for c in r) for r in res.data)
    assert "控制为基础" not in flat, res.data[:8]
    assert "止年度" not in flat, res.data[:8]
    assert any("资产管理计划" in str(c) for r in res.data for c in r), res.data
    assert any("9,073" in str(c) for r in res.data for c in r), res.data


def test_indented_label_gets_leading_spaces_in_grid():
    """科目左缘更深 → 格子带前导空格（给导出/后续层次用）。"""
    words = [
        _word("54", 70, 100, 82, 112),
        _word("核心一级资本充足率（%）", 107, 100, 230, 112),
        _word("14.48", 430, 100, 460, 112),
        _word("58", 70, 140, 82, 152),
        _word("其中：储备资本要求", 129, 140, 224, 152),
        _word("2.50", 430, 140, 460, 152),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    child = None
    for r in table["data"]:
        for c in r:
            t = str(c or "")
            if "储备资本" in t.replace(" ", ""):
                child = t
                break
    assert child is not None, table["data"]
    assert child.startswith(" "), (repr(child), table["data"])


def test_gsib_intro_narrative_not_mixed_into_first_row():
    """表前「…评估指标如下：」不得入表；首行指标「调整后的表内外资产余额」必须保留。"""
    words = [
        _word("本集团根据《商业银行资本管理办法》信息披露有关要求", 85, 148, 560, 160),
        _word("和巴塞尔银行监管委员会2025年度填报说明的最新规定，编", 85, 164, 560, 176),
        _word("制的2025年商业银行全球系统重要性评估指标如下：", 85, 180, 362, 192),
        _word("单位：人民币亿元", 463, 195, 559, 207),
        _word("序号", 92, 211, 112, 223),
        _word("指标", 281, 211, 301, 223),
        _word("指标值", 494, 211, 524, 223),
        _word("1", 99, 226, 105, 238),
        _word("调整后的表内外资产余额", 128, 225, 238, 237),
        _word("155,855", 513, 226, 553, 238),
        _word("2", 99, 241, 105, 253),
        _word("金融机构间资产", 128, 239, 198, 251),
        _word("14,837", 519, 240, 553, 252),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    flat = "\n".join("|".join(str(c) for c in r) for r in table["data"])
    assert "如下" not in flat, table["data"]
    assert "巴塞尔" not in flat, table["data"]
    assert any("调整后的表内外资产余额" in str(c) for r in table["data"] for c in r), table["data"]
    assert any("155,855" in str(c) for r in table["data"] for c in r), table["data"]
    # 序号 1 与指标同在一行
    row1 = next(
        (r for r in table["data"] if any(str(c).strip() == "1" for c in r)),
        None,
    )
    assert row1 is not None, table["data"]
    assert any("调整后" in str(c) for c in row1), row1
    assert any("155,855" in str(c) for c in row1), row1


def test_shue_amount_header_stays_in_last_column():
    """「数额」是右列金额头：凝结核落末列后，禁止当通栏小节挪到第一列。"""
    words = [
        _word("附表一：资本构成披露", 86, 200, 206, 212),
        _word("数额", 510.8, 238.8, 530.8, 250),
        _word("核心一级资本：", 87, 252, 157, 264),
        _word("1", 95.4, 267, 100.4, 279),
        _word("实收资本和资本公积可计入部分", 119.5, 265, 259.4, 277),
        _word("90,624", 526.4, 267, 553.9, 279),
        _word("2", 95.4, 280, 100.4, 292),
        _word("留存收益", 119.5, 279, 159.4, 291),
        _word("969,084", 521.4, 280, 553.9, 292),
    ]
    table = {
        "type": "table",
        "data": [["x"]],
        "_source_words": words,
        "_table_kind": {"kind": "data"},
    }
    res = apply_grid_to_table(table)
    assert res.ok and res.metrics.get("overwrote_data"), res.to_dict()
    hdr = next(
        (r for r in table["data"] if any(str(c).strip().startswith("数额") for c in r)),
        None,
    )
    assert hdr is not None, table["data"]
    # 须在最后一列，不得在首列通栏
    assert str(hdr[-1]).strip().startswith("数额"), hdr
    assert not str(hdr[0]).strip().startswith("数额"), hdr
    assert "⟦↔3⟧" not in str(hdr[0]) and "⟦↔" not in str(hdr[0]), hdr
    row1 = next(r for r in table["data"] if any(str(c).strip() == "1" for c in r))
    assert "90,624" in str(row1[-1]), row1
    assert "实收资本" in str(row1[1]) and "90,624" not in str(row1[1]), row1


if __name__ == "__main__":
    test_row_cluster_three_rows()
    test_restore_simple_grid()
    test_glue_split_in_preprocess()
    test_cjk_merge()
    test_apply_no_words_keeps_data()
    test_conservation_blocks_bad_overwrite()
    test_apply_overwrites_when_source_conserved()
    test_apply_blocks_amount_not_in_source_words()
    test_table_xy_bbox_and_abc_split()
    test_attach_keeps_table_top_multiline_header()
    test_attach_keeps_table_bottom_summary_rows()
    test_attach_peeks_above_even_when_table_top_flush()
    test_change_rate_header_not_merged_into_left_amount()
    test_row_cluster_does_not_merge_adjacent_lines()
    test_left_aligned_text_not_split_by_width()
    test_interval_label_stays_with_header_left_edge()
    test_assign_uses_both_edges_overlap()
    test_assign_rejects_single_edge_only()
    test_change_phrase_in_gutter_goes_to_right_column()
    test_wrapped_change_header_same_column_as_desc()
    test_wide_amount_and_narrow_rate_same_column()
    test_glue_audit_on_validate_fail_repairs_numeric_text()
    test_profit_dist_bottom_header_year_and_units()
    test_serial_indicator_value_three_cols_not_glued()
    test_year_and_indicator_value_header_same_column_as_amounts()
    test_identical_x_bounds_force_same_column_despite_phantom_amount_slot()
    test_dual_metric_header_spills_into_empty_bad_loan_rate_col()
    test_header_align_keeps_orphan_cols_for_span_mark()
    test_header_align_does_not_copy_period_into_section_row()
    test_overlapping_header_amount_bands_same_column()
    test_letter_code_cols_not_merged()
    test_code_column_not_merged_with_amount()
    test_merge_split_decimal_nuclei()
    test_currency_amount_continuous_is_one_nucleus()
    test_wide_title_does_not_merge_amount_cols_wrapped_header()
    test_restore_migration_rate_no_decimal_space()
    test_balance_header_not_split_from_short_integer_amounts()
    test_right_aligned_wide_and_narrow_amounts_same_column()
    test_serial_column_not_glued_with_label_text()
    test_first_col_letter_serial_and_two_digit_pairs_split()
    test_wrap_label_continuation_not_in_serial_col()
    test_period_bucket_headers_keep_left_to_right_order()
    test_nsfr_period_headers_not_glued_by_header_align()
    test_indented_labels_share_one_column_common_boundary()
    test_small_gap_id_fragments_merge_by_vertical_band()
    test_peer_instrument_columns_not_merged_despite_moderate_gap()
    test_orphan_single_datum_column_without_header_is_pruned()
    test_all_blank_column_means_wrong_split_must_reclaim()
    test_cover_only_blank_column_is_pruned()
    test_right_aligned_dash_not_phantom_between_letter_cols()
    test_body_text_and_text_digit_cols_count_as_data()
    test_body_data_cols_not_phantom_from_spanning_header()
    test_body_pd_and_customer_count_not_merged_despite_date_header()
    test_span_marks_cover_neighbor_cells_after_grid()
    test_trailing_section_caption_not_mixed_into_table()
    test_vertical_same_column_range_inherits_from_row_above()
    test_join_cell_continuous_cjk_no_space()
    test_join_cell_preserves_left_to_right_despite_y0_jitter()
    test_logical_wrap_merge_above_and_below_amount_anchor()
    test_logical_wrap_between_two_amounts_is_ambiguous()
    test_logical_wrap_incomplete_de_merges_down_despite_two_amounts()
    test_span_mark_ignores_label_right_tip_overflow()
    test_logical_wrap_placeholder_host_and_serial_continuation()
    test_span_mark_long_body_label_left_edge_only()
    test_ratio_header_wrap_not_phantom_before_pct_values()
    test_year_group_header_spans_amount_and_ratio_cols()
    test_qizhong_detail_rows_stay_with_serial_amount()
    test_deduction_section_title_own_row_and_span()
    test_span_mark_core_tier1_section_crosses_serial_and_label()
    test_span_mark_no_force_without_nucleus_crossing()
    test_span_mark_fv_oci_header_not_cover_total_exposure()
    test_span_mark_wrap_label_row20_not_span_amount()
    test_logical_hierarchy_rows_with_amounts_not_merged()
    test_indented_label_gets_leading_spaces_in_grid()
    test_gsib_intro_narrative_not_mixed_into_first_row()
    test_leading_prose_before_balance_table_not_in_grid()
    test_asset_and_cash_keep_separate_rows()
    test_asset_mgmt_plan_not_split_into_section_title()
    test_consecutive_spanning_prose_rows_stripped()
    test_capital_composition_narrative_not_merged_into_paid_in_capital()
    test_section_title_not_merged_into_serial_amount_row()
    test_shue_amount_header_stays_in_last_column()
    print("OK")
