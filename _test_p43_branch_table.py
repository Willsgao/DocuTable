# -*- coding: utf-8 -*-
"""P43 分支机构表：6 列坐标分列、地址折行、表前说明剥离、数据不丢失。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.table_access import dense_rows


def _item(text: str, x0: float, y0: float, x1: float | None = None) -> SourceItem:
    x1 = x1 if x1 is not None else x0 + max(len(text) * 4.2, 24)
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x1, y0 + 10),
        page=43,
        item_index=f"p43_{y0}_{x0}_{hash(text) & 0xFFFF}",
        y_mid=y0 + 5,
    )


_BRANCH_ROWS = [
    ("0", "总行", "成都市西御街 16 号", "0", "1384", "219,125,312", []),
    (
        "1", "重庆分行", "重庆市江北区建新北路 38 号附 5 号",
        "12", "310", "33,333,450",
        ["及建新北路 38 号 2 幢 17、18 层"],
    ),
    (
        "2", "西安分行", "西安市高新区沣惠南路 18 号唐沣国",
        "6", "232", "49,722,573",
        ["际广场 D 座 1 至 3 层"],
    ),
    ("3", "广安分行", "四川省广安市广安区朝阳大道二段", "5", "107", "9,329,191", ["29、31、33 号"]),
    ("4", "资阳分行", "四川省资阳市雁江区建设北路二段", "3", "78", "6,966,886", ["66 号"]),
    ("5", "眉山分行", "四川省眉山市东坡区湖滨路南一段眉", "5", "129", "14,644,011", ["山东坡国际酒店附属建筑"]),
    (
        "6", "内江分行", "四川省内江市东兴区汉安大道西 289",
        "4", "85", "7,564,710",
        ["号附 265-287 号、附 263 号"],
    ),
    (
        "7", "南充分行", "四川省南充市顺庆区滨江北路二段",
        "5", "93", "7,080,176",
        ["72 号马电花园第 11 幢"],
    ),
    (
        "8", "宜宾分行", '四川省宜宾市南岸东区长江大道"莱',
        "4", "97", "23,787,823",
        ['茵河畔"7 号楼'],
    ),
    (
        "9", "乐山分行", "565、571、577、583、587、591",
        "4", "90", "12,321,981",
        ["号", "四川省乐山市市中区白燕路 559、"],
    ),
    (
        "10", "德阳分行", "四川省德阳市旌阳区沱江路 188 号",
        "3", "91", "16,294,813",
        ['"知汇华庭"裙楼 1、2 层'],
    ),
    (
        "11", "阿坝分行", "马江街 115 号州级周转房 2 期 5 单",
        "1", "24", "1,228,078",
        ["四川省阿坝羌族藏族自治州马尔康县", "元 1、2 层"],
    ),
    (
        "12", "泸州分行", "四川省泸州市江阳区一环路江阳南路",
        "4", "90", "16,248,842",
        ["段 17 号 2 号楼"],
    ),
    (
        "13", "绵阳分行", "四川省绵阳市高新区绵兴东路 113 号",
        "4", "98", "19,214,784",
        ["樊华广场 1-3 层"],
    ),
    ("14", "天府新区分行", "成都市天府新区湖畔路西段 30 号", "8", "229", "24,954,876", []),
    ("15", "西御支行", "成都市西御街 16 号 14 楼、26 楼", "1", "18(兼）", "1,410,466", []),
    ("16", "营业部", "成都市西御街 16 号", "8", "186", "147,472,761", []),
    ("17", "科技支行", "成都市高新区锦城大道 539 号", "5", "129", "10,866,248", []),
    ("18", "琴台支行", "成都市青羊正街 14 号", "9", "261", "34,912,007", []),
]


def _append_body_row(
    items: list[SourceItem],
    seq: str,
    name: str,
    addr: str,
    n_inst: str,
    n_staff: str,
    assets: str,
    row_y: float,
    *,
    scrambled: bool = False,
) -> None:
    """scrambled=True 模拟 Region 0 OCR 流：地址/名称顺序与阅读顺序不一致。"""
    parts = [
        _item(seq, 60, row_y, 72),
        _item(name, 98, row_y, 98 + len(name) * 5),
        _item(addr, 148, row_y, 148 + min(len(addr) * 4.8, 200)),
        _item(n_inst, 338, row_y, 358),
        _item(n_staff, 402, row_y, 425),
        _item(assets, 458, row_y, 510),
    ]
    if scrambled:
        order = [2, 1, 0, 3, 4, 5]  # 地址、名称、序号、三列数值
        for i in order:
            items.append(parts[i])
    else:
        items.extend(parts)


def _make_p43_branch_page(*, full: bool = False, scrambled: bool = False) -> PageSource:
    items: list[SourceItem] = []
    y = 196.0
    items.append(_item("和下辖的 210 家支行，具体经营网点如下：", 58, y, 340))
    y += 16
    for t, x0, x1 in [
        ("序号", 58, 78),
        ("机构名称", 95, 130),
        ("机构地址", 145, 200),
        ("机构数", 330, 365),
        ("员工数", 395, 430),
        ("资产规模（千元）", 455, 520),
    ]:
        items.append(_item(t, x0, y, x1))
    y += 18

    rows = _BRANCH_ROWS if full else _BRANCH_ROWS[:4]
    for seq, name, addr, n_inst, n_staff, assets, wraps in rows:
        _append_body_row(
            items, seq, name, addr, n_inst, n_staff, assets, y,
            scrambled=scrambled,
        )
        y += 16
        for wrap in wraps:
            items.append(_item(wrap, 148, y - 2, 148 + len(wrap) * 4.8))
            y += 14

    region = RegionBox(54, 191, 520, 759, 1.0, 0)
    return PageSource(
        page_number=43,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[region],
        is_table_page=True,
    )


def _assert_six_column_body(rows: list[list[str]]) -> None:
    body = [r for r in rows if str(r[0] or "").strip().isdigit()]
    assert body, rows
    for r in body:
        assert str(r[1] or "").strip(), f"机构名称丢失: {r}"
        assert str(r[2] or "").strip(), f"机构地址丢失: {r}"
        assert str(r[3] or "").strip(), f"机构数丢失: {r}"
        assert str(r[4] or "").strip(), f"员工数丢失: {r}"
        assert str(r[5] or "").strip(), f"资产规模丢失: {r}"


def test_branch_table_six_columns_and_address_wrap():
    page = _make_p43_branch_page()
    result = build_page(page)
    table = next(e.table for e in result.entries if e.table)
    assert table.grid.col_count >= 6, table.grid.col_count
    rows = dense_rows(table)
    zong = next(r for r in rows if str(r[0] or "").strip() == "0")
    assert str(zong[1] or "").strip() == "总行", zong
    assert "西御街" in str(zong[2] or ""), zong
    cq = next(r for r in rows if "重庆分行" in str(r[1] or ""))
    addr = str(cq[2] or "")
    assert "建新北路" in addr and "17、18 层" in addr, (cq, rows)
    orphan_wraps = [
        r for r in rows
        if str(r[0] or "").strip() == ""
        and not any(str(r[i] or "").strip() for i in (1, 3, 4, 5))
        and str(r[2] or "").strip()
    ]
    assert not orphan_wraps, orphan_wraps


def test_branch_table_caption_peeled_and_no_column_loss():
    page = _make_p43_branch_page(full=True, scrambled=True)
    result = build_page(page)
    texts = [e.text_block.text for e in result.entries if e.kind == "text" and e.text_block]
    assert any("经营网点如下" in t for t in texts), texts
    table = next(e.table for e in result.entries if e.table)
    assert table.grid.col_count >= 6, table.grid.col_count
    rows = dense_rows(table)
    assert not any("经营网点如下" in " ".join(str(c) for c in r) for r in rows), rows[0]
    _assert_six_column_body(rows)
    cq = next(r for r in rows if "重庆分行" in str(r[1] or ""))
    assert "17、18 层" in str(cq[2] or ""), cq
    les = next(r for r in rows if "乐山分行" in str(r[1] or ""))
    assert "白燕路" in str(les[2] or "") or "559" in str(les[2] or ""), les
    ms = next(r for r in rows if "眉山分行" in str(r[1] or ""))
    assert "酒店" in str(ms[2] or ""), ms
    assert len([r for r in rows if str(r[0] or "").strip().isdigit()]) == 19, len(rows)
    orphans = [
        r for r in rows
        if str(r[0] or "").strip() == ""
        and str(r[2] or "").strip()
        and not any(str(r[i] or "").strip() for i in (1, 3, 4, 5))
    ]
    assert not orphans, orphans


if __name__ == "__main__":
    test_branch_table_six_columns_and_address_wrap()
    test_branch_table_caption_peeled_and_no_column_loss()
    print("P43 branch table OK")
