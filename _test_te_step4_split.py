# -*- coding: utf-8
"""Table Engine Step 4 — 披露表 Layout + 表文几何分裂。"""

import argparse
import sys
from typing import List

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.split.row_classify import is_tail_annotation_row, row_has_value_data
from codes.table_engine.pipeline import build_page, entry_counts, primary_table
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.split.table_text_split import find_pillar_table_body_start_row, row_y_bounds
from codes.table_engine.table_builder import build_table_from_region
from codes.table_engine.table_access import cell_text, dense_rows, find_row_index

CACHE = DEFAULT_PILLAR_CACHE
PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def _flat_table(t) -> str:
    return " ".join(c.text for row in t.rows for c in row if c)


def _text_entries(result):
    return [e for e in result.entries if e.kind == "text"]


def _table_entries(result):
    return [e for e in result.entries if e.kind == "table"]


def test_disclosure_layout(page_num: int, expected_layout: str) -> None:
    page = load_page(CACHE, page_num)
    result = build_page(page)
    t = primary_table(result)
    check(f"P{page_num} built", t is not None)
    if t:
        ok = t.layout_id == expected_layout or t.layout_id == "constraint_grid"
        check(f"P{page_num} layout ok", ok, t.layout_id)


def test_page_entries(page_num: int, *, max_text: int, max_table: int, min_table: int = 1) -> None:
    page = load_page(CACHE, page_num)
    result = build_page(page)
    n_table, n_text = entry_counts(result)
    print(
        f"  P{page_num}: text={n_text} table={n_table} "
        f"rows={len(primary_table(result).rows) if primary_table(result) else 0}"
    )
    check(f"P{page_num} table count", min_table <= n_table <= max_table, f"got {n_table}")
    check(f"P{page_num} text count", n_text <= max_text, f"got {n_text}")
    return result


def test_row_classify_value_first() -> None:
    print("--- 行分类：值列优先 ---")
    from codes.table_engine.geometry.numeric import (
        has_valid_thousand_separators,
        is_merged_numeric_cell,
        is_numeric_data_cell,
    )

    check("27a with values is data", row_has_value_data(["27a 季末余额", "607,773", "750,116"]))
    check("dash is data", row_has_value_data(["10 名义本金", "-", "-"]))
    check("28 label-only no values", not row_has_value_data(["28 调整后资产a2", "", ""]))
    check("footnote not data", not row_has_value_data(["1．杠杆率a指…", "", ""]))
    check("27a not annotation", not is_tail_annotation_row(["27a 季末", "607,773", "750,116"], 3))
    check("footnote is annotation", is_tail_annotation_row(["1．杠杆率a指不考虑临时豁免…", "", ""], 3))
    check("thousand sep valid", has_valid_thousand_separators("5,240,886"))
    check("thousand sep invalid group", not has_valid_thousand_separators("96,69"))
    check("single numeric ok", is_numeric_data_cell("96.69"))
    check("merged two values", is_merged_numeric_cell("24,938,748 96.69"))
    check("merged not single numeric", not is_numeric_data_cell("24,938,748 96.69"))
    check("merged row still data", row_has_value_data(["本行境内", "24,938,748 96.69", "", ""]))
    check("two dots glued", is_merged_numeric_cell("1.34 1.37"))
    check("two dots no space", is_merged_numeric_cell("96.691.31"))
    check("one dot ok", not is_merged_numeric_cell("5,240,886.12"))
    check("pct one dot ok", not is_merged_numeric_cell("0.84"))
    check("dash plus numeric merged", is_merged_numeric_cell("- 1,234.56"))
    check("dash plus numeric space", is_merged_numeric_cell("— 96.69"))

    from codes.table_engine.split.row_classify import is_entity_scope_header_row

    check("entity scope 本集团", is_entity_scope_header_row(["", "本集团", "", "本集团", ""]))
    check("entity scope 本行", is_entity_scope_header_row(["本行", "本行"]))
    check("entity not body", not is_entity_scope_header_row(["净额", "100", "200"]))

    from codes.table_engine.split.row_classify import (
        is_likely_next_table_header_row,
        trailing_block_is_next_table_header,
    )
    from codes.table_engine.split.structure_split import find_trailing_next_table_header_break

    check("next hdr 本集团", is_likely_next_table_header_row(["本集团", "", "", ""]))
    check("next hdr 本行 cols", is_likely_next_table_header_row(["本行", "本行", ""]))
    check("next hdr unit", is_likely_next_table_header_row(["（人民币百万元）", "2024年", "2023年"]))
    check("narrative not next hdr", not is_likely_next_table_header_row(
        ["其他非利息收入553.41亿元，较上年增加", "", ""]
    ))
    check("section 资产 not next hdr", not is_likely_next_table_header_row(["资产", "", ""]))
    merged_rows = [
        ["投资收益", "21,417", "16,887"],
        ["其他非利息收入总额", "55,341", "36,757"],
        ["本集团", "", ""],
        ["（人民币百万元）", "2024年", "2023年"],
    ]
    check("trailing header break", find_trailing_next_table_header_break(merged_rows) == 2)
    check(
        "trailing block is header",
        trailing_block_is_next_table_header(merged_rows[2:4]),
    )

    from codes.table_engine.geometry.column_anchors import (
        col_index_by_anchor,
        item_column_anchor,
    )

    cols = [(130, 200), (300, 390), (400, 470), (480, 530)]
    check("label band x0", item_column_anchor({"text": "未减值贷款", "x0": 138, "x1": 179}) == 138)
    check("value amount x1", item_column_anchor({"text": "502,471", "x0": 347, "x1": 377}) == 377)
    check(
        "value hdr x1",
        item_column_anchor({"text": "生命周期的影响", "x0": 390.9, "x1": 447.9}) == 447.9,
    )
    check(
        "entity center",
        item_column_anchor({"text": "本行", "x0": 479.5, "x1": 495.6}) == 487.55,
    )
    check(
        "lifecycle col2",
        col_index_by_anchor(390.9, 447.9, "生命周期的影响", cols) == 2,
    )
    check(
        "ecl tail col1",
        col_index_by_anchor(320, 377, "的预期信用损失", cols) == 1,
    )
    check(
        "wrapped label col0",
        col_index_by_anchor(144.6, 247.4, "同业及其他金融机构存放款项和", cols) == 0,
    )


def test_grid_prune_blank_lines() -> None:
    print("--- 空白行列修剪 ---")
    from codes.table_engine.models import BBox, Cell, ColumnGrid, ColumnRange, StructuredTable
    from codes.table_engine.split.grid_prune import prune_blank_rows_columns

    bbox = BBox(0, 0, 10, 10)

    def mk(text: str, r: int, c: int) -> Cell:
        return Cell(text=text, bbox=bbox, row=r, col=c, source_items=[])

    rows = [
        [mk("A", 0, 0), None, mk("B", 0, 2)],
        [None, None, None],
        [mk("1", 2, 0), None, mk("2", 2, 2)],
    ]
    grid = ColumnGrid(
        ranges=[
            ColumnRange(0, 3, 0),
            ColumnRange(3, 6, 1),
            ColumnRange(6, 9, 2),
        ],
        layout_id="generic",
    )
    table = StructuredTable(
        page=1,
        pages=[1],
        y0=0,
        y1=30,
        x0=0,
        x1=9,
        rows=rows,
        grid=grid,
        layout_id="generic",
    )
    pruned = prune_blank_rows_columns(table)
    dense = pruned.iter_rows_dense()
    check("removes blank row", len(dense) == 2, f"rows={len(dense)}")
    check("removes blank col", len(dense[0]) == 2, f"cols={len(dense[0])}")
    check("keeps data", dense[0] == ["A", "B"] and dense[1] == ["1", "2"])

    scope_rows = [
        [mk("标签", 0, 0), None, mk("2024年", 0, 2)],
        [mk("金额", 1, 0), None, mk("100", 1, 2)],
    ]
    scope_table = StructuredTable(
        page=1,
        pages=[1],
        y0=0,
        y1=20,
        x0=0,
        x1=9,
        rows=scope_rows,
        grid=ColumnGrid(
            ranges=[
                ColumnRange(0, 3, 0),
                ColumnRange(3, 6, 1),
                ColumnRange(6, 9, 2),
            ],
            layout_id="generic",
        ),
        layout_id="generic",
        metadata={"scope_source_items": ["orphan-in-col1"]},
    )
    scope_pruned = prune_blank_rows_columns(scope_table, item_lookup={"orphan-in-col1": None})
    scope_dense = scope_pruned.iter_rows_dense()
    check("prune blank col despite scope id", len(scope_dense[0]) == 2, f"cols={len(scope_dense[0])}")

    dash_rows = [
        [mk("–", 0, 0), None],
        [mk("–", 1, 0), None],
    ]
    dash_table = StructuredTable(
        page=1,
        pages=[1],
        y0=0,
        y1=20,
        x0=0,
        x1=6,
        rows=dash_rows,
        grid=ColumnGrid(
            ranges=[ColumnRange(0, 3, 0), ColumnRange(3, 6, 1)],
            layout_id="generic",
        ),
        layout_id="generic",
    )
    dash_pruned = prune_blank_rows_columns(dash_table)
    dash_dense = dash_pruned.iter_rows_dense()
    check("dash column kept", len(dash_dense[0]) == 1, f"cols={len(dash_dense[0])}")

    from codes.table_engine.split.grid_prune import realign_leading_label_column
    from codes.table_engine.models import BBox, Cell, ColumnGrid, ColumnRange, StructuredTable

    unit_bbox = BBox(44, 0, 156, 10)
    label_bbox = BBox(48, 12, 143, 22)
    bond_rows = [
        [Cell("（人民币百万元，百分比除外）", unit_bbox, 0, 0, []), None,
         Cell("面值", BBox(330, 0, 400, 10), 0, 2, []), Cell("到期日", BBox(440, 0, 510, 10), 0, 3, [])],
        [None, Cell("2019年政策性银行金融债券", label_bbox, 1, 1, []),
         Cell("17,440", BBox(330, 12, 380, 22), 1, 2, []), Cell("2029-01-25", BBox(440, 12, 510, 22), 1, 3, [])],
    ]
    bond_table = StructuredTable(
        page=36, pages=[36], y0=0, y1=30, x0=44, x1=510,
        rows=bond_rows,
        grid=ColumnGrid(
            ranges=[ColumnRange(44, 156, 0), ColumnRange(48, 143, 1),
                    ColumnRange(330, 400, 2), ColumnRange(440, 510, 3)],
            layout_id="pillar_disclosure",
        ),
        layout_id="pillar_disclosure",
    )
    aligned = realign_leading_label_column(bond_table)
    aligned_dense = aligned.iter_rows_dense()
    check("unit label same col", aligned_dense[1][0].startswith("2019年"), repr(aligned_dense[1][:2]))

    centered_unit_rows = [
        [None, None, Cell("（除特别注明外，以人民币百万元列示）", BBox(392, 0, 557, 10), 0, 2, []), None, None],
        [None, Cell("本集团", BBox(345, 12, 369, 22), 1, 1, []), None,
         Cell("本行", BBox(479, 12, 496, 22), 1, 3, []), None],
    ]
    centered_unit_table = StructuredTable(
        page=335, pages=[335], y0=0, y1=30, x0=44, x1=557,
        rows=centered_unit_rows,
        grid=ColumnGrid(
            ranges=[
                ColumnRange(55, 307, 0), ColumnRange(307, 370, 1),
                ColumnRange(370, 432, 2), ColumnRange(432, 500, 3),
                ColumnRange(500, 572, 4),
            ],
            layout_id="constraint_grid",
        ),
        layout_id="constraint_grid",
    )
    centered_aligned = realign_leading_label_column(centered_unit_table)
    centered_dense = centered_aligned.iter_rows_dense()
    check(
        "centered unit keeps 本行 column",
        centered_dense[1][3].strip() == "本行",
        str(centered_dense[1]),
    )

    from codes.table_validator.table_classifier import classify_page
    small = [
        ["(%)", "2024年12月31日", "2023年12月31日", "2022年12月31日"],
        ["单一最大客户贷款比例", "4.15", "4.42", "4.50"],
        ["最大十家客户贷款比例", "15.22", "14.87", "14.87"],
    ]
    cr = classify_page(small, 106)
    check("small period table real", cr.is_real_table, cr.reason)

    from codes.table_engine.split.structure_split import (
        _is_reporting_period_body_row,
        _is_reporting_period_header_row,
        _split_at_repeated_header_band,
    )

    header_period = ["", "", "2024年12月31日", ""]
    body_period = ["2024年12月31日", "4,569", "41", "4,610"]
    check("period header centered", _is_reporting_period_header_row(header_period))
    check("period body col0 values", _is_reporting_period_body_row(body_period))
    check("period body not header", not _is_reporting_period_header_row(body_period))
    check("period header not body", not _is_reporting_period_body_row(header_period))
    movement = [
        ["（除特别注明外，以人民币百万元列示）", "", "", ""],
        ["", "软件", "其他", "合计"],
        ["账面价值", "", "", ""],
        ["2024年1月1日", "5,030", "71", "5,101"],
        ["2024年12月31日", "4,569", "41", "4,610"],
        ["", "软件", "其他", "合计"],
        ["2023年1月1日", "16,045", "254", "16,299"],
    ]
    brk = _split_at_repeated_header_band(movement, 5)
    check("split at col header not body period", brk == 5, f"got {brk}")


