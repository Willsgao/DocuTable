# -*- coding: utf-8 -*-
"""P14 类页：大间隙表前说明须按 y 顺序落在同一 TEXT，不拆 description。"""
from __future__ import annotations

import re
import sys

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.scope.gap_capture import plan_page_scopes


def _item(text: str, x0: float, y0: float, x1: float | None = None, y1: float | None = None, idx: str = "") -> SourceItem:
    x1 = x1 if x1 is not None else x0 + len(text) * 4.5
    y1 = y1 if y1 is not None else y0 + 10
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        page=14,
        item_index=idx or f"i_{y0}_{x0}",
        y_mid=(y0 + y1) / 2,
    )


def _make_p14_like_page() -> PageSource:
    items: list[SourceItem] = []
    # 荣誉 region 0 (y 78-242)
    for n, (y, title, org) in enumerate([
        (90, "2024 年成长力银行 50 强排名第一", "时代财经"),
        (110, "财资管理云平台创新案例", "《银行家》杂志社"),
        (130, "2024 年度 Wind 最佳投行", "万得信息技术股份有限公司"),
    ]):
        items.append(_item(f"{23+n}", 60, y))
        items.append(_item(title, 100, y))
        items.append(_item(org, 400, y))

    # 大间隙 242-429：29-30 + 4.11 + （一）+ 单位 + 列头
    items.append(_item("29", 60, 260))
    items.append(_item("2024 年度服务科技金融机构", 100, 260))
    items.append(_item("《21 世纪经济报道》", 400, 260))
    items.append(_item("30", 60, 280))
    items.append(_item("2024 卓越金融企业", 100, 280))
    items.append(_item("《经济观察报》", 400, 280))
    items.append(_item("4.11 近三年主要会计数据和财务指标", 60, 310))
    items.append(_item("（一）近三年主要会计数据", 60, 330))
    items.append(_item("单位：千元", 60, 350))
    items.append(_item("项目", 60, 370))
    items.append(_item("2024 年", 200, 370))
    items.append(_item("2023 年", 280, 370))
    items.append(_item("增减", 360, 370))
    items.append(_item("2022 年", 440, 370))

    # 财务 region 1
    items.append(_item("营业收入", 60, 440))
    items.append(_item("22,981,527", 200, 440))
    items.append(_item("21,702,189", 280, 440))
    items.append(_item("5.89%", 360, 440))
    items.append(_item("20,241,312", 440, 440))

    regions = [
        RegionBox(58, 78, 544, 242, 1.0, 0),
        RegionBox(54, 429, 541, 542, 1.0, 1),
        RegionBox(54, 606, 541, 749, 1.0, 2),
    ]
    return PageSource(
        page_number=14,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_large_gap_preserves_order():
    page = _make_p14_like_page()
    plan = plan_page_scopes(page)
    texts = [b.text for b in plan.gap_texts]
    joined = "\n".join(texts)
    pos_29 = joined.find("29")
    pos_30 = joined.find("30")
    pos_411 = joined.find("4.11")
    pos_sec = joined.find("（一）")
    assert pos_29 >= 0 and pos_30 > pos_29, joined
    assert pos_411 > pos_30, joined
    assert pos_sec > pos_411, joined
    assert "单位：千元" not in joined, "unit row should be pre_header not TEXT"
    assert "项目" not in joined or "2024 年" not in joined, "column header should be pre_header"


def test_pre_header_has_column_headers():
    page = _make_p14_like_page()
    plan = plan_page_scopes(page)
    assert plan.scopes, "expected financial table scope"
    fin = plan.scopes[-1]
    pre_text = " ".join(str(it.text) for it in fin.pre_header_items)
    assert "单位" in pre_text or "千元" in pre_text, pre_text
    assert "项目" in pre_text, pre_text
    assert "2024" in pre_text, pre_text


def test_build_page_text_order():
    page = _make_p14_like_page()
    result = build_page(page)
    texts = [e.text_block.text for e in result.entries if e.kind == "text" and e.text_block]
    # 29/30/4.11/（一）应在同一 TEXT 或相邻且顺序正确
    for i, t in enumerate(texts):
        if "29" in t and "4.11" in t:
            assert t.find("29") < t.find("30") < t.find("4.11") < t.find("（一）"), f"block {i}: {t[:120]}"
            return
    # 允许跨块但 y 顺序
    ordered = sorted(
        [(e.y0, e.text_block.text) for e in result.entries if e.kind == "text" and e.text_block],
        key=lambda x: x[0],
    )
    blob = "\n".join(t for _, t in ordered)
    assert blob.find("29") < blob.find("30") < blob.find("4.11") < blob.find("（一）"), blob


def test_build_page_table_has_headers():
    page = _make_p14_like_page()
    result = build_page(page)
    tables = [e.table for e in result.entries if e.table]
    assert tables, "expected table"
    from codes.table_engine.table_access import dense_rows
    rows = dense_rows(tables[0])
    flat = " ".join(c for r in rows for c in r if c)
    assert "单位" in flat or "千元" in flat, flat[:200]
    assert "项目" in flat, flat[:200]
    assert "营业收入" in flat, flat


def test_supplement_scope_from_above_gap():
    """scope 首行已是表体时，应向上搜到单位/列头。"""
    from codes.table_engine.scope.region_scope import build_table_scope
    from codes.table_engine.scope.header_supplement import supplement_scope_missing_headers

    page = _make_p14_like_page()
    region = page.table_regions[1]
    body_only = [
        it for it in page.items
        if "营业收入" in (it.text or "") or "22,981" in (it.text or "")
    ]
    scope = build_table_scope(page, region, 1, pre_header_items=[])
    scope.items = body_only
    scope = supplement_scope_missing_headers(page, scope)
    joined = " ".join(str(it.text) for it in scope.pre_header_items)
    all_text = " ".join(str(it.text) for it in scope.items)
    assert "单位" in joined or "千元" in joined or "单位" in all_text, f"pre={joined!r} all={all_text[:80]!r}"
    assert "项目" in joined or "项目" in all_text, joined


def test_leading_header_reattach_from_text_tail():
    """表头误落在 TEXT 底部时，entry 级应回补到表首。"""
    from codes.table_engine.models import DocumentEntry, TextBlock
    from codes.table_engine.split.leading_header_reattach import apply_leading_header_reattach
    from codes.table_engine.split.table_text_split import slice_structured_table
    from codes.table_engine.table_access import dense_rows

    page = _make_p14_like_page()
    result = build_page(page)
    table_entry = next(e for e in result.entries if e.table)
    rows = dense_rows(table_entry.table)
    header_line_count = 0
    for r in rows:
        if any(c in ("单位：千元", "项目") for c in r):
            header_line_count += 1
        else:
            break
    assert header_line_count >= 1, rows[:3]

    header_rows = rows[:header_line_count]
    header_text = "\n".join(
        " ".join(c for c in r if c).strip() for r in header_rows if any(c for c in r)
    )
    trimmed = slice_structured_table(table_entry.table, header_line_count)
    header_items = [
        it for it in page.items
        if (it.text or "").strip() in (
            "单位：千元", "项目", "2024 年", "2023 年", "增减", "2022 年",
        )
    ]
    text_block = TextBlock(
        page=14,
        y0=min(it.bbox.y0 for it in header_items),
        y1=max(it.bbox.y1 for it in header_items),
        text=header_text,
        source_items=[it.item_index for it in header_items],
    )
    entries = [
        DocumentEntry(kind="text", page=14, y0=text_block.y0, y1=text_block.y1, text_block=text_block),
        DocumentEntry(
            kind="table", page=14, y0=trimmed.y0, y1=trimmed.y1,
            table=trimmed, entry_id=table_entry.entry_id,
        ),
    ]
    fixed = apply_leading_header_reattach(entries, page)
    tbl = next(e.table for e in fixed if e.table)
    flat = " ".join(c for r in dense_rows(tbl)[:3] for c in r if c)
    assert "单位" in flat or "千元" in flat, flat
    assert "项目" in flat, flat
    assert "营业收入" in flat, flat


def _make_p14_cashflow_wrap_page() -> PageSource:
    """region1 末行「经营活动…流量」+ region2 首行「净额」同值折行。"""
    items: list[SourceItem] = []
    items.append(_item("单位：千元", 60, 350))
    items.append(_item("项目", 60, 370))
    items.append(_item("2024 年", 200, 370))
    items.append(_item("2023 年", 280, 370))
    items.append(_item("增减", 360, 370))
    items.append(_item("2022 年", 440, 370))
    items.append(_item("营业收入", 60, 440))
    items.append(_item("22,981,527", 200, 440))
    items.append(_item("21,702,189", 280, 440))
    items.append(_item("5.89%", 360, 440))
    items.append(_item("20,241,312", 440, 440))
    # region1 末行：标签折行（无净额）
    items.append(_item("经营活动产生的现金流量", 60, 530))
    items.append(_item("-94,226,009", 200, 530))
    items.append(_item("-23,753,784", 280, 530))
    items.append(_item("296.68%", 360, 530))
    items.append(_item("9,692,134", 440, 530))
    # region2 首行：净额尾片 + 同值
    items.append(_item("净额", 60, 610))
    items.append(_item("-94,226,009", 200, 610))
    items.append(_item("-23,753,784", 280, 610))
    items.append(_item("296.68%", 360, 610))
    items.append(_item("9,692,134", 440, 610))
    # 资产负债表头+数据
    items.append(_item("项目", 60, 630))
    items.append(_item("2024 年", 200, 630))
    items.append(_item("2023 年", 280, 630))
    items.append(_item("本报告期末比上年度", 360, 630))
    items.append(_item("2022 年", 440, 630))
    items.append(_item("12 月 31 日", 200, 650))
    items.append(_item("12 月 31 日", 280, 650))
    items.append(_item("末增减", 360, 650))
    items.append(_item("12 月 31 日", 440, 650))
    items.append(_item("总资产", 60, 670))
    items.append(_item("1,250,116,154", 200, 670))
    items.append(_item("1,091,243,069", 280, 670))
    items.append(_item("14.56%", 360, 670))
    items.append(_item("917,650,305", 440, 670))

    regions = [
        RegionBox(54, 429, 541, 542, 1.0, 1),
        RegionBox(54, 606, 541, 749, 1.0, 2),
    ]
    return PageSource(
        page_number=14,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=regions,
        is_table_page=True,
    )


def test_sibling_label_suffix_merge_no_duplicate():
    """「经营活动…现金流量」+「净额」同值折行应合并，不重复数值。"""
    from codes.table_engine.table_access import dense_rows

    page = _make_p14_cashflow_wrap_page()
    result = build_page(page)
    all_rows = [
        row
        for e in result.entries
        if e.table
        for row in dense_rows(e.table)
    ]
    merged = [
        row for row in all_rows
        if "现金流量" in str(row[0] or "") and "净额" in str(row[0] or "")
    ]
    assert merged, [str(r[0]) for r in all_rows]

    for row in all_rows:
        if str(row[0] or "").strip() == "净额":
            raise AssertionError(f"orphan 净额 row: {row}")


def test_adjacent_tables_without_text_overlap_repair():
    """两 region 间隙无叙述、边界折行重叠 → entry 级去重。"""
    from codes.table_engine.table_access import dense_rows

    items: list[SourceItem] = []
    # region1 末行
    for text, x, y in [
        ("项目", 60, 370), ("2024 年", 200, 370), ("2023 年", 280, 370),
        ("增减", 360, 370), ("2022 年", 440, 370),
        ("营业收入", 60, 440), ("22,981,527", 200, 440),
        ("经营活动产生的现金流量", 60, 520),
        ("-94,226,009", 200, 520), ("-23,753,784", 280, 520),
        ("296.68%", 360, 520), ("9,692,134", 440, 520),
    ]:
        items.append(_item(text, x, y))
    # region2 首行净额（gap 130pt，不触发 region 合并）
    for text, x, y in [
        ("净额", 60, 662), ("-94,226,009", 200, 662),
        ("-23,753,784", 280, 662), ("296.68%", 360, 662), ("9,692,134", 440, 662),
        ("项目", 60, 682), ("2024 年", 200, 682), ("2023 年", 280, 682),
        ("总资产", 60, 710), ("1,250,116,154", 200, 710),
    ]:
        items.append(_item(text, x, y))
    page = PageSource(
        page_number=14,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[
            RegionBox(54, 429, 541, 542, 1.0, 0),
            RegionBox(54, 672, 541, 749, 1.0, 1),
        ],
        is_table_page=True,
    )
    from codes.table_engine.scope.gap_capture import plan_page_scopes
    assert plan_page_scopes(page).scopes, "expected at least one table scope"

    result = build_page(page)
    for e in result.entries:
        if e.table:
            for row in dense_rows(e.table):
                if str(row[0] or "").strip() == "净额":
                    raise AssertionError(f"orphan 净额: {row}")
    merged = any(
        "现金流量" in str(r[0] or "") and "净额" in str(r[0] or "")
        for e in result.entries if e.table
        for r in dense_rows(e.table)
    )
    assert merged, "expected merged cashflow label"


def test_overlap_repair_skips_intermediate_stub_table():
    """利润表与资产负债表之间夹小表 region 时，仍应跨表合并「净额」。"""
    import copy

    from codes.table_engine.models import DocumentEntry
    from codes.table_engine.scope.gap_capture import plan_page_scopes
    from codes.table_engine.scope.region_scope import build_table_scope
    from codes.table_engine.split.boundary_overlap import (
        _NO_NARRATIVE_MAX_GAP_PT,
        detect_table_boundary_overlap,
        iter_overlap_candidate_table_pairs,
    )
    from codes.table_engine.split.structure_split import apply_adjacent_table_boundary_repair
    from codes.table_engine.split.table_text_split import _table_to_entry
    from codes.table_engine.table_builder import build_table_from_scope
    from codes.table_engine.table_access import dense_rows

    page = _make_p14_cashflow_wrap_page()
    plan = plan_page_scopes(page)
    scopes = plan.scopes
    if len(scopes) < 2:
        scopes = [
            build_table_scope(page, page.table_regions[0], 0),
            build_table_scope(page, page.table_regions[1], 1),
        ]
    income = build_table_from_scope(scopes[0])
    balance = build_table_from_scope(scopes[1])
    assert income and balance

    # 模拟中间 header-stamp 小表（阻断旧版「仅紧邻表对」逻辑）
    stub_rows = income.rows[:2]
    stub = copy.copy(income)
    stub.rows = copy.deepcopy(stub_rows)
    stub.y0 = income.y1 + 12.0
    stub.y1 = stub.y0 + 28.0
    entries = [
        _table_to_entry(income, 0),
        _table_to_entry(stub, 1),
        _table_to_entry(balance, 2),
    ]
    entries[1].y0 = stub.y0
    entries[1].y1 = stub.y1
    pairs = iter_overlap_candidate_table_pairs(entries)
    assert any(
        p[0].entry_id == 0 and p[1].entry_id == 2 for p in pairs
    ), [(a.entry_id, b.entry_id) for a, b in pairs]

    overlap = detect_table_boundary_overlap(
        entries[0].table, entries[2].table, max_gap=_NO_NARRATIVE_MAX_GAP_PT,
    )
    assert overlap is not None, "expected boundary overlap income vs balance"

    fixed = apply_adjacent_table_boundary_repair(entries)
    for e in fixed:
        if e.table:
            for row in dense_rows(e.table):
                if str(row[0] or "").strip() == "净额":
                    raise AssertionError(f"orphan 净额: {row}")


def test_gap_narrative_cashflow_deduped_from_text():
    """间隙 narrative 含已并入表的现金流量行 → 不得再以 TEXT 重复出现。"""
    from codes.table_engine.scope.gap_capture import _make_text_block, plan_page_scopes
    from codes.table_engine.scope.header_supplement import supplement_scope_missing_headers
    from codes.table_engine.split.content_partition import filter_description_captions
    from codes.table_engine.split.structure_split import (
        apply_adjacent_table_boundary_repair,
        apply_structure_split,
    )
    from codes.table_engine.split.table_text_split import build_page_entries
    from codes.table_engine.split.content_partition import apply_content_partition
    from codes.table_engine.table_builder import build_table_from_scope
    from codes.table_engine.table_access import dense_rows

    page = _make_p14_cashflow_wrap_page()
    plan = plan_page_scopes(page)
    tables = [
        build_table_from_scope(supplement_scope_missing_headers(page, s))
        for s in plan.scopes
    ]
    gap_items = [
        _item("经营活动产生的现金流量", 60, 555),
        _item("-94,226,009", 200, 555),
        _item("-23,753,784", 280, 555),
        _item("296.68%", 360, 555),
        _item("9,692,134", 440, 555),
        _item("净额", 60, 565),
    ]
    gap_texts = filter_description_captions(tables, list(plan.gap_texts))
    gap_texts.append(_make_text_block(14, gap_items))

    entries = build_page_entries(tables=tables, gap_texts=gap_texts)
    entries = apply_structure_split(entries, page)
    entries = apply_adjacent_table_boundary_repair(entries)
    entries = apply_content_partition(entries, page)

    merged = any(
        "现金流量" in str(r[0] or "") and "净额" in str(r[0] or "")
        for e in entries if e.table
        for r in dense_rows(e.table)
    )
    assert merged, "expected merged cashflow row in table"

    for e in entries:
        if e.text_block:
            text = e.text_block.text
            assert "现金流量" not in text, f"duplicate cashflow in TEXT: {text!r}"
            assert text.strip() != "净额", f"orphan suffix in TEXT: {text!r}"


def test_wrapped_change_metric_header_merged_in_table():
    """「本报告期比上年同期」+ 次行「增减」应合并为同一列表头，不得落成独立 TEXT。"""
    from codes.table_engine.table_access import dense_rows

    items: list[SourceItem] = []
    for text, x, y in [
        ("单位：千元", 60, 350),
        ("项目", 60, 370), ("2024 年", 200, 370), ("2023 年", 280, 370),
        ("本报告期比上年同期", 360, 370), ("2022 年", 440, 370),
        ("增减", 360, 385),
        ("营业收入", 60, 440), ("22,981,527", 200, 440), ("21,702,189", 280, 440),
        ("5.89%", 360, 440), ("20,241,312", 440, 440),
    ]:
        items.append(_item(text, x, y))
    page = PageSource(
        page_number=14,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[RegionBox(54, 429, 541, 542, 1.0, 0)],
        is_table_page=True,
    )
    result = build_page(page)
    orphan = [
        e.text_block.text.strip()
        for e in result.entries
        if e.kind == "text" and e.text_block and e.text_block.text.strip() in ("增减", "末增减")
    ]
    assert not orphan, f"增减不应单独成 TEXT: {orphan}"

    table = next(e.table for e in result.entries if e.table)
    header_cells = [c for c in dense_rows(table)[1] if c]
    col3 = next((c for c in header_cells if "比上年同期" in c), "")
    assert "增减" in col3, f"col3 header missing 增减: {header_cells!r}"
    assert col3.index("比上年同期") < col3.index("增减"), col3


def test_double_layer_balance_header_merged():
    """资产负债表双层表头：年份行 + 月日/末增减次行应纵并到同列。"""
    from codes.table_engine.table_access import dense_rows

    items: list[SourceItem] = []
    for text, x, y in [
        ("单位：千元", 60, 600),
        ("项目", 60, 620), ("2024 年", 200, 620), ("2023 年", 280, 620),
        ("本报告期末比上年度", 360, 620), ("2022 年", 440, 620),
        ("12 月 31 日", 200, 635), ("12 月 31 日", 280, 635),
        ("末增减", 360, 635), ("12 月 31 日", 440, 635),
        ("总资产", 60, 660), ("1,250,116,154", 200, 660),
        ("1,091,243,069", 280, 660), ("14.56%", 360, 660), ("917,650,305", 440, 660),
    ]:
        items.append(_item(text, x, y))
    page = PageSource(
        page_number=14,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[RegionBox(54, 606, 541, 749, 1.0, 0)],
        is_table_page=True,
    )
    result = build_page(page)
    table = next(e.table for e in result.entries if e.table)
    header = dense_rows(table)[1]
    assert "12 月 31 日" in header[1] and "2024" in header[1], header
    assert "末增减" in header[3] and "比上年度" in header[3], header
    assert all(c for c in header[1:]), f"no empty value header cols: {header}"


def test_full_p14_gap_wrapped_zengjian_stays_in_table_header():
    """大间隙折行「增减」须并入利润表表头，不得落成表后独立 TEXT。"""
    from codes.table_engine.table_access import dense_rows

    items: list[SourceItem] = []
    # region 0 荣誉
    items.append(_item("23", 60, 90))
    items.append(_item("时代财经", 400, 90))
    # 大间隙 242-429
    items.append(_item("29", 60, 260))
    items.append(_item("2024 年度服务科技金融机构", 100, 260))
    items.append(_item("30", 60, 280))
    items.append(_item("4.11 近三年主要会计数据和财务指标", 60, 310))
    items.append(_item("（一）近三年主要会计数据", 60, 330))
    items.append(_item("单位：千元", 60, 350))
    items.append(_item("项目", 60, 370))
    items.append(_item("2024 年", 200, 370))
    items.append(_item("2023 年", 280, 370))
    items.append(_item("本报告期比上年同期", 360, 370))
    items.append(_item("2022 年", 440, 370))
    items.append(_item("增减", 360, 385))  # 折行次行，与列标同 x
    # region 1 利润表体
    for label, vals, y in [
        ("营业收入", ["22,981,527", "21,702,189", "5.89%", "20,241,312"], 440),
        ("营业利润", ["15,226,559", "13,905,168", "9.50%", "11,698,169"], 460),
        ("经营活动产生的现金流量净额", ["-94,226,009", "-23,753,784", "296.68%", "9,692,134"], 520),
    ]:
        items.append(_item(label, 60, y))
        for v, x in zip(vals, [200, 280, 360, 440]):
            items.append(_item(v, x, y))
    # region 2 资产负债表
    items.append(_item("项目", 60, 620))
    items.append(_item("2024 年", 200, 620))
    items.append(_item("2023 年", 280, 620))
    items.append(_item("本报告期末比上年度", 360, 620))
    items.append(_item("2022 年", 440, 620))
    items.append(_item("12 月 31 日", 200, 635))
    items.append(_item("12 月 31 日", 280, 635))
    items.append(_item("末增减", 360, 635))
    items.append(_item("12 月 31 日", 440, 635))
    items.append(_item("总资产", 60, 660))
    items.append(_item("1,250,116,154", 200, 660))
    items.append(_item("1,091,243,069", 280, 660))
    items.append(_item("14.56%", 360, 660))
    items.append(_item("917,650,305", 440, 660))

    page = PageSource(
        page_number=14,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[
            RegionBox(58, 78, 544, 242, 1.0, 0),
            RegionBox(54, 429, 541, 542, 1.0, 1),
            RegionBox(54, 606, 541, 749, 1.0, 2),
        ],
        is_table_page=True,
    )
    result = build_page(page)
    text_blocks = [
        e.text_block.text.strip()
        for e in result.entries
        if e.kind == "text" and e.text_block and e.text_block.text.strip()
    ]
    for text in text_blocks:
        assert text not in ("增减", "末增减"), f"表头碎片不得单独成 TEXT: {text!r}"
        assert text.strip() != "增减", text

    tables = [e.table for e in result.entries if e.table]
    assert len(tables) >= 2, len(tables)
    income_table = next(
        t for t in tables
        if any("营业收入" in str(c) for r in dense_rows(t) for c in r)
    )
    income_rows = dense_rows(income_table)
    income_header = next(
        (r for r in income_rows if any("项目" in str(c) for c in r)),
        None,
    )
    assert income_header is not None, income_rows[:4]
    change_col = next((c for c in income_header if c and "比上年同期" in str(c)), "")
    assert "增减" in change_col, f"利润表表头缺增减: {income_header!r}"

    # 阅读顺序：增减不得出现在利润表 entry 之后的独立 TEXT
    income_entry = next(e for e in result.entries if e.table and "营业收入" in str(dense_rows(e.table)))
    income_idx = result.entries.index(income_entry)
    for later in result.entries[income_idx + 1:]:
        if later.kind == "text" and later.text_block:
            assert later.text_block.text.strip() not in ("增减", "末增减"), later.text_block.text


def test_misplaced_zengjian_between_regions_attaches_to_income_header():
    """OCR 将「增减」落在两 region 间隙时，仍应回补利润表表头而非表后 TEXT。"""
    from codes.table_engine.table_access import dense_rows

    items: list[SourceItem] = []
    for text, x, y in [
        ("单位：千元", 60, 350),
        ("项目", 60, 370), ("2024 年", 200, 370), ("2023 年", 280, 370),
        ("本报告期比上年同期", 360, 370), ("2022 年", 440, 370),
        ("营业收入", 60, 440), ("22,981,527", 200, 440),
        ("21,702,189", 280, 440), ("5.89%", 360, 440), ("20,241,312", 440, 440),
        ("经营活动产生的现金流量净额", 60, 520),
        ("-94,226,009", 200, 520), ("-23,753,784", 280, 520),
        ("296.68%", 360, 520), ("9,692,134", 440, 520),
        ("增减", 360, 565),  # 误落在 region1/2 间隙
        ("项目", 60, 620), ("2024 年", 200, 620), ("2023 年", 280, 620),
        ("总资产", 60, 660), ("1,250,116,154", 200, 660),
    ]:
        items.append(_item(text, x, y))
    page = PageSource(
        page_number=14,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[
            RegionBox(54, 429, 541, 542, 1.0, 0),
            RegionBox(54, 606, 541, 749, 1.0, 1),
        ],
        is_table_page=True,
    )
    result = build_page(page)
    for e in result.entries:
        if e.kind == "text" and e.text_block:
            assert e.text_block.text.strip() not in ("增减",), e.text_block.text
    income = next(e.table for e in result.entries if e.table)
    header = next(r for r in dense_rows(income) if any("比上年同期" in str(c) for c in r))
    assert any("增减" in str(c) for c in header), header


def test_profit_balance_no_duplicate_rows_between_tables():
    """利润表末行不得重复出现在资产负债表首部（用户 P14 类页）。"""
    from codes.table_engine.table_access import dense_rows

    items: list[SourceItem] = []
    for text, x, y in [
        ("单位：千元", 350, 350),
        ("项目", 60, 370), ("2024 年", 200, 370), ("2023 年", 280, 370),
        ("本报告期比上年同期", 360, 370), ("2022 年", 440, 370), ("增减", 360, 380),
    ]:
        items.append(_item(text, x, y))
    rows_data = [
        ("营业收入", ["22,981,527", "21,702,189", "5.89%", "20,241,312"], 400),
        ("营业利润", ["15,226,559", "13,905,168", "9.50%", "11,698,169"], 420),
        ("利润总额", ["15,165,779", "13,892,333", "9.17%", "11,681,213"], 440),
        ("净利润", ["12,850,233", "11,671,933", "10.10%", "10,043,073"], 460),
        ("归属于母公司股东的净利润", ["12,858,380", "11,671,118", "10.17%", "10,042,377"], 480),
        ("归属于母公司股东的扣除", ["12,824,652", "11,466,741", "11.84%", "9,969,570"], 500),
        ("非经常性损益后的净利润", ["", "", "", ""], 510),
        ("经营活动产生的现金流量", ["-94,226,009", "-23,753,784", "296.68%", "9,692,134"], 530),
        ("净额", ["", "", "", ""], 540),
    ]
    for label, vals, y in rows_data:
        items.append(_item(label, 60, y))
        for v, x in zip(vals, [200, 280, 360, 440]):
            if v:
                items.append(_item(v, x, y))
    for text, x, y in [
        ("归属于母公司股东的扣除", 60, 555), ("12,824,652", 200, 555),
        ("11,466,741", 280, 555), ("11.84%", 360, 555), ("9,969,570", 440, 555),
        ("非经常性损益后的净利润", 60, 565),
        ("经营活动产生的现金流量", 60, 575), ("-94,226,009", 200, 575),
        ("-23,753,784", 280, 575), ("296.68%", 360, 575), ("9,692,134", 440, 575),
        ("净额", 60, 585),
        ("项目", 60, 600), ("2024 年", 200, 600), ("2023 年", 280, 600),
        ("本报告期末比上年度", 360, 600), ("2022 年", 440, 600),
        ("12 月 31 日", 200, 610), ("12 月 31 日", 280, 610), ("末增减", 360, 610),
        ("12 月 31 日", 440, 610),
        ("总资产", 60, 630), ("1,250,116,154", 200, 630),
        ("1,091,243,069", 280, 630), ("14.56%", 360, 630), ("917,650,305", 440, 630),
    ]:
        items.append(_item(text, x, y))

    page = PageSource(
        page_number=14,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[
            RegionBox(54, 390, 541, 545, 1.0, 0),
            RegionBox(54, 550, 541, 680, 1.0, 1),
        ],
        is_table_page=True,
    )
    result = build_page(page)
    tables = [dense_rows(e.table) for e in result.entries if e.table]
    assert len(tables) >= 2, len(tables)

    income_labels = [str(r[0] or "").strip() for r in tables[0]]
    balance_labels = [str(r[0] or "").strip() for r in tables[-1]]
    assert sum(1 for lb in income_labels if "归属于母公司股东的扣除" in lb) == 1, income_labels
    assert not any(lb.strip() == "非经常性损益后的净利润" for lb in income_labels), income_labels
    assert "归属于母公司股东的扣除" not in balance_labels, balance_labels
    assert "经营活动产生的现金流量" not in " ".join(balance_labels), balance_labels
    assert any("总资产" in lb for lb in balance_labels), balance_labels

    all_labels = income_labels + balance_labels
    for label in ("归属于母公司股东的扣除",):
        assert sum(1 for lb in all_labels if label in lb) == 1, f"duplicate {label}: {all_labels}"


if __name__ == "__main__":
    test_large_gap_preserves_order()
    print("plan_page_scopes order OK")
    test_pre_header_has_column_headers()
    print("pre_header has column headers OK")
    test_build_page_text_order()
    print("build_page text order OK")
    test_build_page_table_has_headers()
    print("build_page table headers OK")
    test_supplement_scope_from_above_gap()
    print("supplement_scope from above OK")
    test_leading_header_reattach_from_text_tail()
    print("leading_header_reattach OK")
    test_sibling_label_suffix_merge_no_duplicate()
    print("sibling label suffix merge OK")
    test_adjacent_tables_without_text_overlap_repair()
    print("adjacent tables overlap repair OK")
    test_overlap_repair_skips_intermediate_stub_table()
    print("overlap repair across stub table OK")
    test_gap_narrative_cashflow_deduped_from_text()
    print("gap narrative cashflow dedup OK")
    test_wrapped_change_metric_header_merged_in_table()
    print("wrapped change metric header OK")
    test_double_layer_balance_header_merged()
    print("double layer balance header OK")
    test_full_p14_gap_wrapped_zengjian_stays_in_table_header()
    print("full P14 gap wrapped zengjian OK")
    test_misplaced_zengjian_between_regions_attaches_to_income_header()
    print("misplaced zengjian between regions OK")
    test_profit_balance_no_duplicate_rows_between_tables()
    print("profit balance no duplicate rows OK")
