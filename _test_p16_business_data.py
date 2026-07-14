# -*- coding: utf-8 -*-
"""P16 类页：（四）业务数据与（五）资本构成须分表，表头不得剥离为 TEXT。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.table_access import dense_rows


def _item(
    text: str,
    x0: float,
    y0: float,
    *,
    x1: float | None = None,
    idx: str = "",
) -> SourceItem:
    x1 = x1 if x1 is not None else x0 + len(text) * 4.2
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x1, y0 + 10),
        page=16,
        item_index=idx or f"i_{y0}_{x0}",
        y_mid=y0 + 5,
    )


def _make_p16_like_page() -> PageSource:
    items: list[SourceItem] = []
    items.append(_item("成都银行股份有限公司", 60, 40))
    items.append(_item("2024 年年度报告", 400, 40))

    for t, y in [
        ("2.成本收入比=业务及管理费/营业收入。", 200),
        ("3.不良贷款率=不良贷款余额/发放贷款和垫款总额（不含应计利息）。", 215),
        ("4.拨备覆盖率=贷款损失准备/不良贷款余额。", 230),
        ("5.贷款拨备率=贷款损失准备/发放贷款和垫款总额（不含应计利息）。", 245),
        ("（四）近三年主要业务数据", 265),
        ("单位：千元", 350),
    ]:
        items.append(_item(t, 60, y))

    items.append(_item("项目", 60, 370))
    items.append(_item("2024 年 12 月 31 日", 200, 370))
    items.append(_item("2023 年 12 月 31 日", 300, 370))
    items.append(_item("2022 年 12 月 31 日", 420, 370))

    rows = [
        ("吸收存款", 390, None),
        ("其中：活期公司存款", 410, ["202,174,175", "212,512,948", "206,985,186"]),
        ("活期个人存款", 430, ["53,486,037", "50,420,829", "49,137,183"]),
        ("定期公司存款", 450, ["203,280,735", "178,133,748", "143,289,639"]),
        ("定期个人存款", 470, ["384,929,475", "303,731,169", "224,607,215"]),
        ("小计", 490, ["865,707,137", "764,786,867", "641,500,682"]),
        ("应计利息", 510, ["20,152,203", "15,634,422", "13,151,340"]),
        ("吸收存款总额", 530, ["885,859,340", "780,421,289", "654,652,022"]),
        ("发放贷款和垫款", 550, None),
        ("其中：公司贷款和垫款", 570, ["602,616,836", "501,117,055", "379,587,409"]),
        ("个人贷款和垫款", 590, ["138,702,248", "123,331,334", "106,996,698"]),
        ("小计", 610, ["741,319,084", "624,448,389", "486,584,107"]),
        ("应计利息", 630, ["1,249,141", "1,293,830", "1,242,563"]),
        ("发放贷款和垫款总额", 650, ["742,568,225", "625,742,219", "487,826,670"]),
    ]
    for label, y, vals in rows:
        items.append(_item(label, 60, y))
        if vals:
            items.append(_item(vals[0], 200, y))
            items.append(_item(vals[1], 300, y))
            items.append(_item(vals[2], 420, y))

    items.append(_item("（五）资本构成及变化情况", 60, 670))
    items.append(_item("单位：千元", 350, 670))
    items.append(_item("项目", 60, 690))
    items.append(_item("2024 年 12 月 31 日", 200, 690))
    items.append(_item("2023 年 12 月 31 日", 300, 690))
    items.append(_item("2022 年 12 月 31 日", 420, 690))
    items.append(_item("并表", 200, 710))
    items.append(_item("非并表", 260, 710))
    items.append(_item("并表", 320, 710))
    items.append(_item("非并表", 380, 710))

    regions = [RegionBox(54, 281, 573, 723, 1.0, 0)]
    return PageSource(
        page_number=16,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_business_data_table_keeps_column_header():
    page = _make_p16_like_page()
    result = build_page(page)
    biz_tables = [
        e
        for e in result.entries
        if e.table and any("活期公司存款" in str(r[0] or "") for r in dense_rows(e.table))
    ]
    assert biz_tables, "expected business data table"
    rows = dense_rows(biz_tables[0].table)
    headerish = [r for r in rows if str(r[0] or "").strip() == "项目"]
    assert headerish, [str(r[0]) for r in rows[:5]]


def test_capital_section_split_to_separate_table():
    page = _make_p16_like_page()
    result = build_page(page)
    capital = [
        e
        for e in result.entries
        if e.table and any("并表" in str(c) for row in dense_rows(e.table) for c in row)
    ]
    biz = [
        e
        for e in result.entries
        if e.table and any("活期公司存款" in str(r[0] or "") for r in dense_rows(e.table))
    ]
    assert biz, "business table missing"
    assert capital, "capital table should be separate"
    assert biz[0] is not capital[0], "must not merge (四) and (五) tables"

    biz_rows = dense_rows(biz[0].table)
    for row in biz_rows:
        label = str(row[0] or "")
        assert "资本构成" not in label, row
        assert "吸收存款总额发放贷款" not in label.replace(" ", ""), row
        assert "发放贷款和垫款总额（五）" not in label, row
    assert any(str(r[0] or "").strip() == "发放贷款和垫款" for r in biz_rows), biz_rows


def test_section_labels_not_merged_into_totals():
    page = _make_p16_like_page()
    result = build_page(page)
    for e in result.entries:
        if not e.table:
            continue
        for row in dense_rows(e.table):
            label = str(row[0] or "")
            assert "吸收存款总额发放贷款" not in label.replace(" ", ""), row
            assert "发放贷款和垫款总额（五）" not in label, row


def test_header_recovered_when_region_starts_at_body():
    """region 顶边落在表体行时，仍须向上纳入 项目+日期 表头。"""
    page = _make_p16_like_page()
    # 模拟检测框从「其中：活期公司存款」起（表头在 region 顶边之上）
    page.table_regions[0] = page.table_regions[0].__class__(
        54, 405, 573, 723, 1.0, 0,
    )
    result = build_page(page)
    biz = [
        e
        for e in result.entries
        if e.table and any("活期公司存款" in str(r[0] or "") for r in dense_rows(e.table))
    ]
    assert biz, "business table missing"
    rows = dense_rows(biz[0].table)
    assert any(str(r[0] or "").strip() == "项目" for r in rows[:6]), rows[:6]
    assert not any("资本构成" in str(r[0] or "") for r in rows), rows[-3:]


def test_header_reattached_when_stripped_to_preceding_text():
    """表体先入库、列标误落在上方 TEXT 时，须回补进 TABLE。"""
    page = _make_p16_like_page()
    # 模拟 scope 仅从「吸收存款」起收，表头行留在 gap
    body_only = [
        it for it in page.items
        if it.bbox.y0 >= 390 and it.bbox.y0 <= 660
    ]
    from codes.table_engine.scope.region_scope import build_table_scope
    from codes.table_engine.scope.header_supplement import supplement_scope_missing_headers
    from codes.table_engine.table_builder import build_table_from_scope
    from codes.table_engine.split.table_text_split import build_page_entries
    from codes.table_engine.pipeline import build_page

    scope = build_table_scope(page, page.table_regions[0], 0)
    scope.items = body_only
    scope = supplement_scope_missing_headers(page, scope)
    rows = [str(it.text) for it in scope.items if str(it.text).strip() == "项目"]
    assert rows, "header should be supplemented into scope"

    result = build_page(page)
    biz = [
        e for e in result.entries
        if e.table and any("活期公司存款" in str(r[0] or "") for r in dense_rows(e.table))
    ]
    assert biz
    assert any(str(r[0] or "").strip() == "项目" for r in dense_rows(biz[0].table))


def _make_p16_capital_dates_first_page() -> PageSource:
    """（五）资本表表头为「日期行在上、项目行在下」时仍须与（四）分表。"""
    items: list[SourceItem] = []
    items.append(_item("单位：千元", 350, 350))
    items.append(_item("项目", 60, 370))
    for t, x, y in [
        ("2024 年 12 月 31 日", 200, 370),
        ("2023 年 12 月 31 日", 300, 370),
        ("2022 年 12 月 31 日", 420, 370),
    ]:
        items.append(_item(t, x, y))
    for label, y, vals in [
        ("吸收存款", 390, None),
        ("其中：活期公司存款", 410, ["202,174,175", "212,512,948", "206,985,186"]),
        ("吸收存款总额", 430, ["885,859,340", "780,421,289", "654,652,022"]),
        ("发放贷款和垫款", 450, None),
        ("发放贷款和垫款总额", 470, ["742,568,225", "625,742,219", "487,826,670"]),
    ]:
        items.append(_item(label, 60, y))
        if vals:
            for v, x in zip(vals, [200, 300, 420]):
                items.append(_item(v, x, y))
    items.append(_item("（五）资本构成及变化情况", 60, 490))
    items.append(_item("单位：千元", 350, 490))
    for t, x, y in [
        ("2024 年 12 月 31 日", 200, 510),
        ("2023 年 12 月 31 日", 300, 510),
        ("2022 年 12 月 31 日", 420, 510),
    ]:
        items.append(_item(t, x, y))
    items.append(_item("项目", 60, 530))
    for t, x in [("并表", 200), ("非并表", 260), ("并表", 320), ("非并表", 380)]:
        items.append(_item(t, x, 550))
    regions = [RegionBox(54, 360, 573, 560, 1.0, 0)]
    return PageSource(
        page_number=16,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_capital_section_split_when_dates_before_project_row():
    page = _make_p16_capital_dates_first_page()
    result = build_page(page)
    biz = [
        e for e in result.entries
        if e.table and any("活期公司存款" in str(r[0] or "") for r in dense_rows(e.table))
    ]
    capital = [
        e for e in result.entries
        if e.table and any("并表" in str(c) for row in dense_rows(e.table) for c in row)
    ]
    assert biz and capital and biz[0] is not capital[0]
    biz_rows = dense_rows(biz[0].table)
    assert any(str(r[0] or "").strip() == "发放贷款和垫款总额" for r in biz_rows)
    assert not any("资本构成" in str(r[0] or "") for r in biz_rows), biz_rows[-3:]
    last = biz_rows[-1]
    assert "742,568,225" in str(last[1] or "")


if __name__ == "__main__":
    test_business_data_table_keeps_column_header()
    print("column header kept OK")
    test_capital_section_split_to_separate_table()
    print("capital section split OK")
    test_section_labels_not_merged_into_totals()
    print("section labels not merged OK")
    test_header_recovered_when_region_starts_at_body()
    print("header recovered when region starts at body OK")
    test_header_reattached_when_stripped_to_preceding_text()
    print("header reattach from gap OK")
    test_capital_section_split_when_dates_before_project_row()
    print("capital split dates-before-project OK")