def test_illegal_numeric_cell_detect() -> None:
    print("--- 非法数值格检测 ---")
    from codes.table_engine.geometry.cell_numeric_repair import is_illegal_value_cell
    from codes.table_engine.geometry.numeric import is_merged_numeric_cell

    check("merged two amounts", is_illegal_value_cell("2,944 1,964"))
    check("dash plus amount", is_illegal_value_cell("– 1,424"))
    check("single amount ok", not is_illegal_value_cell("21,674"))
    check("pct ok", not is_illegal_value_cell("1.75%"))
    check("merged helper", is_merged_numeric_cell("104,923 101,772"))
    check("date not illegal", not is_illegal_value_cell("2024年12月31日"))


def test_item_conservation_cell_text() -> None:
    print("--- item 守恒：同格多片文本 ---")
    from codes.table_engine.geometry.cell_builder import _cell_text_from_items

    items = [
        {"text": "12月31日", "x0": 419, "x1": 454},
        {"text": "12月31日", "x0": 486, "x1": 519},
    ]
    check("dup labels one text", _cell_text_from_items([items[0]]) == "12月31日")
    merged = _cell_text_from_items(items)
    check("two distinct labels joined", merged == "12月31日", merged)
    items2 = [
        {"text": "发放贷款和垫款", "x0": 94, "x1": 151},
        {"text": "金融投资", "x0": 102, "x1": 130},
    ]
    check("two labels preserved", "发放贷款" in _cell_text_from_items(items2) and "金融投资" in _cell_text_from_items(items2))
    # 左右并列：即使「人民币」y0 更小，也不得拼成「人民币 注释」
    note_rmb = [
        {"text": "人民币", "x0": 250, "x1": 300, "y0": 100, "y1": 112},
        {"text": "注释", "x0": 200, "x1": 230, "y0": 106, "y1": 118},
    ]
    joined_nr = _cell_text_from_items(note_rmb)
    check(
        "note before rmb LTR",
        joined_nr.startswith("注释") and joined_nr.index("注释") < joined_nr.index("人民币"),
        joined_nr,
    )
    # 守恒 reconcile 路径：不得用 (y0,x0) 把左侧「注释」排到「人民币」后
    from codes.table_engine.conservation.item_conservation import (
        _text_from_source_items,
        _union_text_preserve,
    )
    from codes.table_engine.models import BBox, SourceItem

    lookup = {
        "rmb": SourceItem(
            text="人民币",
            bbox=BBox(250, 100, 300, 112),
            page=1,
            item_index="rmb",
            y_mid=106.0,
        ),
        "note": SourceItem(
            text="注释",
            bbox=BBox(200, 106, 230, 118),
            page=1,
            item_index="note",
            y_mid=112.0,
        ),
    }
    cons = _text_from_source_items(["rmb", "note"], lookup)
    check("conservation note before rmb", cons == "注释 人民币", cons)
    check(
        "union prefers reading-order incoming",
        _union_text_preserve("人民币 注释", "注释 人民币") == "注释 人民币",
    )


def test_numeric_repair_p314() -> None:
    print("--- P314 数值修复 2024/2023 分列 ---")
    from pathlib import Path

    from codes.table_engine.geometry.cell_numeric_repair import is_illegal_value_cell

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 314)
    result = build_page(page)
    tables = _table_entries(result)
    bal = next(
        (e for e in tables if e.table and any(
            any("存放同业款项" in c for c in r) for r in dense_rows(e.table)
        )),
        None,
    )
    check("P314 balance table", bal is not None)
    if not bal:
        return
    rows = dense_rows(bal.table)
    dep = next((r for r in rows if any("存放同业款项" in c for c in r)), None)
    check("deposit row", dep is not None, str(dep))
    if dep:
        check("2024 separate", any(c.strip() == "2,944" for c in dep))
        check("2023 separate", any(c.strip() == "1,964" for c in dep))
        check("no illegal glue", not any(is_illegal_value_cell(c) for c in dep), str(dep))


def test_p5_km1() -> None:
    print("--- P5 KM1 ---")
    test_disclosure_layout(5, "pillar_disclosure")
    result = test_page_entries(5, max_text=2, max_table=1)
    texts = _text_entries(result)
    tables = _table_entries(result)
    if texts:
        joined = "\n".join(e.text_block.text for e in texts if e.text_block)
        check("narrative has 2.1", "2.1" in joined)
        check("narrative has KM1", "KM1" in joined)
    if tables and tables[0].table:
        flat = _flat_table(tables[0].table)
        check("row1 capital", "3,165,549" in flat)
        check("row4 rwa", "21,854,590" in flat)
        check("no narrative in table", "商业银行资本管理办法" not in flat)
        check("section 可用资本", "可用资本" in flat)


def test_p6() -> None:
    print("--- P6 ---")
    test_disclosure_layout(6, "pillar_disclosure")
    result = test_page_entries(6, max_text=2, max_table=1)
    tables = _table_entries(result)
    if tables and tables[0].table:
        flat = _flat_table(tables[0].table)
        check("has row 13", "13" in flat or find_row_index(tables[0].table, "13") is not None)
        check("not fragmented", len(tables) == 1)


def test_p9_ov1() -> None:
    print("--- P9 OV1 ---")
    test_disclosure_layout(9, "pillar_disclosure")
    result = test_page_entries(9, max_text=2, max_table=1)
    texts = _text_entries(result)
    tables = _table_entries(result)
    if texts:
        joined = "\n".join(e.text_block.text for e in texts if e.text_block)
        check("narrative has OV1", "OV1" in joined)
        check("narrative has 2.2.2", "2.2.2" in joined)
    if tables and tables[0].table:
        flat = _flat_table(tables[0].table)
        check("row1 credit", "19,814,943" in flat)
        check("row29 total", "21,854,590" in flat)
        check("no 下表 in table", "下表列示" not in flat)
        rows = dense_rows(tables[0].table)
        row2 = next(
            (r for r in rows if r and str(r[0]).strip() == "2"),
            None,
        )
        check("row2 exists", row2 is not None)
        if row2:
            label = str(row2[1] if len(row2) > 1 else "").strip()
            check("row2 label in col1", "信用风险" in label and "资产证券化" in label, label[:60])
            check("row2 no wrap orphan rows", not any(
                r and not str(r[0]).strip() and "信用风险（不包括" in str(r[1] if len(r) > 1 else r[0])
                for r in rows
            ))
            wrap_in_serial = any(
                r and "信用风险（不包括" in str(r[0])
                for r in rows
            )
            check("row2 wrap not in serial col", not wrap_in_serial)


def test_p10_cc1() -> None:
    print("--- P10 CC1 ---")
    result = test_page_entries(10, max_text=3, max_table=1)
    tables = _table_entries(result)
    texts = _text_entries(result)
    if tables and tables[0].table:
        t = tables[0].table
        check("layout cc1", t.layout_id == "pillar_cc1")
        flat = _flat_table(t)
        check("row1 amount", "385,621" in flat)
        check("no 3.1 in table", "国家金融监督管理总局" not in flat)
        expected_rows = {
            "1": ("实收资本和资本公积可计入部分", "385,621", "e+g"),
            "2": ("留存收益", "2,718,849", ""),
            "2a": ("盈余公积", "402,196", "h"),
            "2b": ("一般风险准备", "534,151", "i"),
            "2c": ("未分配利润", "1,782,502", "j"),
            "3": ("累计其他综合收益", "65,136", ""),
            "4": ("少数股东资本可计入部分", "3,703", ""),
            "5": ("扣除前的核心一级资本", "3,173,309", ""),
            "6": ("审慎估值调整", "-", ""),
            "7": ("商誉", "2,170", "a-c"),
            "8": ("其他无形资产", "5,009", "b-d"),
            "9": ("依赖未来盈利", "-", ""),
            "10": ("现金流储备", "581", ""),
            "11": ("损失准备缺口", "-", ""),
            "12": ("资产证券化销售利得", "-", ""),
            "13": ("未实现损益", "-", ""),
            "14": ("养老金资产净额", "-", ""),
            "15": ("持有本银行的股票", "-", ""),
            "16": ("核心一级资本", "-", ""),
            "17": ("核心一级资本中应扣除金额", "-", ""),
            "18": ("核心一级资本中应扣除金额", "-", ""),
            "19": ("其他依赖于银行未来盈利", "-", ""),
            "20": ("超过核心一级资本 15%", "-", ""),
            "21": ("其中：对金融机构大额少数资本投资", "-", ""),
        }
        for serial, (label_part, amount, code) in expected_rows.items():
            ri = find_row_index(t, serial)
            check(f"row{serial} present", ri is not None)
            if ri is None:
                continue
            cells = [cell_text(t, ri, ci).strip() for ci in range(4)]
            check(
                f"row{serial} columns",
                cells[0] == serial
                and label_part in cells[1]
                and cells[2] == amount
                and cells[3] == code,
                repr(cells),
            )
        orphan_wraps = {
            "一级资本",
            "应扣除金额",
            "过核心一级资本 15%的应扣除金额",
        }
        check(
            "P10 no orphan label continuations",
            not any(
                not cell_text(t, ri, 0).strip()
                and cell_text(t, ri, 1).strip() in orphan_wraps
                for ri in range(len(t.rows))
            ),
        )
        check("row21 present", find_row_index(t, "21") is not None)
        ri21 = find_row_index(t, "21")
        if ri21 is not None:
            dash_cols = [
                cell_text(t, ri21, i)
                for i in range(len(t.rows[ri21]))
            ]
            check("row21 dash code", "-" in dash_cols, str(dash_cols))
        check("row17 present", find_row_index(t, "17") is not None)
    if texts:
        joined = "\n".join(e.text_block.text for e in texts if e.text_block)
        check("text has 3.1", "3.1" in joined or "非资本债务" in joined)


def test_p11_continuation() -> None:
    print("--- P11 续表 ---")
    result = test_page_entries(11, max_text=2, max_table=1)
    tables = _table_entries(result)
    if tables and tables[0].table:
        t = tables[0].table
        check("layout cc1", t.layout_id == "pillar_cc1")
        check("row 22", find_row_index(t, "22") is not None)
        check("no 232425 glue", "232425" not in _flat_table(t))


def test_p12_cc1_header_body_single_table() -> None:
    print("--- P12 CC1 表头/表体合并 ---")
    result = test_page_entries(12, max_text=2, max_table=1)
    tables = _table_entries(result)
    texts = _text_entries(result)
    if not tables or not tables[0].table:
        return

    table = tables[0].table
    rows = dense_rows(table)
    check("P12 layout cc1", table.layout_id == "pillar_cc1", table.layout_id)
    check(
        "P12 header retained",
        any("人民币百万元" in "".join(row) for row in rows[:5])
        and any("2024年12月31日" in "".join(row) for row in rows[:5]),
        repr(rows[:5]),
    )
    for serial, label_part, amount in (
        ("54", "核心一级资本充足率", "14.48"),
        ("60", "全球系统重要性银行", "1.50"),
        ("65a", "TLAC 非资本债务工具", "14,657"),
        ("71", "可计入二级资本超额损失准备", "307,950"),
    ):
        ri = find_row_index(table, serial)
        check(f"P12 row{serial} present", ri is not None)
        if ri is None:
            continue
        check(
            f"P12 row{serial} columns",
            label_part in cell_text(table, ri, 1)
            and cell_text(table, ri, 2).strip() == amount,
            repr([cell_text(table, ri, ci) for ci in range(4)]),
        )
    duplicate = "\n".join(
        entry.text_block.text
        for entry in texts
        if entry.text_block is not None
    )
    check(
        "P12 no table rows duplicated as text",
        all(token not in duplicate for token in ("60 其中", "65a 对未并表", "117,516")),
        duplicate,
    )


def test_p13_cc2_serial_label_columns() -> None:
    print("--- P13 CC2 序号/标签分列 ---")
    from codes.table_engine.table_access import cell_text, find_row_index

    page = load_page(CACHE, 13)
    t = build_table_from_region(page)
    check("P13 built", t is not None)
    if not t:
        return
    check("P13 cc2 layout", t.layout_id == "pillar_cc2")
    for serial, label_part in (
        ("1", "现金"),
        ("3", "贵金属"),
        ("14", "固定"),
        ("28", "吸收"),
    ):
        ri = find_row_index(t, serial)
        if ri is None:
            check(f"P13 row {serial}", False, "missing")
            continue
        c0 = cell_text(t, ri, 0)
        c1 = cell_text(t, ri, 1)
        check(
            f"P13 row{serial} serial col0",
            c0.strip() == serial,
            repr(c0),
        )
        check(
            f"P13 row{serial} label col1",
            label_part in c1 and c0.strip() == serial,
            f"col0={c0!r} col1={c1!r}",
        )
    ri1 = find_row_index(t, "1")
    if ri1 is not None:
        check(
            "P13 row1 values col_a/col_b",
            cell_text(t, ri1, 2).strip() == "2,571,361"
            and cell_text(t, ri1, 3).strip() == "2,571,361",
            f"cols={[cell_text(t, ri1, j) for j in range(5)]}",
        )
    ri10 = find_row_index(t, "10")
    if ri10 is not None:
        check("P13 row10 serial", cell_text(t, ri10, 0).strip() == "10")
        check(
            "P13 row10 label",
            "摊余成本" in cell_text(t, ri10, 1)
            and "损益的金融资产" not in cell_text(t, ri10, 1),
            cell_text(t, ri10, 1)[:40],
        )
    hdr = t.iter_rows_dense()
    if len(hdr) > 2 and len(hdr[2]) >= 5:
        check(
            "P13 header 代码 in col_c",
            "代码" in str(hdr[2][4] or "")
            and "代码" not in str(hdr[2][3] or ""),
            str(hdr[2][3:5]),
        )
        check(
            "P13 header 监管并表 col_b only",
            "监管并表" in str(hdr[2][3] or "")
            and "代码" not in str(hdr[2][3] or ""),
            str(hdr[2][3]),
        )


