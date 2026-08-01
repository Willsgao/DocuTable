# -*- coding: utf-8 -*-
"""P28：变化原因表不得与上方利润表误合并，且表头「变化原因」不得丢失。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_access import dense_rows


def _item(text: str, x0: float, y0: float, w: float | None = None) -> SourceItem:
    w = w if w is not None else max(len(text) * 4.5, 20.0)
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x0 + w, y0 + 10),
        page=28,
        item_index=f"i_{y0:.0f}_{x0:.0f}_{abs(hash(text + str(y0))) % 99999}",
        y_mid=y0 + 5,
    )


def _make_p28_page() -> PageSource:
    items: list[SourceItem] = []
    items.append(_item("成都银行股份有限公司", 60, 40, 200))
    items.append(_item("2024 年年度报告", 350, 40, 120))

    # Region 0: 利润表主要项目
    items.append(_item("单位：千元", 400, 95))
    for t, x in [("项目", 80), ("2024 年", 220), ("2023 年", 320), ("增减幅度", 420)]:
        items.append(_item(t, x, 110))
    for label, vals, y in [
        ("一、营业收入", ["22,981,527", "21,702,189", "5.89%"], 130),
        ("其中：利息净收入", ["18,460,724", "17,653,551", "4.57%"], 150),
        ("手续费及佣金净收入", ["709,775", "662,124", "7.20%"], 170),
        ("二、营业支出", ["7,754,968", "7,797,021", "-0.54%"], 190),
        ("五、净利润", ["12,850,233", "11,671,933", "10.10%"], 290),
    ]:
        items.append(_item(label, 80, y))
        for v, x in zip(vals, [220, 320, 420]):
            items.append(_item(v, x, y))

    # 大间隙：新小节 + 变化原因表表头
    items.append(_item("（二）利润表中变化幅度超过30%的项目及变化原因", 60, 350, 280))
    items.append(_item("单位：千元", 400, 390))
    for t, x in [
        ("项目", 60),
        ("2024 年", 160),
        ("2023 年", 250),
        ("增减幅度", 340),
        ("变化原因", 470),
    ]:
        items.append(_item(t, x, 410))

    # Region 1: 变化原因表体（百分比与原因 OCR 粘连）
    for label, a, b, glued, y in [
        ("手续费及佣金支出", "51,968", "88,822", "-41.49%代理业务支出减少", 440),
        ("其他收益", "113,604", "280,170", "-59.45%政府补助减少", 460),
        ("公允价值变动损益", "691,826", "157,724", "338.63%基金估值变动", 480),
        ("汇兑损益", "-648,315", "-256,758", "152.50%外汇掉期业务估值变动", 500),
        ("其他债权投资公允价值变动", "1,162,092", "442,980", "162.34%债券估值变动", 580),
    ]:
        items.append(_item(label, 60, y))
        items.append(_item(a, 160, y))
        items.append(_item(b, 250, y))
        items.append(_item(glued, 340, y, 140))

    # Region 2: 业务收入构成
    items.append(_item("（三）报告期各项业务收入构成情况", 60, 640, 220))
    items.append(_item("单位：千元", 400, 670))
    for t, x in [
        ("项目", 60),
        ("金额", 160),
        ("占比", 250),
        ("本报告期比上年", 340),
        ("2023 年", 460),
    ]:
        items.append(_item(t, x, 688))
    items.append(_item("同期增减", 340, 700))
    items.append(_item("存放中央银行款项", 60, 720))
    for v, x in zip(["899,467", "1.90%", "3.15%", "872,031"], [160, 250, 340, 460]):
        items.append(_item(v, x, 720))

    return PageSource(
        page_number=28,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[
            RegionBox(76, 105, 518, 335, 1.0, 0),
            RegionBox(51, 425, 541, 610, 1.0, 1),
            RegionBox(51, 703, 541, 743, 0.9, 2),
        ],
        is_table_page=True,
    )


def test_p28_regions_not_merged_across_subsection():
    page = _make_p28_page()
    plan = plan_page_scopes(page)
    assert len(plan.scopes) >= 3, f"expected 3 scopes, got {len(plan.scopes)}"


def test_p28_change_reason_header_preserved():
    page = _make_p28_page()
    result = build_page(page)
    tables = [e.table for e in result.entries if e.table]
    assert len(tables) >= 2, len(tables)

    reason_table = None
    for t in tables:
        rows = dense_rows(t)
        flat = " ".join(str(c) for r in rows for c in r if c)
        if "手续费及佣金支出" in flat and "变化原因" in flat:
            reason_table = t
            break
        if "手续费及佣金支出" in flat:
            reason_table = t
            break
    assert reason_table is not None, "missing change-reason table"

    rows = dense_rows(reason_table)
    header = next(
        (r for r in rows if any(str(c or "").strip() == "项目" for c in r)),
        None,
    )
    assert header is not None, rows[:4]
    assert any("变化原因" in str(c) for c in header), f"变化原因 lost: {header!r}"
    assert any("增减幅度" in str(c) for c in header), header

    body = next((r for r in rows if "手续费及佣金支出" in str(r[0] or "")), None)
    assert body is not None, rows
    assert any("代理业务" in str(c) for c in body), f"reason body lost: {body!r}"
    assert not any("一、营业收入" in str(c) for r in rows for c in r), rows[:5]


def test_p28_profit_table_not_duplicated_as_reason():
    page = _make_p28_page()
    result = build_page(page)
    tables = [dense_rows(e.table) for e in result.entries if e.table]
    profitish = [
        rows for rows in tables
        if any("一、营业收入" in str(c) for r in rows for c in r)
    ]
    assert len(profitish) == 1, f"profit table duplicated: {len(profitish)}"


def test_glued_trailing_header_splits_by_x():
    """OCR 将「2023年 增减幅度 变化原因」粘成一格时应按 x 拆到 3/4/5 列。"""
    from codes.table_engine.table_access import dense_rows

    items: list[SourceItem] = []
    items.append(_item("单位：千元", 400, 390))
    items.append(_item("项目", 60, 410))
    items.append(_item("2024 年", 160, 410))
    # 粘连宽格：覆盖原 2023/增减幅度/变化原因 三列区域
    items.append(_item("2023 年 增减幅度 变化原因", 250, 410, 280))
    for label, vals, y in [
        ("手续费及佣金支出", ["51,968", "88,822", "-41.49%", "代理业务支出减少"], 440),
        ("其他收益", ["113,604", "280,170", "-59.45%", "政府补助减少"], 460),
    ]:
        items.append(_item(label, 60, y))
        for v, x in zip(vals, [160, 250, 340, 470]):
            items.append(_item(v, x, y))

    page = PageSource(
        page_number=28,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[RegionBox(51, 390, 541, 610, 1.0, 0)],
        is_table_page=True,
    )
    result = build_page(page)
    table = next(e.table for e in result.entries if e.table)
    header = next(
        r for r in dense_rows(table)
        if any(str(c or "").strip() == "项目" for c in r)
    )
    assert "增减幅度" in [str(c).strip() for c in header], header
    assert "变化原因" in [str(c).strip() for c in header], header
    # 不得整段糊进同一格
    assert not any(
        "增减幅度" in str(c) and "变化原因" in str(c) and "2023" in str(c)
        for c in header
    ), header
    # 各自独立一列
    texts = [str(c).strip() for c in header]
    assert texts.count("增减幅度") == 1 and texts.count("变化原因") == 1, header


if __name__ == "__main__":
    test_p28_regions_not_merged_across_subsection()
    print("regions not merged OK")
    test_p28_change_reason_header_preserved()
    print("change reason header OK")
    test_p28_profit_table_not_duplicated_as_reason()
    print("no duplicate profit table OK")
    test_glued_trailing_header_splits_by_x()
    print("glued trailing header split OK")
