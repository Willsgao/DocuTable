# -*- coding: utf-8 -*-
"""P28 类页：变化原因表末行落在大间隙中，应归入上一表而非 TEXT。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
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
        page=28,
        item_index=idx or f"i_{y0}_{x0}",
        y_mid=(y0 + y1) / 2,
    )


def _make_p28_like_page() -> PageSource:
    items: list[SourceItem] = []

    # region0: 利润摘要表
    items.append(_item("单位：千元", 400, 115))
    items.append(_item("项目", 76, 130))
    items.append(_item("2024 年", 200, 130))
    items.append(_item("2023 年", 280, 130))
    items.append(_item("增减幅度", 360, 130))
    items.append(_item("一、营业收入", 76, 155))
    items.append(_item("22,981,527", 200, 155))
    items.append(_item("21,702,189", 280, 155))
    items.append(_item("5.89%", 360, 155))

    # region1: （二）变化幅度超过30% 表
    items.append(_item("（二）利润表中变化幅度超过30%的项目及变化原因", 51, 425))
    items.append(_item("单位：千元", 400, 440))
    items.append(_item("项目", 60, 455))
    items.append(_item("2024 年", 170, 455))
    items.append(_item("2023 年", 250, 455))
    items.append(_item("增减幅度", 330, 455))
    items.append(_item("变化原因", 410, 455))

    change_rows = [
        ("手续费及佣金支出", "51,968", "88,822", "-41.49%代理业务支出减少"),
        ("其他收益", "113,604", "280,170", "-59.45%政府补助减少"),
        ("公允价值变动损益", "691,826", "157,724", "338.63%基金估值变动"),
        ("汇兑损益", "-648,315", "-256,758", "152.50%外汇掉期业务估值变动"),
        ("其他资产减值损失", "12,782", "128,895", "-90.08%抵债资产减值计提减少"),
        ("营业外收入", "5,512", "9,854", "-44.06%其他营业外收入减少"),
        ("营业外支出", "66,292", "22,689", "192.18%其他营业外支出增加"),
        ("重新计量设定受益计划净负债", "-107,410", "-55,700", "92.84%设定受益计划精算损失变动"),
    ]
    y = 475.0
    for label, v1, v2, pct_reason in change_rows:
        items.append(_item(label, 60, y))
        items.append(_item(v1, 170, y))
        items.append(_item(v2, 250, y))
        items.append(_item(pct_reason, 410, y))
        y += 16.0
    items.append(_item("或净资产的变动", 60, y))

    # 大间隙中的表尾续行（region1 y1=610 之下、region2 y0=703 之上）
    items.append(_item("其他债权投资公允价值变动", 60, 665))
    items.append(_item("1,162,092", 170, 665))
    items.append(_item("442,980", 250, 665))
    items.append(_item("162.34%债券估值变动", 410, 665))

    # 下一张表的小节标题（应在 TEXT，不进变化原因表）
    items.append(_item("（三）报告期各项业务收入构成情况", 51, 688))

    # region2: 收入构成表头 + 首行
    items.append(_item("单位：千元", 400, 708))
    items.append(_item("项目", 60, 718))
    items.append(_item("金额", 170, 718))
    items.append(_item("占比", 250, 718))
    items.append(_item("本报告期比上年同期增减", 310, 718))
    items.append(_item("2023 年", 450, 718))
    items.append(_item("存放中央银行款项", 60, 733))
    items.append(_item("899,467", 170, 733))
    items.append(_item("1.90%", 250, 733))
    items.append(_item("3.15%", 360, 733))
    items.append(_item("872,031", 450, 733))

    regions = [
        RegionBox(76, 105, 518, 335, 1.0, 0),
        RegionBox(51, 425, 541, 610, 1.0, 1),
        RegionBox(51, 703, 541, 743, 1.0, 2),
    ]
    return PageSource(
        page_number=28,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_change_table_absorbs_gap_tail_row():
    page = _make_p28_like_page()
    result = build_page(page)
    tables = [e.table for e in result.entries if e.table]
    assert len(tables) >= 2, "expected multiple tables"

    change_table = None
    for tbl in tables:
        flat = " ".join(c for r in dense_rows(tbl) for c in r if c)
        if "手续费及佣金支出" in flat and "变化幅度" in flat or "变化原因" in flat:
            change_table = tbl
            break
    assert change_table is not None, "change reason table not found"

    rows = dense_rows(change_table)
    flat = " ".join(c for r in rows for c in r if c)
    assert "其他债权投资公允价值变动" in flat, flat
    assert "1,162,092" in flat, flat
    assert "442,980" in flat, flat
    assert "162.34%" in flat, flat
    assert "债券估值变动" in flat, flat


def test_gap_tail_row_not_standalone_text():
    page = _make_p28_like_page()
    result = build_page(page)
    texts = [
        e.text_block.text
        for e in result.entries
        if e.kind == "text" and e.text_block
    ]
    for t in texts:
        if "其他债权投资公允价值变动" in t and "1,162,092" in t:
            raise AssertionError(f"orphan row should not remain as TEXT: {t!r}")


if __name__ == "__main__":
    test_change_table_absorbs_gap_tail_row()
    print("gap tail in table OK")
    test_gap_tail_row_not_standalone_text()
    print("not standalone text OK")