def test_p14_cc2_header_code_column() -> None:
    print("--- P14 CC2 表头代码列 ---")
    from codes.table_engine.table_access import cell_text, find_row_index

    page = load_page(CACHE, 14)
    result = build_page(page)
    tables = _table_entries(result)
    check("P14 built", tables and tables[0].table is not None)
    if not tables or not tables[0].table:
        return
    t = tables[0].table
    check("P14 cc2 layout", t.layout_id == "pillar_cc2")
    hdr = t.iter_rows_dense()
    if len(hdr) > 2 and len(hdr[2]) >= 5:
        check(
            "P14 header 代码 in col_c",
            str(hdr[2][4] or "").strip() == "代码",
            str(hdr[2][3:5]),
        )
        check(
            "P14 header 监管并表 col_b",
            "监管并表" in str(hdr[2][3] or "")
            and "代码" not in str(hdr[2][3] or ""),
            str(hdr[2][3]),
        )
    ri30 = find_row_index(t, "30")
    if ri30 is not None:
        check(
            "P14 row30 values",
            cell_text(t, ri30, 2).strip() == "40,388"
            and cell_text(t, ri30, 3).strip() == "40,021",
            f"cols={[cell_text(t, ri30, j) for j in range(4)]}",
        )


def test_p21_cr6_multi_value_columns() -> None:
    print("--- P21 CR6 多数据列分列 ---")
    from codes.table_engine.pipeline import primary_table
    from codes.table_engine.table_access import cell_text, find_row_index

    page = load_page(CACHE, 21)
    result = build_page(page)
    t = primary_table(result)
    check("P21 built", t is not None)
    if not t:
        return
    check("P21 sec1 layout", t.layout_id == "pillar_sec1")
    check("P21 at least 12 data cols", len(t.grid.ranges) >= 12, len(t.grid.ranges))
    ri = None
    for i in range(len(t.rows)):
        if cell_text(t, i, 1).strip() == "[0.00,0.15)":
            ri = i
            break
    if ri is None:
        check("P21 prob row", False, "missing")
        return
    expected = (
        ("1,904,390", 2),
        ("115,315", 3),
        ("33.63%", 4),
        ("1,943,280", 5),
        ("0.09%", 6),
        ("96", 7),
        ("44.36%", 8),
        ("2.41", 9),
        ("707,021", 10),
        ("36%", 11),
        ("796", 12),
    )
    for val, col in expected:
        got = cell_text(t, ri, col).strip()
        check(f"P21 row prob col{col}", got == val, repr(got))
    merged = cell_text(t, ri, 5)
    check("P21 no merged tail in col5", " " not in merged.strip(), merged[:40])
    # col h/i：平均有效期限（年）与 风险加权资产 2 表头不得跨列粘连
    h_maturity = ""
    i_assets = ""
    for ri_h in range(min(8, len(t.rows))):
        h_maturity = (h_maturity + " " + cell_text(t, ri_h, 9)).strip()
        i_assets = (i_assets + " " + cell_text(t, ri_h, 10)).strip()
    check(
        "P21 col h maturity header",
        "效期限" in h_maturity
        and "年" in h_maturity
        and "风险加权" not in h_maturity,
        repr(h_maturity[:60]),
    )
    check(
        "P21 col i rwa header",
        "风险加权" in i_assets and "产" in i_assets and "效期限" not in i_assets,
        repr(i_assets[:60]),
    )
    # CR6 双左列：暴露类别 | 违约概率区间
    check(
        "P21 layout category+pd_range",
        len(t.grid.ranges) >= 2
        and getattr(t.grid.ranges[0], "role", "") == "category"
        and getattr(t.grid.ranges[1], "role", "") == "pd_range",
        [getattr(cr, "role", "") for cr in t.grid.ranges[:3]],
    )
    ri_fin = None
    for i in range(len(t.rows)):
        if cell_text(t, i, 1).strip() == "[0.50,0.75)":
            ri_fin = i
            break
    if ri_fin is not None:
        check(
            "P21 category col0 金融",
            cell_text(t, ri_fin, 0).strip() == "",
            repr(cell_text(t, ri_fin, 0)),
        )
        check(
            "P21 pd col1 no category glue",
            "金融" not in cell_text(t, ri_fin, 1),
            repr(cell_text(t, ri_fin, 1)),
        )
    ri_first = None
    for i in range(len(t.rows)):
        if cell_text(t, i, 1).strip() == "[0.00,0.15)":
            ri_first = i
            break
    if ri_first is not None:
        check(
            "P21 category 金融机构 rollup",
            cell_text(t, ri_first, 0).strip() == "金融机构",
            repr(cell_text(t, ri_first, 0)),
        )
    ri_corp = None
    for i in range(len(t.rows)):
        if "公司" in cell_text(t, i, 0) and cell_text(t, i, 1).strip().startswith("[0.75"):
            ri_corp = i
            break
    if ri_corp is not None:
        check(
            "P21 category 公司 3",
            "公司" in cell_text(t, ri_corp, 0),
            repr(cell_text(t, ri_corp, 0)),
        )
    # entry 表：小计/100（违约）须在 pd_range 列（col1），不得被 grid_prune 左移
    from codes.table_engine.pipeline import build_page as _build_page

    entry_result = _build_page(load_page(CACHE, 21))
    et = [e.table for e in entry_result.entries if e.kind == "table" and e.table][0]
    ri_entry = None
    for i in range(len(et.rows)):
        if cell_text(et, i, 1).strip() == "[0.00,0.15)":
            ri_entry = i
            break
    if ri_entry is not None:
        check(
            "P21 entry category 金融机构 col0",
            cell_text(et, ri_entry, 0).strip() == "金融机构",
            repr(cell_text(et, ri_entry, 0)),
        )
        check(
            "P21 entry values split col2/col3",
            cell_text(et, ri_entry, 2).strip() == "1,904,390"
            and cell_text(et, ri_entry, 3).strip() == "115,315",
            [cell_text(et, ri_entry, j) for j in range(4)],
        )
    for label in ("100（违约）", "小计"):
        found_col1 = any(cell_text(et, i, 1).strip() == label for i in range(len(et.rows)))
        found_col0 = any(cell_text(et, i, 0).strip() == label for i in range(len(et.rows)))
        check(f"P21 entry {label} in pd col", found_col1 and not found_col0, f"col0={found_col0} col1={found_col1}")
    from codes.table_engine.geometry.cell_numeric_repair import is_illegal_value_cell

    ri_corp_sub = None
    for i in range(len(t.rows)):
        if cell_text(t, i, 1).strip() == "小计" and "11,787,150" in cell_text(t, i, 2):
            ri_corp_sub = i
            break
    if ri_corp_sub is not None:
        check(
            "P21 corp subtotal expected loss",
            cell_text(t, ri_corp_sub, 12).strip() == "209,952",
            cell_text(t, ri_corp_sub, 12),
        )
        check(
            "P21 corp subtotal impairment",
            cell_text(t, ri_corp_sub, 13).strip() == "498,673",
            cell_text(t, ri_corp_sub, 13),
        )
        check(
            "P21 corp subtotal no glued amounts",
            not is_illegal_value_cell(cell_text(t, ri_corp_sub, 12))
            and not is_illegal_value_cell(cell_text(t, ri_corp_sub, 13)),
            [cell_text(t, ri_corp_sub, j) for j in range(12, 14)],
        )
    check(
        "P21 no illegal merged value cells",
        not any(
            is_illegal_value_cell(c)
            for r in dense_rows(t)
            for ci, c in enumerate(r)
            if c and ci >= 2
        ),
    )


def test_p33_irrbb_footer_no_split() -> None:
    print("--- P33 IRRBB1 表尾期间/一级资本不拆表 ---")
    result = build_page(load_page(CACHE, 33))
    tables = [e.table for e in result.entries if e.kind == "table" and e.table]
    texts = [e.text_block.text for e in result.entries if e.kind == "text" and e.text_block]
    check("P33 single table entry", len(tables) == 1, str(len(tables)))
    if not tables:
        return
    t = tables[0]
    rows = dense_rows(t)
    check("P33 at least 12 rows", len(rows) >= 12, str(len(rows)))
    ri_max = next((i for i, r in enumerate(rows) if r[0].strip() == "最大值"), None)
    if ri_max is not None:
        check(
            "P33 max row separate",
            rows[ri_max][0].strip() == "最大值"
            and rows[ri_max][1].strip() == "(454,022)"
            and rows[ri_max][2].strip() == "(453,152)",
            rows[ri_max],
        )
        if ri_max + 1 < len(rows):
            check(
                "P33 period footer row",
                rows[ri_max + 1][0].strip() == "期间"
                and "2024" in rows[ri_max + 1][2],
                rows[ri_max + 1],
            )
        if ri_max + 2 < len(rows):
            check(
                "P33 tier1 capital row",
                "一级资本" in rows[ri_max + 2][0]
                and rows[ri_max + 2][2].strip() == "3,081,596",
                rows[ri_max + 2],
            )
    check(
        "P33 no orphan max values text",
        not any("(454,022)" in tx and "最大值" in tx for tx in texts),
    )
    check(
        "P33 no duplicate tier1 table",
        not any("一级资本" in tx and "3,081,596" in tx for tx in texts),
    )


def test_p35_dsib_indicator_columns() -> None:
    print("--- P35 DSIB1 一级/二级指标按坐标分列 ---")
    result = build_page(load_page(CACHE, 35))
    t = [e.table for e in result.entries if e.kind == "table" and e.table][0]
    check("P35 dsib layout", t.layout_id == "pillar_dsib", t.layout_id)
    check("P35 three cols after prune", t.grid.col_count == 3, str(t.grid.col_count))
    rows = dense_rows(t)
    check(
        "P35 金融机构间资产 col1",
        rows[3][1].strip() == "金融机构间资产",
        rows[3],
    )
    check(
        "P35 关联度|负债分列",
        rows[4][0].strip() == "关联度" and rows[4][1].strip() == "金融机构间负债",
        rows[4],
    )
    check(
        "P35 托管资产 col1",
        rows[7][1].strip() == "托管资产",
        rows[7],
    )


def test_p38_liq1_two_value_columns() -> None:
    print("--- P38 LIQ1 折算前/后分列 ---")
    from codes.table_engine.geometry.cell_numeric_repair import is_illegal_value_cell
    from codes.table_engine.table_access import cell_text, find_row_index

    page = load_page(CACHE, 38)
    t = build_table_from_region(page)
    check("P38 built", t is not None)
    if not t:
        return
    check("P38 disclosure layout", t.layout_id == "pillar_disclosure")
    check("P38 four cols", len(t.grid.ranges) == 4, len(t.grid.ranges))
    rows = dense_rows(t)
    check(
        "P38 no merged two amounts in one cell",
        not any(is_illegal_value_cell(c) for r in rows for c in r if c),
        [c for r in rows[:12] for c in r if " " in str(c) and "," in str(c)],
    )
    ri2 = find_row_index(t, "2")
    if ri2 is not None:
        check("P38 row2 serial", cell_text(t, ri2, 0).strip() == "2")
        check("P38 row2 label", "零售存款" in cell_text(t, ri2, 1))
        check(
            "P38 row2 before/after split",
            cell_text(t, ri2, 2).strip() == "15,348,337"
            and cell_text(t, ri2, 3).strip() == "1,383,705",
            [cell_text(t, ri2, j) for j in range(5)],
        )
    check(
        "P38 section 现金流出 separate",
        any(r[1].strip() == "现金流出" or r[0].strip() == "现金流出" for r in rows[:8]),
        rows[:8],
    )


def test_p27_sec1_header_rows_separate() -> None:
    print("--- P27 SEC1 表头各行独立 ---")
    from codes.table_engine.table_access import cell_text

    page = load_page(CACHE, 27)
    t = build_table_from_region(page)
    check("P27 built", t is not None)
    if not t:
        return
    check("P27 sec1 layout", t.layout_id == "pillar_sec1")
    rows = dense_rows(t)
    check("P27 at least 5 header rows", len(rows) >= 6, len(rows))
    if len(rows) < 6:
        return
    check(
        "P27 role banners separate",
        "银行作为发起机构" in rows[2][4]
        and "其中，满" not in rows[2][4],
        rows[2],
    )
    check(
        "P27 其中满 own row",
        rows[3].count("其中，满") >= 2
        and all("银行作为" not in c for c in rows[3]),
        rows[3],
    )
    check(
        "P27 STC header not glued",
        rows[4].count("足STC标") >= 2
        and all("足STC标准" not in c for c in rows[4]),
        rows[4],
    )
    check(
        "P27 准的 own row",
        rows[5].count("准的") >= 2
        and all("足STC" not in c for c in rows[5]),
        rows[5],
    )
    ri1 = find_row_index(t, "1")
    if ri1 is not None:
        check("P27 row1 label", "零售" in cell_text(t, ri1, 1))
        check("P27 row1 value", cell_text(t, ri1, 2).strip() == "7,195")


