# -*- coding: utf-8 -*-
"""P30 类页：利息表顶行回补、（六）不进表、末 region 下新表。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.table_access import dense_rows


def _item(text: str, x: float, y: float, *, x1: float | None = None) -> SourceItem:
    x1 = x1 if x1 is not None else x + len(text) * 4.2
    return SourceItem(
        text=text,
        bbox=BBox(x, y, x1, y + 10),
        page=30,
        item_index=f"i_{y}_{x}",
        y_mid=y + 5,
    )


def _make_p30_like_page() -> PageSource:
    items: list[SourceItem] = []
    items.append(_item("成都银行股份有限公司", 60, 40))
    items.append(_item("2024 年年度报告", 400, 40))

    # 利息表（region0 顶边落在表体中部）
    for label, y, vals in [
        ("债券及其他投资", 118, ["8,218,359", "19.25%", "9,651,182", "24.57%"]),
        ("利息收入小计", 138, ["42,697,378", "100.00%", "39,287,897", "100.00%"]),
        ("向中央银行借款", 158, ["1,284,079", "5.30%", "811,272", "3.75%"]),
        ("吸收存款", 178, ["17,883,231", "73.79%", "15,954,375", "73.75%"]),
        ("应付债券", 198, ["4,119,808", "17.00%", "3,875,197", "17.91%"]),
    ]:
        items.append(_item(label, 60, y))
        for v, x in zip(vals, [200, 280, 360, 440]):
            items.append(_item(v, x, y))

    items.append(_item("利息支出", 60, 148))
    items.append(_item("利息支出小计", 60, 228))
    for v, x in zip(
        ["24,236,654", "100.00%", "21,634,346", "100.00%"],
        [200, 280, 360, 440],
    ):
        items.append(_item(v, x, 228))
    items.append(_item("利息净收入", 60, 248))
    for v, x in zip(["18,460,724", "-", "17,653,551", "-"], [200, 280, 360, 440]):
        items.append(_item(v, x, 248))

    items.append(_item("（六）非利息净收入", 60, 310))
    items.append(_item("1.手续费及佣金净收入", 60, 330))

    items.append(_item("单位：千元", 350, 420))
    items.append(_item("项目", 60, 440))
    items.append(_item("2024 年", 200, 440))
    items.append(_item("2023 年", 300, 440))
    for label, y, vals in [
        ("手续费及佣金收入", 460, ["761,743", "750,946"]),
        ("手续费及佣金净收入", 520, ["709,775", "662,124"]),
    ]:
        items.append(_item(label, 60, y))
        items.append(_item(vals[0], 200, y))
        items.append(_item(vals[1], 300, y))

    items.append(_item("2.投资收益", 60, 680))
    items.append(_item("单位：千元", 350, 710))
    items.append(_item("项目", 60, 730))
    items.append(_item("2024 年", 200, 730))
    items.append(_item("2023 年", 300, 730))
    items.append(_item("投资收益", 60, 750))
    items.append(_item("125,000", 200, 750))
    items.append(_item("98,000", 300, 750))

    regions = [
        RegionBox(54, 100, 541, 277, 1.0, 0),
        RegionBox(54, 286, 541, 323, 1.0, 1),
        RegionBox(54, 416, 541, 663, 1.0, 2),
    ]
    return PageSource(
        page_number=30,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_interest_table_includes_top_body_row():
    page = _make_p30_like_page()
    result = build_page(page)
    interest = [
        e for e in result.entries
        if e.table and any("吸收存款" in str(r[0] or "") for r in dense_rows(e.table))
    ]
    assert interest, "expected merged interest table"
    rows = dense_rows(interest[0].table)
    labels = [str(r[0] or "") for r in rows]
    assert any("债券及其他投资" in lb for lb in labels), labels[:5]


def test_subsection_six_not_in_interest_table():
    page = _make_p30_like_page()
    result = build_page(page)
    for e in result.entries:
        if not e.table:
            continue
        if any("吸收存款" in str(r[0] or "") for r in dense_rows(e.table)):
            for row in dense_rows(e.table):
                assert "非利息净收入" not in str(row[0] or ""), row


def test_trailing_investment_income_table():
    page = _make_p30_like_page()
    result = build_page(page)
    texts = [
        e.text_block.text.strip()
        for e in result.entries
        if e.text_block and e.text_block.text.strip()
    ]
    assert any("2.投资收益" in t or "投资收益" in t and "2." in t for t in texts), texts
    inv = [
        e for e in result.entries
        if e.table and any("投资收益" in str(r[0] or "") for r in dense_rows(e.table))
        and "手续费" not in " ".join(
            str(c) for row in dense_rows(e.table) for c in row
        )
    ]
    assert inv, [dense_rows(e.table)[0] if e.table else e.text_block for e in result.entries]
    rows = dense_rows(inv[0].table)
    assert any(str(r[0] or "").strip() == "项目" for r in rows[:4]), rows[:4]
    assert any("千元" in str(c) for row in rows[:3] for c in row), rows[:3]
    assert any("125,000" in str(c) for row in rows for c in row)


def test_gap_text_includes_fee_section_heading():
    page = _make_p30_like_page()
    result = build_page(page)
    texts = [
        e.text_block.text.strip()
        for e in result.entries
        if e.text_block and e.text_block.text.strip()
    ]
    assert any("手续费及佣金净收入" in t for t in texts), texts
    assert any("非利息净收入" in t or "（六）" in t for t in texts), texts
def test_interest_section_label_row_preserved():
    """表内仅标签的小节行（利息支出）须保留在表体中。"""
    page = _make_p30_like_page()
    result = build_page(page)
    interest = [
        e for e in result.entries
        if e.table and any("吸收存款" in str(r[0] or "") for r in dense_rows(e.table))
    ]
    assert interest, "expected merged interest table"
    labels = [str(r[0] or "").strip() for r in dense_rows(interest[0].table)]
    assert "利息支出" in labels, labels
    idx = labels.index("利息支出")
    row = dense_rows(interest[0].table)[idx]
    assert not any(
        str(c or "").strip() and str(c or "").strip() not in ("-", "－", "—", "–")
        for c in row[1:]
    ), row


if __name__ == "__main__":
    test_interest_table_includes_top_body_row()
    print("top body row OK")
    test_subsection_six_not_in_interest_table()
    print("subsection six excluded OK")
    test_interest_section_label_row_preserved()
    print("section label row preserved OK")
    test_trailing_investment_income_table()
    print("trailing investment table OK")
    test_gap_text_includes_fee_section_heading()
    print("gap section headings OK")
