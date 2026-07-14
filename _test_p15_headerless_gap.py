# -*- coding: utf-8 -*-
"""P15 类页：首个 region 上方无表头权益矩阵应识别为 TABLE。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_access import dense_rows


def _item(
    text: str,
    x0: float,
    y0: float,
    x1: float | None = None,
    y1: float | None = None,
    idx: str = "",
) -> SourceItem:
    x1 = x1 if x1 is not None else x0 + len(text) * 4.5
    y1 = y1 if y1 is not None else y0 + 10
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        page=15,
        item_index=idx or f"i_{y0}_{x0}",
        y_mid=(y0 + y1) / 2,
    )


def _make_p15_like_page() -> PageSource:
    items: list[SourceItem] = []
    # 页眉
    items.append(_item("成都银行股份有限公司", 60, 40))
    items.append(_item("2024 年年度报告", 400, 40))

    # 无表头权益矩阵（在 region0 上方）
    for label, v1, v2, v3, v4, y in [
        ("归属于母公司股东的净资", "85,855,452", "71,235,227", "20.52%", "61,342,713", 180),
        ("归属于母公司普通股股东", "79,856,754", "65,236,529", "22.41%", "55,344,015", 200),
        ("归属于母公司普通股股东", "19.15", "17.10", "11.99%", "14.81", 220),
    ]:
        items.append(_item(label, 60, y))
        items.append(_item(v1, 200, y))
        items.append(_item(v2, 280, y))
        items.append(_item(v3, 360, y))
        items.append(_item(v4, 440, y))
    for tail, y in [
        ("产", 190),
        ("的净资产", 210),
        ("的每股净资产（元/股）", 230),
    ]:
        items.append(_item(tail, 60, y))

    # 附注（应保留 TEXT）
    items.append(_item("注：贷款损失准备=以摊余成本计量的发放贷款和垫款损失准备", 60, 235))

    # region0: （二）财务指标
    items.append(_item("（二）近三年主要财务指标", 60, 248))
    items.append(_item("项目", 60, 268))
    items.append(_item("2024 年", 200, 268))
    items.append(_item("2023 年", 280, 268))
    items.append(_item("本报告期比上年同期", 360, 268))
    items.append(_item("2022 年", 440, 268))
    items.append(_item("期增减", 360, 278))
    items.append(_item("基本每股收益（元/股）", 60, 300))
    items.append(_item("3.28", 200, 300))
    items.append(_item("3.01", 280, 300))
    items.append(_item("8.97%", 360, 300))
    items.append(_item("2.69", 440, 300))
    items.append(_item("每股经营活动产生的现金流", 60, 370))
    items.append(_item("-22.6", 200, 370))
    items.append(_item("-6.23", 280, 370))
    items.append(_item("262.76%", 360, 370))
    items.append(_item("2.59", 440, 370))
    items.append(_item("量净额（元/股）", 60, 380))

    # region1: （三）补充指标
    items.append(_item("（三）近三年补充财务指标", 60, 580))
    items.append(_item("项目", 60, 600))
    items.append(_item("2024 年", 200, 600))
    items.append(_item("2023 年", 280, 600))
    items.append(_item("2022 年", 440, 600))
    items.append(_item("资产利润率", 60, 620))
    items.append(_item("1.10%", 200, 620))
    items.append(_item("1.16%", 280, 620))
    items.append(_item("1.19%", 440, 620))

    regions = [
        RegionBox(54, 253, 541, 392, 1.0, 0),
        RegionBox(54, 592, 541, 726, 1.0, 1),
    ]
    return PageSource(
        page_number=15,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_plan_page_scopes_promotes_headerless_equity_table():
    page = _make_p15_like_page()
    plan = plan_page_scopes(page)
    headerless = [
        s for s in plan.scopes if s.metadata.get("headerless_gap_table")
    ]
    assert headerless, "expected headerless gap scope"
    joined = "".join(str(it.text) for it in headerless[0].items)
    assert "85,855,452" in joined
    assert "归属于母公司股东的净资" in joined
    gap_joined = "\n".join(b.text for b in plan.gap_texts)
    assert "85,855,452" not in gap_joined, gap_joined


def test_build_page_headerless_equity_as_table():
    page = _make_p15_like_page()
    result = build_page(page)
    tables = [e for e in result.entries if e.table]
    assert len(tables) >= 3, len(tables)

    equity_rows = [
        row
        for e in tables
        for row in dense_rows(e.table)
        if "归属于母公司" in str(row[0] or "")
    ]
    assert len(equity_rows) >= 2, [str(r[0]) for r in equity_rows]
    assert any("净资产" in str(r[0] or "") for r in equity_rows)

    for e in result.entries:
        if e.text_block:
            text = e.text_block.text
            assert "85,855,452" not in text, f"equity data leaked to TEXT: {text!r}"
            assert text.strip() not in ("产", "的净资产"), text


def test_cashflow_wrap_merged_not_in_text():
    page = _make_p15_like_page()
    result = build_page(page)
    for e in result.entries:
        if e.table:
            for row in dense_rows(e.table):
                if "现金流量" in str(row[0] or ""):
                    assert "净额" in str(row[0] or "")
        if e.text_block:
            assert "量净额" not in e.text_block.text, e.text_block.text


def _make_p15_realistic_layout() -> PageSource:
    """折行尾片落在附注之后（与用户文本1顺序一致）。"""
    items: list[SourceItem] = []
    items.append(_item("成都银行股份有限公司", 60, 40))
    items.append(_item("2024 年年度报告", 400, 40))

    for label, v1, v2, v3, v4, y in [
        ("归属于母公司股东的净资", "85,855,452", "71,235,227", "20.52%", "61,342,713", 165),
        ("归属于母公司普通股股东", "79,856,754", "65,236,529", "22.41%", "55,344,015", 185),
        ("归属于母公司普通股股东", "19.15", "17.10", "11.99%", "14.81", 205),
    ]:
        items.append(_item(label, 60, y))
        items.append(_item(v1, 200, y))
        items.append(_item(v2, 280, y))
        items.append(_item(v3, 360, y))
        items.append(_item(v4, 440, y))

    items.append(_item("注：贷款损失准备=以摊余成本计量的发放贷款和垫款损失准备", 60, 222))
    items.append(_item("他综合收益的发放贷款和垫款损失准备。", 60, 232))
    items.append(_item("成都银行股份有限公司", 60, 242))
    items.append(_item("2024 年年度报告", 400, 242))
    for tail, y in [("产", 252), ("的净资产", 262), ("的每股净资产（元/股）", 272)]:
        items.append(_item(tail, 60, y))

    items.append(_item("（二）近三年主要财务指标", 60, 248))
    items.append(_item("项目", 60, 268))
    items.append(_item("2024 年", 200, 268))
    items.append(_item("基本每股收益（元/股）", 60, 300))
    items.append(_item("3.28", 200, 300))

    regions = [
        RegionBox(54, 253, 541, 392, 1.0, 0),
        RegionBox(54, 592, 541, 726, 1.0, 1),
    ]
    return PageSource(
        page_number=15,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_build_page_realistic_wrap_after_footnote():
    """折行尾片在附注之后、且 y 略低于 region 顶 → 仍应并入无表头表。"""
    page = _make_p15_realistic_layout()
    result = build_page(page)
    tables = [e for e in result.entries if e.table]
    equity = [
        row
        for e in tables
        for row in dense_rows(e.table)
        if "归属于母公司" in str(row[0] or "")
    ]
    assert len(equity) >= 3, [str(r[0]) for r in equity]
    assert any("净资产" in str(r[0] or "") for r in equity)
    assert any("每股净资产" in str(r[0] or "") for r in equity)

    for e in result.entries:
        if e.text_block:
            text = e.text_block.text
            assert "85,855,452" not in text, text
            assert "产" not in text or "归属于母公司" in text, text
            assert "的每股净资产" not in text, text


def _make_p15_user_interleaved_layout() -> PageSource:
    """与用户 P15 一致：表体与折行尾片交错排列。"""
    items: list[SourceItem] = []
    items.append(_item("成都银行股份有限公司", 60, 40))
    items.append(_item("2024 年年度报告", 400, 40))

    rows = [
        ("归属于母公司股东的净资", "85,855,452", "71,235,227", "20.52%", "61,342,713", 120, None),
        ("产", None, None, None, None, 132, None),
        ("归属于母公司普通股股东", "79,856,754", "65,236,529", "22.41%", "55,344,015", 145, None),
        ("的净资产", None, None, None, None, 157, None),
        ("归属于母公司普通股股东", "19.15", "17.10", "11.99%", "14.81", 170, None),
        ("的每股净资产（元/股）", None, None, None, None, 182, None),
    ]
    for label, v1, v2, v3, v4, y, _ in rows:
        items.append(_item(label, 60, y))
        if v1:
            items.append(_item(v1, 200, y))
            items.append(_item(v2, 280, y))
            items.append(_item(v3, 360, y))
            items.append(_item(v4, 440, y))

    items.append(_item("注：贷款损失准备=以摊余成本计量的发放贷款和垫款损失准备", 60, 210))
    items.append(_item("他综合收益的发放贷款和垫款损失准备。", 60, 220))
    items.append(_item("（二）近三年主要财务指标", 60, 245))
    items.append(_item("项目", 60, 268))
    items.append(_item("2024 年", 200, 268))
    items.append(_item("基本每股收益（元/股）", 60, 300))
    items.append(_item("3.28", 200, 300))

    regions = [
        RegionBox(54, 253, 541, 392, 1.0, 0),
        RegionBox(54, 592, 541, 726, 1.0, 1),
    ]
    return PageSource(
        page_number=15,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_reading_order_footnote_after_equity_table():
    """附注须在权益表之后，页眉须在权益表之前。"""
    page = _make_p15_user_interleaved_layout()
    result = build_page(page)
    order: list[str] = []
    for e in result.entries:
        if e.kind == "text" and e.text_block:
            if "成都银行" in e.text_block.text:
                order.append("chrome")
            elif "贷款损失准备" in e.text_block.text:
                order.append("footnote")
        elif e.table:
            rows = dense_rows(e.table)
            flat = " ".join(str(c) for row in rows for c in row)
            if "85,855,452" in flat:
                order.append("equity")
                for row in rows:
                    assert "（二）" not in str(row[0] or ""), row
            elif "基本每股收益" in flat:
                order.append("finance")

    assert order.index("chrome") < order.index("equity") < order.index("footnote") < order.index("finance")


def _make_p15_region1_with_prior_footnotes() -> PageSource:
    """region0 表后附注须为 TEXT，不得并入 region1 表头。"""
    items: list[SourceItem] = []
    items.append(_item("注：1.每股收益和净资产收益率根据中国证券监督管理委员会公告〔2010〕2 号《公开发行证券", 60, 410))
    items.append(_item("的公司信息披露编报规则第 9 号——净资产收益率和每股收益的计算及披露》计算。", 60, 425))
    items.append(_item("2.非经常性损益根据中国证券监督管理委员会公告〔2008〕43 号《公开发行证券的公司信息", 60, 445))
    items.append(_item("披露解释性公告第 1 号——非经常性损益》的定义计算。", 60, 460))
    items.append(_item("（三）近三年补充财务指标", 60, 570))
    items.append(_item("项目", 60, 600))
    items.append(_item("2024 年", 200, 600))
    items.append(_item("2023 年", 280, 600))
    items.append(_item("2022 年", 440, 600))
    items.append(_item("资产利润率", 60, 620))
    items.append(_item("1.10%", 200, 620))
    items.append(_item("1.16%", 280, 620))
    items.append(_item("1.19%", 440, 620))
    items.append(_item("项目", 60, 650))
    items.append(_item("2024 年 12 月 31 日", 200, 650))
    items.append(_item("2023 年 12 月 31 日", 280, 650))
    items.append(_item("2022 年 12 月 31 日", 440, 650))
    items.append(_item("不良贷款率", 60, 670))
    items.append(_item("0.66%", 200, 670))
    regions = [
        RegionBox(54, 253, 541, 392, 1.0, 0),
        RegionBox(54, 592, 541, 726, 1.0, 1),
    ]
    return PageSource(
        page_number=15,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_region1_footnotes_not_in_table_header():
    """（二）表后附注不得作为（三）补充指标表的表头行。"""
    page = _make_p15_region1_with_prior_footnotes()
    plan = plan_page_scopes(page)
    scope1 = next(s for s in plan.scopes if s.region_index == 1)
    pre_joined = " ".join(str(it.text) for it in scope1.pre_header_items)
    assert "每股收益" not in pre_joined, pre_joined
    assert "非经常性损益" not in pre_joined, pre_joined

    gap_joined = "\n".join(b.text for b in plan.gap_texts)
    assert "每股收益" in gap_joined or "非经常性损益" in gap_joined, gap_joined

    result = build_page(page)
    for e in result.entries:
        if not e.table:
            continue
        rows = dense_rows(e.table)
        if not any("资产利润率" in str(r[0] or "") for r in rows):
            continue
        for row in rows:
            label = str(row[0] or "")
            assert "每股收益" not in label, row
            assert "非经常性损益" not in label, row
        header_rows = [r for r in rows if str(r[0] or "").strip() == "项目"]
        assert header_rows, rows
        first_header_idx = rows.index(header_rows[0])
        for row in rows[:first_header_idx]:
            assert str(row[0] or "").strip() in ("", "项目") or "补充财务" not in str(row[0] or ""), rows


def test_region1_dual_annual_header_splits_into_two_tables():
    """（三）补充指标区内「项目+年」与「项目+日期」两张表须拆分。"""
    page = _make_p15_region1_with_prior_footnotes()
    result = build_page(page)
    supplement_tables = []
    for e in result.entries:
        if not e.table:
            continue
        rows = dense_rows(e.table)
        flat = " ".join(str(c) for row in rows for c in row)
        if "资产利润率" in flat or "不良贷款率" in flat:
            supplement_tables.append(rows)

    assert len(supplement_tables) == 2, supplement_tables

    t1, t2 = supplement_tables
    assert any(str(r[0] or "").strip() == "项目" for r in t1)
    assert any("资产利润率" in str(r[0] or "") for r in t1)
    assert "不良贷款率" not in " ".join(str(r[0] or "") for r in t1)

    assert any(str(r[0] or "").strip() == "项目" for r in t2)
    assert any("不良贷款率" in str(r[0] or "") for r in t2)
    assert "资产利润率" not in " ".join(str(r[0] or "") for r in t2)
    assert any("12 月 31 日" in str(c) for row in t2 for c in row)


if __name__ == "__main__":
    test_plan_page_scopes_promotes_headerless_equity_table()
    print("plan headerless scope OK")
    test_build_page_headerless_equity_as_table()
    print("build_page headerless table OK")
    test_cashflow_wrap_merged_not_in_text()
    print("cashflow wrap dedup OK")
    test_build_page_realistic_wrap_after_footnote()
    print("realistic wrap after footnote OK")
    test_reading_order_footnote_after_equity_table()
    print("reading order footnote after equity OK")
    test_region1_footnotes_not_in_table_header()
    print("region1 footnotes not in table header OK")
    test_region1_dual_annual_header_splits_into_two_tables()
    print("region1 dual annual header split OK")