def test_p25_ccrf_serial_label_wrap() -> None:
    print("--- P25 CCR1 序号/标签折行 ---")
    from codes.table_engine.table_access import cell_text, find_row_index

    page = load_page(CACHE, 25)
    t = build_table_from_region(page)
    check("P25 built", t is not None)
    if not t:
        return
    check("P25 ccrf layout", t.layout_id == "pillar_ccrf")
    for serial, label_part in (
        ("1", "标准法"),
        ("2", "现期暴露法"),
        ("3", "证券融资交易"),
        ("4", "合计"),
    ):
        ri = find_row_index(t, serial)
        if ri is None:
            check(f"P25 row {serial}", False, "missing")
            continue
        c0 = cell_text(t, ri, 0)
        c1 = cell_text(t, ri, 1)
        check(f"P25 row{serial} serial col0", c0.strip() == serial, repr(c0))
        check(
            f"P25 row{serial} label col1",
            label_part in c1 and c0.strip() == serial,
            f"col0={c0!r} col1={c1!r}",
        )
    ri1 = find_row_index(t, "1")
    if ri1 is not None:
        check(
            "P25 row1 wrapped label",
            "衍生工具" in cell_text(t, ri1, 1),
            cell_text(t, ri1, 1),
        )
        check(
            "P25 row1 values",
            cell_text(t, ri1, 2).strip() == "82,519"
            and cell_text(t, ri1, 7).strip() == "84,428",
            [cell_text(t, ri1, j) for j in range(8)],
        )
    hdr = dense_rows(t)
    if len(hdr) >= 7:
        check(
            "P25 header RC in col_a",
            cell_text(t, 5, 2).strip() == "(RC)",
            cell_text(t, 5, 2),
        )
        check(
            "P25 header PFE in col_b",
            cell_text(t, 6, 3).strip() == "(PFE)",
            cell_text(t, 6, 3),
        )
        check(
            "P25 header 重置成本 in col_a",
            cell_text(t, 3, 2).strip() == "重置成本",
            cell_text(t, 3, 2),
        )
        check(
            "P25 header 潜在风险 in col_b",
            cell_text(t, 2, 3).strip() == "潜在风险",
            cell_text(t, 2, 3),
        )
        check(
            "P25 header no cross-col merge b/c",
            "暴露" not in cell_text(t, 4, 4)
            and cell_text(t, 4, 3).strip() == "暴露",
            [cell_text(t, 4, j) for j in range(5)],
        )


def test_role_driven_serial_and_wrap_merge() -> None:
    print("--- 通用 row_no 序号保护与标签折行 ---")
    from codes.table_engine.geometry.cell_builder import _assign_item_to_columns
    from codes.table_engine.geometry.data_column_assign import (
        reconcile_col_items_by_anchor,
    )
    from codes.table_engine.geometry.row_refiner import (
        LayoutAnchors,
        _can_merge_label_with_numbered,
    )

    ranges = [(0.0, 40.0), (40.0, 70.0), (70.0, 180.0), (180.0, 240.0)]
    serial = {"text": "2", "x0": 48.0, "x1": 60.0}
    value = {"text": "3", "x0": 205.0, "x1": 214.0}
    assigned = reconcile_col_items_by_anchor(
        [[], [serial], [], [value]],
        ranges,
        layout_id="future_serial_layout",
        value_cols=[3],
        serial_col=1,
    )
    check("row_no role protects nonzero serial col", serial in assigned[1])
    check("short value outside row_no remains value", value in assigned[3])

    unprotected = reconcile_col_items_by_anchor(
        [[], [serial], [], []],
        ranges,
        layout_id="future_serial_layout",
        value_cols=[3],
    )
    check("no row_no role means no serial exemption", serial in unprotected[3])

    typed_ranges = [(38.0, 86.0), (86.0, 206.0), (206.0, 270.0)]
    typed_buckets = [[] for _ in typed_ranges]
    _assign_item_to_columns(
        {"text": "32", "x0": 53.0, "x1": 64.0},
        typed_ranges,
        typed_buckets,
        len(typed_ranges),
        "constraint_grid",
        serial_col=0,
        label_col=1,
    )
    _assign_item_to_columns(
        {"text": "表外项目", "x0": 86.3, "x1": 128.0},
        typed_ranges,
        typed_buckets,
        len(typed_ranges),
        "constraint_grid",
        serial_col=0,
        label_col=1,
    )
    check("typed row_no keeps serial only", [x["text"] for x in typed_buckets[0]] == ["32"])
    check("typed label column keeps text", [x["text"] for x in typed_buckets[1]] == ["表外项目"])

    layout = LayoutAnchors(
        row_num_x_max=84.0,
        label_x_min=84.0,
        label_x_max=170.0,
        value_x_min=200.0,
    )
    numbered = {
        "items": [
            {"text": "7", "x0": 70.0, "x1": 78.0, "y0": 108.0, "y1": 122.0},
            {"text": "1,234", "x0": 210.0, "x1": 238.0, "y0": 108.0, "y1": 122.0},
        ]
    }
    overlapping_label = {
        "items": [
            {"text": "通用风险计量方法", "x0": 90.0, "x1": 160.0, "y0": 99.0, "y1": 111.0},
        ]
    }
    section_label = {
        "items": [
            {"text": "通用风险计量方法", "x0": 90.0, "x1": 160.0, "y0": 75.0, "y1": 87.0},
        ]
    }
    check(
        "overlapping label joins numbered row",
        _can_merge_label_with_numbered(overlapping_label, numbered, layout),
    )
    check(
        "separate section label stays independent",
        not _can_merge_label_with_numbered(section_label, numbered, layout),
    )


def test_p22_cr6_footer_note_not_in_last_cell() -> None:
    print("--- P22 CR6 表尾脚注不混入最后数值格 ---")
    from codes.table_engine.pipeline import primary_table
    from codes.table_engine.split.boundary_overlap import (
        row_is_address_column_wrap_fragment,
    )
    from codes.table_engine.table_access import cell_text

    result = build_page(load_page(CACHE, 22))
    t = primary_table(result)
    check("P22 built", t is not None)
    if not t:
        return
    last_row = t.rows[-1]
    last_text = cell_text(t, len(t.rows) - 1, len(last_row) - 1)
    check(
        "P22 last numeric cell clean",
        "501,689" in last_text and "表内资产余额" not in last_text,
        repr(last_text),
    )
    note_text = "\n".join(
        e.text_block.text
        for e in result.entries
        if e.kind == "text" and e.text_block is not None
    )
    check("P22 footer note remains text", "表内资产余额" in note_text, repr(note_text[-100:]))
    check(
        "footnote is not address continuation",
        not row_is_address_column_wrap_fragment(
            ["", "", "1．表内资产余额和表外转换前资产均未考虑风险缓释。", ""],
        ),
    )
    check(
        "real address continuation remains supported",
        row_is_address_column_wrap_fragment(
            ["", "", "北京市西城区金融大街25号", ""],
        ),
    )


def test_p19_cr5_serial_label_columns() -> None:
    print("--- P19 CR5-2 序号/风险权重分列 ---")
    page = load_page(CACHE, 19)
    result = build_page(page)
    tables = _table_entries(result)
    check("P19 built", tables and tables[0].table is not None)
    if not tables or not tables[0].table:
        return
    t = tables[0].table
    check("P19 six columns", len(t.grid.ranges) >= 6, f"got {len(t.grid.ranges)}")
    for serial, label_part in (
        ("1", "低于 40%"),
        ("2", "40-70%"),
        ("5", "90-100%"),
        ("10", "1250%"),
        ("11", "合计"),
    ):
        ri = find_row_index(t, serial)
        if ri is None:
            check(f"P19 row {serial}", False, "missing")
            continue
        c0 = cell_text(t, ri, 0)
        c1 = cell_text(t, ri, 1)
        check(
            f"P19 row{serial} serial col0",
            c0.strip() == serial,
            repr(c0),
        )
        check(
            f"P19 row{serial} label col1",
            label_part in c1 and serial not in c1.replace(label_part, "").strip(),
            f"col0={c0!r} col1={c1!r}",
        )
    for ri in range(len(t.rows)):
        c0 = cell_text(t, ri, 0).strip()
        if c0.isdigit() or (len(c0) <= 3 and c0[:1].isdigit()):
            check(f"P19 row{ri} serial only col0", " " not in c0, repr(c0))


def test_p41_multi_region_merge() -> None:
    print("--- P41 多 region 续表 ---")
    result = test_page_entries(41, max_text=0, max_table=1)
    tables = _table_entries(result)
    check("P41 one table", len(tables) == 1, f"got {len(tables)}")
    if not tables or not tables[0].table:
        return
    t = tables[0].table
    rows = dense_rows(t)
    check("P41 rows 18-28", find_row_index(t, "18") is not None and find_row_index(t, "28") is not None)
    check("P41 header 序号", any("序号" in c for row in rows[:4] for c in row))
    check("P41 header a/b", any(c == "a" for row in rows[:3] for c in row))
    check("P41 row20 merged", any("20" in row[0] and "1,037,820" in _flat_table(t) for row in rows if row))
    check("P41 no orphan 序号 text", not any(e.text_block.text.strip() == "序号" for e in _text_entries(result)))


def test_p28_fee_income() -> None:
    print("--- P28 手续费及佣金 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 28)
    result = build_page(page)
    tables = _table_entries(result)
    check("P28 >= 2 tables", len(tables) >= 2, f"got {len(tables)}")
    fee = max(tables, key=lambda e: len(dense_rows(e.table)) if e.table else 0)
    if fee.table:
        rows = dense_rows(fee.table)
        hdr = rows[0] if rows else []
        check("header has 2022", any("2022" in c for c in hdr))
        change_col = next((c for c in hdr if "变动" in c), "")
        check("change col separate", change_col and "2022" not in change_col, repr(change_col))
        check("row 2022 value", rows[1][-1] == "130,830" if len(rows) > 1 else False)


def test_p29_non_interest_income_split() -> None:
    print("--- P29 其他非利息收入 / 业务及管理费 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 29)
    result = build_page(page)
    tables = _table_entries(result)
    texts = _text_entries(result)
    check("P29 two tables", len(tables) == 2, f"got {len(tables)}")
    check("P29 three text blocks", len(texts) == 3, f"got {len(texts)}")
    if len(tables) < 2:
        return
    t1 = dense_rows(tables[0].table)
    t2 = dense_rows(tables[1].table)
    check("table1 header years", any("2024" in c for c in t1[0]))
    check("table1 investment income", any("投资收益" in r[0] for r in t1))
    check("table1 no narrative", not any("553.41" in r[0] for r in t1))
    check("table2 starts with unit row", "人民币" in t2[0][0] or "百万元" in t2[0][0])
    check("table2 employee cost", any("员工成本" in r[0] for r in t2))
    check("table2 no section narrative", not any("553.41" in r[0] for r in t2))
    check("table2 no 下表列出", not any("下表列出" in r[0] for r in t2))
    if texts:
        mid = next((e for e in texts if e.text_block and "553.41" in e.text_block.text), None)
        check("mid narrative in text", mid is not None)
        check("tables ordered by y", tables[0].y0 < mid.y0 < tables[1].y0 if mid else False)
    _check_page_source_conservation(page, result.entries)


def test_p106_loan_concentration() -> None:
    print("--- P106 贷款集中度 + 十大借款人 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 106)
    result = build_page(page)
    tables = _table_entries(result)
    texts = _text_entries(result)
    check("P106 two tables", len(tables) == 2, f"got {len(tables)}")
    if len(tables) < 2:
        return
    t_small = dense_rows(tables[0].table)
    t_top10 = dense_rows(tables[1].table)
    check("concentration 4.15", any("4.15" in c for r in t_small for c in r))
    check("concentration row label", any("单一最大" in r[0] for r in t_small))
    check("no concentration rows in text", not any(
        e.text_block and "4.15" in e.text_block.text and "单一最大" in e.text_block.text
        for e in texts
    ))
    check("top10 customer A", any("客户A" in r[0] for r in t_top10))
    check("top10 total", any("总额" in r[0] for r in t_top10))
    check("top10 no market risk narrative", not any("市场风险管理" in r[0] for r in t_top10))
    pre = next((e for e in texts if e.text_block and "贷款集中度" in e.text_block.text), None)
    check("preamble has 贷款集中度", pre is not None)
    caps = [e for e in texts if e.text_block and "下表列出" in e.text_block.text and "十大" in e.text_block.text]
    check("single caption for top10", len(caps) == 1, f"got {len(caps)}")
    check("top10 reporting date in table", any(
        "2024" in c and "12月31日" in c for r in t_top10[:4] for c in r
    ))
    body_rows = [r for r in t_top10 if r and r[0] and str(r[0]).startswith("客户")]
    check("top10 body rows", len(body_rows) >= 5)
    if len(body_rows) >= 5:
        check(
            "top10 industry in col1",
            sum(1 for r in body_rows if len(r) > 1 and r[1] and "业" in r[1]) >= 5,
        )
        check(
            "top10 col2 not industry",
            not any(len(r) > 2 and r[2] and "业" in r[2] and "客户" not in r[2] for r in body_rows),
        )
    check("table1 no top10 caption", not any("十大单一借款人" in r[0] for r in t_small))
    if pre and tables:
        check("preamble before tables", pre.y0 < tables[0].y0)
    _check_page_source_conservation(page, result.entries)


def test_p268_deposits_single_main_table() -> None:
    print("--- P268 吸收存款主表不拆 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 268)
    result = build_page(page)
    tables = _table_entries(result)
    check("P268 two tables", len(tables) == 2, f"got {len(tables)}")
    if len(tables) < 2:
        return
    main = dense_rows(tables[0].table)
    sub = dense_rows(tables[1].table)
    check("main has 活期", any("活期存款" in c for r in main for c in r))
    check("main has 定期", any("定期存款" in c for r in main for c in r))
    check("main total 28,713,870", any("28,713,870" in c for r in main for c in r))
    check("main not split at 活期小计 only", not (
        any("12,313,326" in c for r in main for c in r)
        and not any("15,925,246" in c for r in main for c in r)
    ))
    check("sub has 保证金", any("保证金" in c for r in sub for c in r))
    _check_page_source_conservation(page, result.entries)


def _row_text(row: List[str]) -> str:
    return " ".join(c for c in row if c)


def _table_last_nonempty_row(rows: List[List[str]]) -> str:
    for row in reversed(rows):
        t = _row_text(row)
        if t:
            return t
    return ""


def _table_first_nonempty_rows(rows: List[List[str]], n: int = 3) -> List[str]:
    out: List[str] = []
    for row in rows:
        t = _row_text(row)
        if t:
            out.append(t)
        if len(out) >= n:
            break
    return out


def test_p271_employee_benefits_period_headers() -> None:
    print("--- P271 应付职工薪酬报告期归位 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 271)
    result = build_page(page)
    tables = _table_entries(result)
    texts = _text_entries(result)
    check("P271 four tables", len(tables) == 4, f"got {len(tables)}")
    if len(tables) < 4:
        return
    t1 = dense_rows(tables[0].table)
    t2 = dense_rows(tables[1].table)
    t3 = dense_rows(tables[2].table)
    t4 = dense_rows(tables[3].table)
    check("t1 ends at 2024 total", "1,196" in _table_last_nonempty_row(t1))
    check("t1 no trailing 2023", "2023年" not in _table_last_nonempty_row(t1))
    head2 = _table_first_nonempty_rows(t2, 4)
    check("t2 starts 2023", any("2023年" in h for h in head2[:2]))
    check("t2 no trailing entity", "本行" not in _table_last_nonempty_row(t2))
    check("t2 no trailing 2024", "2024年" not in _table_last_nonempty_row(t2))
    head3 = _table_first_nonempty_rows(t3, 4)
    check("t3 starts 本行", any("本行" in h for h in head3[:2]))
    check("t3 has 2024 header", any("2024年" in h for h in head3[:3]))
    check("t3 no trailing 2023", "2023年" not in _table_last_nonempty_row(t3))
    head4 = _table_first_nonempty_rows(t4, 3)
    check("t4 starts 2023", any("2023年" in h for h in head4[:2]))
    foot = next(
        (e for e in texts if e.text_block and "没收的供款" in e.text_block.text),
        None,
    )
    check("footnote in text", foot is not None)
    _check_page_source_conservation(page, result.entries)


def test_p275_debt_securities_columns() -> None:
    print("--- P275 已发行债务证券 标签/注释分列 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 275)
    result = build_page(page)
    tables = _table_entries(result)
    check("P275 three tables", len(tables) == 3, f"got {len(tables)}")
    debt = next(
        (e for e in tables if e.table and any(
            "已发行同业存单" in c for r in dense_rows(e.table) for c in r
        )),
        None,
    )
    check("debt securities table", debt is not None)
    if not debt or not debt.table:
        return
    rows = dense_rows(debt.table)
    check("6 columns", debt.table.grid.col_count == 6, str(debt.table.grid.col_count))
    hdr = next((r for r in rows if any(c == "注释" for c in r)), None)
    body = next((r for r in rows if any("已发行同业存单" in c for c in r)), None)
    check("comment header col1", hdr and hdr[1] == "注释", str(hdr))
    check("label col0", body and body[0].startswith("已发行同业存单"), str(body[:3] if body else None))
    check("note marker col1", body and body[1] == "(1)", str(body[:3] if body else None))
    check("no footnote in debt table", not any(
        "重新计量包括" in c for r in rows for c in r
    ))
    check("no orphan 2023 closing in debt", not any(
        r[0] == "2023年12月31日" and "28,180" in "".join(r) for r in rows
    ))
    _check_page_source_conservation(page, result.entries)


def test_p301_segment_period_header_split() -> None:
    print("--- P301 经营分部居中报告期拆表 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 301)
    result = build_page(page)
    tables = _table_entries(result)
    check("P301 two tables", len(tables) == 2, f"got {len(tables)}")
    if len(tables) < 2:
        return
    t1 = dense_rows(tables[0].table)
    t2 = dense_rows(tables[1].table)
    check("t1 has capex", any("资本性支出" in c for r in t1 for c in r))
    check("t1 no balance sheet", not any("分部资产" in c for r in t1 for c in r))
    check("t2 starts 2024-12-31", any("2024年12月31日" in c for r in t2[:2] for c in r))
    check("t2 has segment assets", any("分部资产" in c for r in t2 for c in r))
    check("t2 has credit commitment", any("表外信贷承诺" in c for r in t2 for c in r))
    _check_page_source_conservation(page, result.entries)


def test_p312_related_party_header_rows() -> None:
    print("--- P312 关联方双层表头不纵并 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 312)
    result = build_page(page)
    tables = _table_entries(result)
    txn = next(
        (e for e in tables if e.table and any(
            any("利息收入" in c for c in r) and any("21,674" in c for c in r)
            for r in dense_rows(e.table)
        )),
        None,
    )
    check("P312 transaction table", txn is not None)
    if not txn:
        return
    rows = dense_rows(txn.table)
    year_row = next((r for r in rows if "2024年" in r and "2023年" in r), None)
    ratio_row = next((r for r in rows if r.count("占同类交易") == 2), None)
    col_row = next(
        (r for r in rows if "注释" in r and "交易金额" in r and "的比例" in r),
        None,
    )
    check("year row separate", year_row is not None, str(year_row))
    check("ratio row separate", ratio_row is not None, str(ratio_row))
    check("year row no ratio", year_row and "占同类交易" not in year_row, str(year_row))
    check("ratio row no year", ratio_row and "2024年" not in ratio_row and "2023年" not in ratio_row, str(ratio_row))
    check("column header row", col_row is not None, str(col_row))
    if col_row:
        note_i = next((i for i, c in enumerate(col_row) if c.strip() == "注释"), None)
        amt_i = next((i for i, c in enumerate(col_row) if c.strip() == "交易金额"), None)
        check("note before amount", note_i is not None and amt_i is not None and note_i < amt_i, str(col_row))
        check("note not glued to amount", not any(
            "注释" in c and "交易金额" in c for c in col_row
        ), str(col_row))
    mgmt = next((r for r in rows if any("652" in c for c in r) and any("业务及管理费" in c for c in r)), None)
    if mgmt:
        check("footnote separate", any(c.strip() == "(i)" for c in mgmt), str(mgmt))
        check("amount separate", any(c.strip() == "652" for c in mgmt), str(mgmt))
        check("no glued footnote amount", not any("652" in c and "(i)" in c for c in mgmt), str(mgmt))
    check("txn table ends at mgmt", rows[-1] == mgmt or any(
        "业务及管理费" in c for c in rows[-1]
    ), str(rows[-1]))
    check("no balance labels in txn tail", not any(
        "存放同业款项" in c for c in rows[-1]
    ), str(rows[-1]))
    bal = next(
        (e for e in tables if e.table and any(
            any("存放同业款项" in c for c in r) and any("37,494" in c for c in r)
            for r in dense_rows(e.table)
        )),
        None,
    )
    if bal:
        brow = dense_rows(bal.table)
        check("balance table has section title", any(
            "资产负债表日重大交易的余额" in c for r in brow[:2] for c in r
        ), str(brow[:3]))
        check("balance table no txn glue row", not any(
            "利息收入" in c and "存放同业款项" in c for r in brow for c in r
        ), str(brow[0]))
    if year_row and ratio_row and col_row:
        yi = rows.index(year_row)
        ri = rows.index(ratio_row)
        ci = rows.index(col_row)
        check("header row order", yi < ri < ci, f"{yi} {ri} {ci}")
    _check_page_source_conservation(page, result.entries)


def test_p314_subsidiary_balance_columns() -> None:
    print("--- P314 子公司往来 2024/2023 分列 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 314)
    result = build_page(page)
    tables = _table_entries(result)
    bal = next(
        (e for e in tables if e.table and any(
            any("存放同业款项" in c for c in r) and any("2,944" in c for c in r)
            for r in dense_rows(e.table)
        )),
        None,
    )
    check("P314 balance table", bal is not None)
    if not bal:
        return
    rows = dense_rows(bal.table)
    dep = next((r for r in rows if any("存放同业款项" in c for c in r)), None)
    check("deposit row", dep is not None, str(dep))
    if dep:
        check("2024 value separate", any(c.strip() == "2,944" for c in dep), str(dep))
        check("2023 value separate", any(c.strip() == "1,964" for c in dep), str(dep))
        check("no glued year values", not any(
            "2,944" in c and "1,964" in c for c in dep
        ), str(dep))
    lend = next((r for r in rows if any("拆出资金" in c for c in r)), None)
    if lend:
        check("lend 2024", any(c.strip() == "104,923" for c in lend), str(lend))
        check("lend 2023", any(c.strip() == "101,772" for c in lend), str(lend))
    hdr = rows[:8]
    date_row = next((r for r in hdr if r.count("12月31日") >= 2), None)
    year_row = next((r for r in hdr if "2024年" in r and "2023年" in r), None)
    check("two month-day headers", date_row is not None, str(hdr))
    if date_row and year_row:
        yi = [i for i, c in enumerate(year_row) if "2024年" in c]
        yj = [i for i, c in enumerate(year_row) if "2023年" in c]
        di = [i for i, c in enumerate(date_row) if "12月31日" in c]
        check("month-day under 2024", yi and di and yi[0] == di[0], f"year={year_row} date={date_row}")
        check("month-day under 2023", yj and len(di) >= 2 and yj[0] == di[1], f"year={year_row} date={date_row}")
    _check_page_source_conservation(page, result.entries)


def test_p320_entity_period_header_rows() -> None:
    print("--- P320 本集团/本行 三层表头不纵并 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 320)
    result = build_page(page)
    tables = _table_entries(result)
    bal = next(
        (e for e in tables if e.table and any(
            any("存放中央银行款项" in c for c in r) and any("2,524,670" in c for c in r)
            for r in dense_rows(e.table)
        )),
        None,
    )
    check("P320 credit exposure table", bal is not None)
    if not bal:
        return
    rows = dense_rows(bal.table)
    entity_row = next((r for r in rows if "本集团" in r and "本行" in r), None)
    year_row = next((r for r in rows if r.count("2024年") >= 2 and r.count("2023年") >= 2), None)
    date_row = next((r for r in rows if r.count("12月31日") >= 4), None)
    check("entity scope row", entity_row is not None, str(entity_row))
    check("year row separate", year_row is not None, str(year_row))
    check("month-day row separate", date_row is not None, str(date_row))
    check("year row no month-day glue", year_row and all(
        "12月31日" not in c for c in year_row
    ), str(year_row))
    check("date row no year glue", date_row and all(
        "2024年" not in c and "2023年" not in c for c in date_row
    ), str(date_row))
    if entity_row and year_row and date_row:
        ei = rows.index(entity_row)
        yi = rows.index(year_row)
        di = rows.index(date_row)
        check("header top-to-bottom order", ei < yi < di, f"{ei} {yi} {di}")
        y24 = [i for i, c in enumerate(year_row) if c.strip() == "2024年"]
        y23 = [i for i, c in enumerate(year_row) if c.strip() == "2023年"]
        dcols = [i for i, c in enumerate(date_row) if "12月31日" in c]
        check("month-day under 2024 cols", len(y24) == 2 and len(dcols) == 4 and y24[0] == dcols[0] and y24[1] == dcols[2], f"year={year_row} date={date_row}")
        check("month-day under 2023 cols", len(y23) == 2 and y23[0] == dcols[1] and y23[1] == dcols[3], f"year={year_row} date={date_row}")
    _check_page_source_conservation(page, result.entries)


def test_p325_stage3_loss_provision_columns() -> None:
    print("--- P325 阶段三贷款损失准备 表头与数据列对齐 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 325)
    result = build_page(page)
    tables = _table_entries(result)
    region = next(
        (e for e in tables if e.table and any(
            any("82,590" in c for c in r) and any("珠江三角洲" in c for c in r)
            for r in dense_rows(e.table)
        )),
        None,
    )
    check("P325 stage3 table", region is not None)
    if not region:
        return
    rows = dense_rows(region.table)
    hdr = next((r for r in rows if "贷款总额" in r and "阶段一" in r), None)
    body = next((r for r in rows if any("82,590" in c for c in r)), None)
    check("subheader row", hdr is not None, str(hdr))
    check("body row", body is not None, str(body))
    if hdr and body:
        hi = hdr.index(next(c for c in hdr if "贷款总额" in c))
        ai = hdr.index(next(c for c in hdr if "阶段一" in c))
        bi = hdr.index(next(c for c in hdr if "阶段二" in c))
        ti = hdr.index(next(c for c in hdr if c.strip() == "阶段三"))
        check("贷款总额 col", body[hi].strip() == "82,590", f"hdr={hdr} body={body}")
        check("阶段一 col", body[ai].strip() == "(48,731)", f"hdr={hdr} body={body}")
        check("阶段二 col", body[bi].strip() == "(34,726)", f"hdr={hdr} body={body}")
        check("阶段三 col", body[ti].strip() == "(62,769)", f"hdr={hdr} body={body}")
    dist = next(
        (e for e in tables if e.table and any(
            any("5,240,886" in c for c in r) for r in dense_rows(e.table)
        )),
        None,
    )
    check("P325 region table", dist is not None)
    if dist:
        drow = next(r for r in dense_rows(dist.table) if any("5,240,886" in c for c in r))
        check("region table 6 value cols", sum(1 for c in drow[1:] if c.strip()) == 6, str(drow))
    _check_page_source_conservation(page, result.entries)


def test_p322_credit_impairment_header_split() -> None:
    print("--- P322 2023 段信用减值表头不合并 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 322)
    result = build_page(page)
    tables = _table_entries(result)
    t2023 = next(
        (e for e in tables if e.table and any(
            any("4,031" in c for c in r) for r in dense_rows(e.table)
        )),
        None,
    )
    check("P322 2023 table", t2023 is not None)
    if not t2023:
        return
    rows = dense_rows(t2023.table)
    hdr = next((r for r in rows if any("已逾期" in c for c in r)), None)
    check("impairment header row", hdr is not None, str(hdr))
    if hdr:
        merged = [c for c in hdr if "已逾期" in c and "已发生" in c]
        check("no merged impairment header cell", not merged, str(hdr))
        overdue = [c for c in hdr if c.strip() == "已逾期未发生信用减值"]
        impaired = [c for c in hdr if c.strip() == "已发生信用减值"]
        check("separate overdue header", len(overdue) == 1, str(hdr))
        check("separate impaired header", len(impaired) == 1, str(hdr))
        if overdue and impaired:
            oi, ii = hdr.index(overdue[0]), hdr.index(impaired[0])
            check("header column order", oi < ii, str(hdr))
    body = next((r for r in rows if any("4,031" in c for c in r)), None)
    sub = next((r for r in rows if "公司" in r and "个人" in r), None)
    if hdr and body and sub:
        ci = sub.index(next(c for c in sub if c.strip() == "公司"))
        pi = sub.index(next(c for c in sub if c.strip() == "个人"))
        check("body under company col", body[ci].strip() == "4,031", f"sub={sub} body={body}")
        check("body under personal col", body[pi].strip() == "27,616", f"sub={sub} body={body}")
    _check_page_source_conservation(page, result.entries)


def test_p328_ecl_stage_header_rows() -> None:
    print("--- P328 信用风险敞口 三层表头保留 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 328)
    result = build_page(page)
    exposure = next(
        (e for e in _table_entries(result) if e.table and any(
            any("24,655,387" in c for c in r) for r in dense_rows(e.table)
        )),
        None,
    )
    check("P328 exposure table", exposure is not None)
    if not exposure:
        return
    rows = dense_rows(exposure.table)
    stage = next((r for r in rows if "阶段一" in r and "阶段二" in r), None)
    period = next((r for r in rows if "12个月" in r and "整个存续期" in r), None)
    loss = next((r for r in rows if r.count("预期信用损失") >= 2), None)
    check("stage header row", stage is not None, str(stage))
    check("period kind row", period is not None, str(period))
    check("ecl loss row", loss is not None, str(loss))
    if stage and period and loss:
        si = rows.index(stage)
        pi = rows.index(period)
        li = rows.index(loss)
        check("header row order", si < pi < li, f"{si} {pi} {li}")
        check("two lifetime period cols", period.count("整个存续期") == 2, str(period))
        check("three ecl loss cols", sum(1 for c in loss if c.strip() == "预期信用损失") == 3, str(loss))
        merged = [c for c in stage if "12个月" in c or "预期信用损失" in c]
        check("stage row not vertically merged", not merged, str(stage))
    body = next((r for r in rows if any("24,655,387" in c for c in r)), None)
    if body and stage:
        bi = body.index(next(c for c in body if "24,655,387" in c))
        si = stage.index(next(c for c in stage if "阶段一" in c))
        check("body under stage col", bi == si, f"stage={stage} body={body}")
    _check_page_source_conservation(page, result.entries)


def test_p330_ecl_header_both_periods() -> None:
    print("--- P330 金融投资 2024/2023 三层表头完整 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 330)
    result = build_page(page)
    tables = _table_entries(result)

    def _loss_row(rows):
        return next((r for r in rows if sum(1 for c in r if c.strip() == "预期信用损失") >= 2), None)

    t2024 = next(
        (e for e in tables if e.table and any(any("9,928,818" in c for c in r) for r in dense_rows(e.table))),
        None,
    )
    t2023 = next(
        (e for e in tables if e.table and any(any("8,910,166" in c for c in r) for r in dense_rows(e.table))),
        None,
    )
    check("P330 2024 table", t2024 is not None)
    check("P330 2023 table", t2023 is not None)
    if not t2024 or not t2023:
        return
    r24 = dense_rows(t2024.table)
    r23 = dense_rows(t2023.table)
    loss24 = _loss_row(r24)
    loss23 = _loss_row(r23)
    check("2024 ecl loss row", loss24 is not None, str(loss24))
    check("2023 ecl loss row", loss23 is not None, str(loss23))
    if loss24:
        check("2024 three ecl cols", sum(1 for c in loss24 if c.strip() == "预期信用损失") == 3, str(loss24))
    if loss23:
        check("2023 three ecl cols", sum(1 for c in loss23 if c.strip() == "预期信用损失") == 3, str(loss23))
        check("2023 total col", "总计" in loss23, str(loss23))
    period23 = next((r for r in r23 if "12个月" in r and r.count("整个存续期") == 2), None)
    check("2023 period row", period23 is not None, str(period23))
    _check_page_source_conservation(page, result.entries)


def test_p331_stage_column_order() -> None:
    print("--- P331 应收同业 阶段一/二/三 分列顺序 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 331)
    result = build_page(page)
    tables = _table_entries(result)

    t2024 = next(
        (e for e in tables if e.table and any(any("1,445,054" in c for c in r) for r in dense_rows(e.table))),
        None,
    )
    t2023 = next(
        (e for e in tables if e.table and any(any("1,798,447" in c for c in r) for r in dense_rows(e.table))),
        None,
    )
    check("P331 2024 table", t2024 is not None)
    check("P331 2023 table", t2023 is not None)
    if not t2024 or not t2023:
        return

    def _stage_row(rows):
        return next((r for r in rows if "阶段一" in r and "阶段二" in r and "阶段三" in r), None)

    r24 = dense_rows(t2024.table)
    r23 = dense_rows(t2023.table)
    stage24 = _stage_row(r24)
    stage23 = _stage_row(r23)
    check("2024 stage row", stage24 is not None, str(stage24))
    check("2023 stage row", stage23 is not None, str(stage23))
    for label, row in (("2024", stage24), ("2023", stage23)):
        if not row:
            continue
        texts = [c.strip() for c in row if c.strip()]
        check(f"{label} three stage cols", texts == ["阶段一", "阶段二", "阶段三"], str(row))
        merged = [c for c in row if "阶段二" in c and "阶段三" in c]
        check(f"{label} no merged stage cols", not merged, str(row))
    _check_page_source_conservation(page, result.entries)


def test_p335_entity_scope_column_placement() -> None:
    print("--- P335 本集团/本行 按坐标分列 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 335)
    result = build_page(page)
    tables = _table_entries(result)
    tbl = next(
        (e for e in tables if e.table and any(
            any("1,082,973" in c for c in r) for r in dense_rows(e.table)
        )),
        None,
    )
    check("P335 rating table", tbl is not None)
    if not tbl:
        return
    rows = dense_rows(tbl.table)
    entity_row = next((r for r in rows if "本集团" in r and "本行" in r), None)
    check("entity scope row", entity_row is not None, str(entity_row))
    if entity_row:
        gi = entity_row.index(next(c for c in entity_row if c.strip() == "本集团"))
        bi = entity_row.index(next(c for c in entity_row if c.strip() == "本行"))
        check("本集团 before 本行", gi < bi, str(entity_row))
        check("本行 not adjacent to 本集团", bi - gi >= 2, str(entity_row))
        year_row = next((r for r in rows if r.count("2024年") >= 2), None)
        if year_row:
            y24 = [i for i, c in enumerate(year_row) if c.strip() == "2024年"]
            check("本集团 over 2024 pair", gi == y24[0], f"entity={entity_row} year={year_row}")
            check("本行 over 2024 pair", bi == y24[1], f"entity={entity_row} year={year_row}")
    _check_page_source_conservation(page, result.entries)


def test_p340_sensitivity_header_columns() -> None:
    print("--- P340 敏感性分析 三列表头分列 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 340)
    result = build_page(page)
    tables = _table_entries(result)

    t2024 = next(
        (e for e in tables if e.table and any(any("502,471" in c for c in r) for r in dense_rows(e.table))),
        None,
    )
    t2023 = next(
        (e for e in tables if e.table and any(any("504,308" in c for c in r) for r in dense_rows(e.table))),
        None,
    )
    check("P340 2024 table", t2024 is not None)
    check("P340 2023 table", t2023 is not None)
    if not t2024 or not t2023:
        return

    def _bottom_hdr(rows):
        return next(
            (r for r in rows if "的预期信用损失" in r and "生命周期的影响" in r),
            None,
        )

    r24 = dense_rows(t2024.table)
    r23 = dense_rows(t2023.table)
    h24 = _bottom_hdr(r24)
    h23 = _bottom_hdr(r23)
    check("2024 bottom header row", h24 is not None, str(h24))
    check("2023 bottom header row", h23 is not None, str(h23))
    for label, hdr, body_amt in (("2024", h24, "45,810"), ("2023", h23, "49,411")):
        if not hdr:
            continue
        merged = [c for c in hdr if "的预期信用损失" in c and "生命周期的影响" in c]
        check(f"{label} headers not merged", not merged, str(hdr))
        ei = hdr.index(next(c for c in hdr if "的预期信用损失" in c))
        li = hdr.index(next(c for c in hdr if c.strip() == "生命周期的影响"))
        pi = hdr.index(next(c for c in hdr if c.strip() == "目前损失准备"))
        check(f"{label} three distinct cols", ei < li < pi, str(hdr))
        body = next(r for r in (r24 if label == "2024" else r23) if body_amt in r)
        check(f"{label} lifecycle under header", body[li].strip() == body_amt, f"hdr={hdr} body={body}")
    _check_page_source_conservation(page, result.entries)


def test_p345_wrapped_label_column() -> None:
    print("--- P345 折行科目名 标签列左对齐 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 345)
    result = build_page(page)
    tables = _table_entries(result)
    tbl = next(
        (e for e in tables if e.table and any(
            any("2,521,325" in c for c in r) for r in dense_rows(e.table)
        )),
        None,
    )
    check("P345 rate risk table", tbl is not None)
    if not tbl:
        return
    rows = dense_rows(tbl.table)
    wrap = next((r for r in rows if "同业及其他金融机构存放款项和" in r), None)
    data = next((r for r in rows if "拆入资金" in r and "2,521,325" in r), None)
    check("wrapped label row", wrap is not None, str(wrap))
    check("data row", data is not None, str(data))
    if wrap:
        li = wrap.index(next(c for c in wrap if "同业及其他金融机构存放款项和" in c))
        check("wrapped label in col0", li == 0, str(wrap))
        check("wrapped label not in col1", not (len(wrap) > 1 and wrap[1].strip()), str(wrap))
    if data:
        di = data.index(next(c for c in data if c.strip() == "拆入资金"))
        check("continuation label col0", di == 0, str(data))
    _check_page_source_conservation(page, result.entries)


def test_p356_no_duplicate_table_tail_in_text() -> None:
    print("--- P356 表底数据行不进 TEXT 重复 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 356)
    result = build_page(page)
    tables = _table_entries(result)
    texts = _text_entries(result)
    t2023 = next(
        (e for e in tables if e.table and any(
            any("2,218,419" in c for c in r) for r in dense_rows(e.table)
        )),
        None,
    )
    check("P356 2023 table", t2023 is not None)
    if t2023:
        rows = dense_rows(t2023.table)
        guarantee = next(
            (r for r in rows if any("担保" in c and "承兑" in c for c in r)),
            None,
        )
        check("guarantee row in table", guarantee is not None, str(rows[-2:]))
    dup_texts = [
        e for e in texts
        if e.text_block and "2,218,419" in e.text_block.text and "担保" in e.text_block.text
    ]
    check("no duplicate guarantee text", len(dup_texts) == 0, str([e.text_block.text for e in dup_texts]))
    loan_dup = [
        e for e in texts
        if e.text_block and "1,611,820" in e.text_block.text and "表外贷款" in e.text_block.text
    ]
    check("no duplicate loan commitment text", len(loan_dup) == 0)
    _check_page_source_conservation(page, result.entries)


def test_p293_note_sections_no_duplicate() -> None:
    print("--- P293 附注节 44/45/46 不重复不拆 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 293)
    result = build_page(page)
    tables = _table_entries(result)
    texts = _text_entries(result)
    check("P293 three tables", len(tables) == 3, f"got {len(tables)}")
    sec44 = [e for e in tables if e.table and any(
        "公允价值变动" in c for r in dense_rows(e.table) for c in r
    )]
    check("one sec44 table", len(sec44) == 1, f"got {len(sec44)}")
    sec45 = [e for e in tables if e.table and any(
        "保险业务收入" in c for r in dense_rows(e.table) for c in r
    )]
    check("one sec45 table", len(sec45) == 1, f"got {len(sec45)}")
    admin = next(
        (e for e in tables if e.table and any(
            "214,312" in c for r in dense_rows(e.table) for c in r
        )),
        None,
    )
    check("sec46 admin table", admin is not None)
    if admin and admin.table:
        rows = dense_rows(admin.table)
        check("sec46 not split", len(rows) >= 20, f"rows={len(rows)}")
        check("sec46 has 员工成本", any("员工成本" in c for r in rows for c in r))
        check("sec46 has 物业及设备", any("物业及设备" in c for r in rows for c in r))
        check("sec46 no sec45 tail", not any("7,393" in c for r in rows[:2] for c in r))
    dup44_text = sum(
        1 for e in texts
        if e.text_block and "公允价值变动" in e.text_block.text
    )
    check("no orphan sec44 text", dup44_text == 0, f"count={dup44_text}")
    sec46_text = next(
        (e for e in texts if e.text_block and "46" in e.text_block.text and "业务及管理费" in e.text_block.text),
        None,
    )
    check("sec46 title in text", sec46_text is not None)
    _check_page_source_conservation(page, result.entries)


def test_p254_intangible_assets_split() -> None:
    print("--- P254 无形资产 2024/2023 变动表 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 254)
    result = build_page(page)
    tables = _table_entries(result)
    texts = _text_entries(result)
    check("P254 two tables", len(tables) == 2, f"got {len(tables)}")
    if len(tables) < 2:
        return
    t24 = dense_rows(tables[0].table)
    t23 = dense_rows(tables[1].table)
    check("2024 ends with 4,569 closing", any("4,569" in c for r in t24[-3:] for c in r))
    check("2024 no orphan closing in 2023 table", not any(
        "4,569" in c for r in t23[:3] for c in r
    ))
    check("2023 starts with column header", any("软件" in c for r in t23[:3] for c in r))
    check("2023 ends with 5,101", any("5,101" in c for r in t23 for c in r))
    foot = next((e for e in texts if e.text_block and "汇率变动" in e.text_block.text), None)
    check("footnote in text", foot is not None)
    _check_page_source_conservation(page, result.entries)


def test_p36_bond_tables() -> None:
    print("--- P36 债券三表 + 十大金融债券 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 36)
    result = build_page(page)
    tables = _table_entries(result)
    texts = _text_entries(result)
    check("P36 three tables", len(tables) == 3, f"got {len(tables)}")
    if len(tables) < 3:
        return
    t_currency = dense_rows(tables[0].table)
    t_issuer = dense_rows(tables[1].table)
    t_top10 = dense_rows(tables[2].table)
    check("currency table 人民币", any("人民币" in r[0] for r in t_currency))
    check("issuer table 政府", any("政府" in r[0] for r in t_issuer))
    check("top10 bond rows", any("政策性银行金融债券" in c for r in t_top10 for c in r))
    check("top10 has face value", any("17,440" in c for r in t_top10 for c in r))
    ncol = max(len(r) for r in t_top10) if t_top10 else 0
    from codes.table_engine.geometry.cell_numeric_repair import is_illegal_value_cell
    check("top10 col count sane", 3 <= ncol <= 5, f"got {ncol}")
    check(
        "top10 no illegal numeric",
        not any(is_illegal_value_cell(c) for r in t_top10 for c in r if c),
    )
    check(
        "top10 unit label same column",
        t_top10[0][0].startswith("（人民币")
        and any("政策性银行金融债券" in r[0] for r in t_top10[1:4]),
    )
    check(
        "top10 no fully blank column",
        not any(
            all(not (r[j].strip() if j < len(r) else "") for r in t_top10)
            for j in range(ncol)
        ),
    )
    check("top10 not plain text", not any(
        e.text_block and "17,440" in e.text_block.text and "政策性银行金融债券" in e.text_block.text
        for e in texts
    ))
    foot = next((e for e in texts if e.text_block and "减值准备" in e.text_block.text), None)
    check("footnote in text", foot is not None)
    _check_page_source_conservation(page, result.entries)


def test_p24_interest_table() -> None:
    print("--- P24 利息净收入表 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 24)
    result = build_page(page)
    tables = _table_entries(result)
    if not tables or not tables[0].table:
        check("P24 table built", False)
        return
    t = tables[0].table
    rows = dense_rows(t)
    check("P24 7 cols", t.grid.col_count == 7, f"cols={t.grid.col_count}")
    hdr = next((r for r in rows if r and "平均余额" in "".join(r)), None)
    check("metric headers split", hdr is not None)
    if hdr:
        check("支出 col separate", "支出" in hdr and "成本率" in "".join(hdr))
        exp_idx = hdr.index("支出") if "支出" in hdr else -1
        rate_idx = next((i for i, c in enumerate(hdr) if "成本率" in c), -1)
        check("支出 != 成本率 col", exp_idx >= 0 and rate_idx >= 0 and exp_idx != rate_idx)
    loan = next((r for r in rows if r and "发放贷款" in r[0]), None)
    if loan:
        check("loan row values", loan[1] == "25,228,241" and loan[3] == "3.43")
    joined = "".join("".join(r) for r in rows[:5])
    check("no glued year banner", "2024年利息收入" not in joined.replace(" ", ""))
    tables = _table_entries(result)
    check("single table entry", len(tables) == 1, f"got {len(tables)}")
    if len(tables) == 1:
        all_rows = dense_rows(tables[0].table)
        flat = _flat_table(tables[0].table)
        check("has liability section", "吸收存款" in flat and "负债" in flat)
        check("has net interest", "589,882" in flat and "617,233" in flat)
        check("has nim row", "净利息收益率" in flat and "1.51" in flat and "1.70" in flat)
        check("rows >= 22", len(all_rows) >= 22, f"rows={len(all_rows)}")


def test_p44_revenue_table() -> None:
    print("--- P44 业务板块营收表 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    result = build_page(load_page(cache, 44))
    tables = _table_entries(result)
    texts = _text_entries(result)
    rev = next(
        (e for e in tables if e.table and any("750,151" in c for r in dense_rows(e.table) for c in r)),
        None,
    )
    check("P44 revenue table", rev is not None)
    if not rev or not rev.table:
        return
    rows = dense_rows(rev.table)
    total = next((r for r in rows if r and r[0].startswith("总额")), None)
    check("total row label clean", total is not None and total[0] == "总额", repr(total[0][:40]) if total else "")
    flat = _flat_table(rev.table)
    check("no narrative in table", "2,306.64" not in flat and "占比为" not in flat)
    if texts:
        joined = "\n".join(e.text_block.text for e in texts if e.text_block)
        check("narrative after table in text", "2,306.64" in joined or "占比为" in joined)


def test_p229_buy_repo_net_row() -> None:
    print("--- P229 买入返售净额行 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    result = build_page(load_page(cache, 229))
    buy_repo = next(
        (
            e
            for e in _table_entries(result)
            if e.table and any("622,592" in c for r in dense_rows(e.table) for c in r)
        ),
        None,
    )
    check("P229 buy repo table", buy_repo is not None)
    if not buy_repo or not buy_repo.table:
        return
    rows = dense_rows(buy_repo.table)
    net = next((r for r in rows if r and str(r[0]).startswith("净额")), None)
    check("has net row", net is not None, str(rows[-1][0]) if rows else "")
    if net:
        check("net values", "622,559" in net[1] and "603,048" in net[3], str(net))


def test_p361_fair_value_single_table() -> None:
    print("--- P361 公允价值层级单表 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    result = build_page(load_page(cache, 361))
    tables = _table_entries(result)
    texts = _text_entries(result)
    check("P361 one main table", len(tables) == 1, f"got {len(tables)}")
    if not tables or not tables[0].table:
        return
    rows = dense_rows(tables[0].table)
    flat = " ".join(c for r in rows for c in r if c)
    check("has asset total", "4,475,360" in flat)
    check("has liability total", "318,744" in flat)
    check("no mid section as lone text", not any(
        e.text_block and "以公允价值计量且其变动计入当期损益" in e.text_block.text
        and "金融负债" not in e.text_block.text
        for e in texts
    ))


def test_p363_fair_value_period_continuation() -> None:
    print("--- P363 第三层级变动 2024/2023 同表续片 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 363)
    result = build_page(page)
    tables = _table_entries(result)
    texts = _text_entries(result)
    period_tables = [
        e for e in tables
        if e.table and any(
            any("168,725" in c or "171,891" in c for c in r)
            for r in dense_rows(e.table)
        )
    ]
    check("P363 fair value table exists", len(period_tables) >= 1)
    check("P363 single table for both periods", len(period_tables) == 1, f"got {len(period_tables)}")
    if period_tables and period_tables[0].table:
        rows = dense_rows(period_tables[0].table)
        flat = " ".join(c for r in rows for c in r if c)
        check("has 2024 opening", "171,891" in flat)
        check("has 2023 closing", "171,891" in flat and "2023" in flat)
        check("has 2024 closing", "168,725" in flat)
        header_frag_texts = [
            e for e in texts
            if e.text_block
            and "其他以公允价值计量" in e.text_block.text
            and "且其变动计入其他" in e.text_block.text
        ]
        check("no orphan header fragment text", len(header_frag_texts) == 0)
    _check_page_source_conservation(page, result.entries)


def test_p104_industry_loan_columns() -> None:
    print("--- P104 按行业贷款 8 值列 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    result = build_page(load_page(cache, 104))
    tables = _table_entries(result)
    industry = next(
        (
            e
            for e in tables
            if e.table
            and any("本行境内贷款" in r[0] for r in dense_rows(e.table))
        ),
        None,
    )
    check("P104 industry table", industry is not None)
    if not industry or not industry.table:
        return
    t = industry.table
    check(">= 9 cols", t.grid.col_count >= 9, str(t.grid.col_count))
    rows = dense_rows(t)
    body = next((r for r in rows if r and "本行境内贷款" in r[0]), None)
    check("body row found", body is not None)
    if body and len(body) >= 5:
        check("amount col separate", body[1] == "24,938,748", body[1])
        check("pct col separate", body[2] == "96.69", body[2])
        check("npl amount separate", body[3] == "325,868", body[3])
        check("npl rate separate", body[4] == "1.31", body[4])
    glued = any(
        "24,938,748" in c and "96.69" in c
        for row in rows
        for c in row
    )
    check("no glued amount+pct", not glued)
    power = next((r for r in rows if r and "电力" in r[0]), None)
    if power:
        check(
            "P104 power label split",
            power[0].strip() == "电力、热力、燃气及水生产和供应业"
            and power[1].strip() == "1,600,664",
            power[:3],
        )


def test_p26_loan_interest_structure_table() -> None:
    print("--- P26 贷款利息结构表 标签/数值分列 ---")
    from pathlib import Path

    from codes.table_engine.geometry.cell_numeric_repair import is_illegal_value_cell

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    result = build_page(load_page(cache, 26))
    tables = _table_entries(result)
    loan = next(
        (
            e
            for e in tables
            if e.table
            and any("24,338,681" in c for r in dense_rows(e.table) for c in r)
        ),
        None,
    )
    check("P26 loan table", loan is not None)
    if not loan or not loan.table:
        return
    rows = dense_rows(loan.table)
    body = next((r for r in rows if r and "本行境内贷款" in r[0]), None)
    check("P26 body row label only in col0", body is not None and "24,338" not in body[0])
    if body and len(body) >= 3:
        check("P26 balance col", body[1].strip() == "24,338,681", body[1])
        check("P26 interest col", body[2].strip() == "812,900", body[2])
    check(
        "P26 no glued label+amount",
        not any(
            "24,338" in r[0] and "本行境内" in r[0]
            for r in rows
            if r
        ),
    )
    check(
        "P26 value cols clean",
        not any(
            is_illegal_value_cell(c)
            for r in rows
            for ci, c in enumerate(r)
            if c and ci >= 1
        ),
    )


def test_p34_annual_loan_split() -> None:
    print("--- P34 年报贷款分布 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 34)
    result = build_page(page)
    tables = _table_entries(result)
    check("P34 3 tables", len(tables) == 3, f"got {len(tables)}")
    if len(tables) < 2:
        return
    t1 = dense_rows(tables[0].table)
    t2 = dense_rows(tables[1].table)
    check("table1 ends at total", any("发放贷款和垫款总额" in r[0] for r in t1))
    check("table1 no guarantee section", not any("按担保方式" in r[0] for r in t1))
    check("table2 has guarantee data", any("信用贷款" in r[0] for r in t2))
    check("table2 has date header", any("2024" in c and "12" in c for r in t2[:5] for c in r if c))
    check("table2 no section title in table", not any("按担保方式" in r[0] for r in t2))
    check("table2 no narrative in table", not any("下表列出" in r[0] for r in t2))


def test_p15_financial_summary_2024_values() -> None:
    print("--- P15 财务摘要 2024 列 OCR 漏检补缺 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 15)
    result = build_page(page)
    tables = _table_entries(result)
    check("P15 table built", tables and tables[0].table is not None)
    if not tables or not tables[0].table:
        return
    rows = dense_rows(tables[0].table)
    ded = next((r for r in rows if "扣除非经常" in r[0]), None)
    fee = next((r for r in rows if "手续费及佣金" in r[0]), None)
    cap = next((r for r in rows if r[0].strip() == "资本充足率5"), None)
    if ded:
        check("P15 ded 2024 col", ded[1].strip() == "10.68", ded[1:])
    if fee:
        check("P15 fee 2024 col", fee[1].strip() == "13.99", fee[1:])
    if cap:
        check("P15 cap 2024 col", cap[1].strip() == "19.69", cap[1:])
    _check_page_source_conservation(page, result.entries)


def test_p14_financial_summary() -> None:
    print("--- P14 财务摘要 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 14)
    result = build_page(page)
    tables = _table_entries(result)
    if not tables or not tables[0].table:
        check("P14 table built", False)
        return
    t = tables[0].table
    rows = dense_rows(t)
    check("P14 >= 7 cols", t.grid.col_count >= 7, f"cols={t.grid.col_count}")
    rev = next((r for r in rows if r and "营业收入" in r[0]), None)
    check("revenue row", rev is not None)
    if rev:
        check("label col only", "750,151" not in rev[0], rev[0][:40])
        check("value col separate", rev[1] == "750,151", rev[1:])
        check("no glued pairs", "769,736" not in rev[0] and "(2.54)" not in rev[2] or rev[3] == "(2.54)")


def test_p37_leverage_tail() -> None:
    print("--- P37 杠杆率续行 ---")
    from codes.table_engine.geometry.grid_infer import _merged_numeric_violations

    synthetic_rows = [{
        "row_phase": "body",
        "items": [
            {"text": "425,464", "x0": 376.8, "x1": 410.9},
            {"text": "319,349", "x0": 479.5, "x1": 513.7},
        ],
    }]
    check(
        "right-aligned periods are separate columns",
        _merged_numeric_violations(
            synthetic_rows,
            [(60.0, 95.0), (96.0, 260.0), (260.0, 413.6), (413.6, 540.0)],
        ) == 0,
    )
    result = test_page_entries(37, max_text=2, max_table=1, min_table=1)
    texts = _text_entries(result)
    tables = _table_entries(result)
    if tables and tables[0].table:
        t = tables[0].table
        check("P37 no ghost value column", len(t.grid.ranges) == 4, len(t.grid.ranges))
        ri15 = find_row_index(t, "15")
        check("row 15", ri15 is not None)
        if ri15 is not None:
            check(
                "row 15 two periods",
                cell_text(t, ri15, 2).strip() == "47,997"
                and cell_text(t, ri15, 3).strip() == "1,982",
                [cell_text(t, ri15, ci) for ci in range(len(t.rows[ri15]))],
            )
        ri19 = find_row_index(t, "19")
        if ri19 is not None:
            check(
                "long amounts stay in same period columns",
                cell_text(t, ri19, 2).strip() == "(5,754,628)"
                and cell_text(t, ri19, 3).strip() == "(5,535,401)",
                [cell_text(t, ri19, ci) for ci in range(len(t.rows[ri19]))],
            )
        check("row 27", find_row_index(t, "27") is not None)
        check("row 27a values", "607,773" in _flat_table(t))
        check("row 28a values", "43,213,494" in _flat_table(t))
        check("row 29a", find_row_index(t, "29a") is not None)
        check("row 29a values", "7.69" in _flat_table(t))
        flat = _flat_table(t)
        check("no footnote1 in table", "杠杆率a指" not in flat or "不考虑临时豁免" in "\n".join(
            e.text_block.text for e in texts if e.text_block
        ))
    if texts:
        joined = "\n".join(e.text_block.text for e in texts if e.text_block)
        check("footnote 1 in text", "杠杆率a指" in joined)


def test_p42_liq2_tail() -> None:
    print("--- P42 LIQ2 续表尾 ---")
    result = test_page_entries(42, max_text=2, max_table=1, min_table=1)
    texts = _text_entries(result)
    tables = _table_entries(result)
    if tables and tables[0].table:
        t = tables[0].table
        expected = {
            "32": ("表外项目", "7,516,665", "201,910"),
            "33": ("所需的稳定资金合计", "", "21,027,700"),
            "34": ("净稳定资金比例（%）", "", "133.91"),
        }
        for serial, (label, col5, col6) in expected.items():
            ri = find_row_index(t, serial)
            check(f"row {serial} serial split", ri is not None)
            if ri is not None:
                check(f"row {serial} row_no only", cell_text(t, ri, 0) == serial, cell_text(t, ri, 0))
                check(f"row {serial} label column", cell_text(t, ri, 1) == label, cell_text(t, ri, 1))
                check(
                    f"row {serial} values preserved",
                    cell_text(t, ri, 5) == col5 and cell_text(t, ri, 6) == col6,
                    [cell_text(t, ri, ci) for ci in range(len(t.rows[ri]))],
                )
        check("row 34 value", "133.91" in _flat_table(t))
        flat = _flat_table(t)
        check("no footnote in table", "满足监管要求" not in flat and "1．折算" not in flat)
        check("notes empty", not (t.notes or "").strip())
    if texts and tables:
        joined = "\n".join(e.text_block.text for e in texts if e.text_block)
        check("footnote marker", "折算前数值" in joined)
        check("narrative nsfr", "133.91%" in joined or "净稳定资金比例" in joined)
        check("text below table", texts[0].y0 > tables[0].y1 - 5)


def test_p352_maturity_header() -> None:
    print("--- P352 剩余到期日表头 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 352)
    result = build_page(page)
    tables = _table_entries(result)
    check("P352 one table", len(tables) == 1)
    if not tables or not tables[0].table:
        return
    rows = dense_rows(tables[0].table)
    head = rows[:8]
    flat_head = " ".join(str(c) for r in head for c in r)
    check("entity 本集团", "本集团" in flat_head)
    check("report date", "2024年12月31日" in flat_head)
    check("maturity 无期限", "无期限" in flat_head)
    check("maturity 合计", "合计" in flat_head)
    check("section 资产", any(str(r[0]).strip() == "资产" for r in head))
    check("no missing header warn", not any("missing header band" in w for w in result.warnings))
    _check_page_source_conservation(page, result.entries)


def test_p364_fair_value_header() -> None:
    print("--- P364 公允价值第三层级表头 ---")
    from pathlib import Path

    cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
    if not cache.exists():
        print("  [SKIP] annual report cache missing")
        return
    page = load_page(cache, 364)
    result = build_page(page)
    tables = _table_entries(result)
    texts = _text_entries(result)
    check("P364 two tables", len(tables) == 2)
    if not tables or not tables[0].table:
        return
    rows = dense_rows(tables[0].table)
    head = rows[:8]
    flat_head = " ".join(str(c) for r in head for c in r)
    check("entity 本行", "本行" in flat_head)
    check("2024 year", "2024年" in flat_head)
    check("fv phrase", "公允价值" in flat_head or "计入当期损益" in flat_head)
    check("col 金融资产", "金融资产" in flat_head)
    flat0 = _flat_table(tables[0].table)
    check("row 2024 opening", "3,757" in flat0 and "2024年1月1日" in flat0)
    if texts:
        joined = "\n".join(e.text_block.text for e in texts if e.text_block)
        check("section in text", "60 风险管理" in joined or "(5)" in joined)
        check("no 本行 only in text", "本行" not in joined)
    _check_page_source_conservation(page, result.entries)


def _check_page_source_conservation(page, entries) -> None:
    """页内带文本 item 须出现在 TABLE 或 TEXT 的 source_items 中。"""
    from codes.table_engine.conservation.item_conservation import audit_scope_conservation
    from codes.table_engine.split.content_partition import _all_table_source_ids

    covered: set[str] = set()
    for entry in entries:
        if entry.kind == "text" and entry.text_block:
            covered.update(str(s) for s in entry.text_block.source_items or [])
        if entry.kind == "table" and entry.table:
            covered |= _all_table_source_ids([entry.table])

    page_ids = {it.item_index for it in page.items if it.text.strip()}
    missing = page_ids - covered
    # 年报页眉页脚、页码等常落在 table region 外，允许少量未归属 item
    check(
        "gap items conserved",
        len(missing) <= max(24, int(len(page_ids) * 0.12)),
        f"missing={len(missing)}",
    )
    scope_missing = audit_scope_conservation(entries, page)
    check(
        "scope items conserved",
        len(scope_missing) == 0,
        f"missing={len(scope_missing)}",
    )


def test_split_y_from_bbox() -> None:
    print("--- split Y 来自 bbox ---")
    page = load_page(CACHE, 10)
    result = build_page(page)
    texts = _text_entries(result)
    tables = _table_entries(result)
    if texts and texts[0].text_block and tables and tables[0].table:
        ty0 = texts[0].y0
        table = tables[0].table
        check("text above table", ty0 < table.y0 - 5, f"text_y0={ty0:.0f} table_y0={table.y0:.0f}")
        rows = dense_rows(table)
        body_start = find_pillar_table_body_start_row(rows)
        if body_start == 0 and len(table.rows) >= 2:
            expected_y0, _ = row_y_bounds(table, 0, 1)
            check("table y0 matches cell bbox", abs(table.y0 - expected_y0) < 2.0, f"{table.y0:.1f} vs {expected_y0:.1f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", default="5,6,9,10,11,12,13,22,29,34,36,41,42,44,104,106,229,254,268,271,275,293,301,312,314,320,322,325,328,330,331,335,340,345,356,361,363", help="逗号分隔页码")
    args = parser.parse_args()
    pages = [int(x.strip()) for x in args.pages.split(",") if x.strip()]

    if 5 in pages:
        test_row_classify_value_first()
        test_grid_prune_blank_lines()
        test_illegal_numeric_cell_detect()
        test_item_conservation_cell_text()
        test_p5_km1()
    if 6 in pages:
        test_p6()
    if 9 in pages:
        test_p9_ov1()
    if 10 in pages:
        test_p10_cc1()
    if 11 in pages:
        test_p11_continuation()
    if 12 in pages:
        test_p12_cc1_header_body_single_table()
    if 13 in pages:
        test_p13_cc2_serial_label_columns()
    if 14 in pages:
        test_p14_cc2_header_code_column()
    if 19 in pages:
        test_p19_cr5_serial_label_columns()
    if 21 in pages:
        test_p21_cr6_multi_value_columns()
    if 22 in pages:
        test_p22_cr6_footer_note_not_in_last_cell()
    if 33 in pages:
        test_p33_irrbb_footer_no_split()
    if 35 in pages:
        test_p35_dsib_indicator_columns()
    if 25 in pages:
        test_role_driven_serial_and_wrap_merge()
        test_p25_ccrf_serial_label_wrap()
    if 27 in pages:
        test_p27_sec1_header_rows_separate()
    if 38 in pages:
        test_p38_liq1_two_value_columns()
    if 41 in pages:
        test_p41_multi_region_merge()
    if 42 in pages:
        test_p42_liq2_tail()
    if 37 in pages:
        test_p37_leverage_tail()
    if 26 in pages:
        test_p26_loan_interest_structure_table()
    if 34 in pages:
        test_p34_annual_loan_split()
    if 36 in pages:
        test_p36_bond_tables()
    if 44 in pages:
        test_p44_revenue_table()
    if 104 in pages:
        test_p104_industry_loan_columns()
    if 106 in pages:
        test_p106_loan_concentration()
    if 229 in pages:
        test_p229_buy_repo_net_row()
    if 254 in pages:
        test_p254_intangible_assets_split()
    if 268 in pages:
        test_p268_deposits_single_main_table()
    if 271 in pages:
        test_p271_employee_benefits_period_headers()
    if 275 in pages:
        test_p275_debt_securities_columns()
    if 293 in pages:
        test_p293_note_sections_no_duplicate()
    if 301 in pages:
        test_p301_segment_period_header_split()
    if 312 in pages:
        test_p312_related_party_header_rows()
    if 314 in pages:
        test_p314_subsidiary_balance_columns()
        test_numeric_repair_p314()
    if 320 in pages:
        test_p320_entity_period_header_rows()
    if 322 in pages:
        test_p322_credit_impairment_header_split()
    if 325 in pages:
        test_p325_stage3_loss_provision_columns()
    if 328 in pages:
        test_p328_ecl_stage_header_rows()
    if 330 in pages:
        test_p330_ecl_header_both_periods()
    if 356 in pages:
        test_p356_no_duplicate_table_tail_in_text()
    if 345 in pages:
        test_p345_wrapped_label_column()
    if 340 in pages:
        test_p340_sensitivity_header_columns()
    if 335 in pages:
        test_p335_entity_scope_column_placement()
    if 331 in pages:
        test_p331_stage_column_order()
    if 361 in pages:
        test_p361_fair_value_single_table()
    if 363 in pages:
        test_p363_fair_value_period_continuation()
    if 352 in pages:
        test_p352_maturity_header()
    if 364 in pages:
        test_p364_fair_value_header()
    if 14 in pages:
        test_p14_financial_summary()
    if 15 in pages:
        test_p15_financial_summary_2024_values()
    if 28 in pages:
        test_p28_fee_income()
    if 29 in pages:
        test_p29_non_interest_income_split()
    if 24 in pages:
        test_p24_interest_table()
    if any(p in pages for p in (10, 11)):
        test_split_y_from_bbox()

    print(f"\n=== Step 4: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
