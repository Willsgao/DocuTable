# -*- coding: utf-8 -*-
"""CC2：序号列右容差不得吞科目；资产/负债进序号列。"""

from codes.table_engine.layout.pillar_cc2 import CC2LayoutPlugin
from codes.table_engine.geometry.cell_builder import _assign_item_to_columns


_COL_RANGES = [
    (60.0, 95.0),
    (96.0, 210.0),
    (210.0, 389.22),
    (389.22, 497.4),
    (497.4, 540.0),
]


def test_cc2_col_index_keeps_label_out_of_serial_band() -> None:
    plugin = CC2LayoutPlugin()
    assert plugin.col_index_for_item(70.9, 91.9, "资产", _COL_RANGES) == 0
    assert plugin.col_index_for_item(70.9, 91.9, "负债", _COL_RANGES) == 0
    assert plugin.col_index_for_item(77.4, 82.6, "1", _COL_RANGES) == 0
    # 科目 x0≈100：旧 hi+6 会误判进 col0
    assert plugin.col_index_for_item(100.0, 215.4, "现金及存放中央银行款项", _COL_RANGES) == 1


def test_assign_pillar_cc2_section_and_label_columns() -> None:
    def _cols(text: str, x0: float, x1: float) -> int:
        buckets: list = [[] for _ in _COL_RANGES]
        _assign_item_to_columns(
            {"text": text, "x0": x0, "x1": x1, "y0": 0, "y1": 10},
            _COL_RANGES,
            buckets,
            len(_COL_RANGES),
            "pillar_cc2",
        )
        hit = [i for i, b in enumerate(buckets) if b]
        assert len(hit) == 1, (text, hit)
        return hit[0]

    assert _cols("资产", 70.9, 91.9) == 0
    assert _cols("负债", 70.9, 91.9) == 0
    assert _cols("1", 77.4, 82.6) == 0
    assert _cols("现金及存放中央银行款项", 100.0, 215.4) == 1


def test_cc2_page13_section_headers_in_serial_column() -> None:
    from codes.table_engine.config import DEFAULT_PILLAR_CACHE
    from codes.table_engine.pipeline import build_page
    from codes.table_engine.source.liteparse_loader import load_liteparse_document
    from codes.table_engine.table_access import dense_rows
    from pathlib import Path

    if not Path(DEFAULT_PILLAR_CACHE).exists():
        print("skip: no pillar cache")
        return
    page = load_liteparse_document(DEFAULT_PILLAR_CACHE).get_page(13)
    assert page is not None
    table = max(build_page(page).tables, key=lambda t: len(t.rows))
    rows = dense_rows(table)
    asset = next(r for r in rows if "资产" in [str(c).strip() for c in r])
    liability = next(r for r in rows if "负债" in [str(c).strip() for c in r])
    assert str(asset[0]).strip() == "资产", asset
    assert str(liability[0]).strip() == "负债", liability
    cash = next(r for r in rows if "现金及存放" in "".join(r))
    assert str(cash[0]).strip() == "1", cash
    assert "现金" in str(cash[1]), cash


def test_grid_nucleus_keeps_section_lead_in_serial_col() -> None:
    """回归：grid_nucleus 不得把「资产」从序号列赶到项目列。"""
    import copy
    import json
    from pathlib import Path

    from codes.table_engine.export.legacy_adapter import document_to_legacy_list
    from codes.table_engine.pipeline import DocumentBuilder
    from codes.table_validator.dedup_engine import DeduplicationEngine
    from codes.v2_steps.table_anomaly_bridge import ensure_anomaly_reports

    cache = Path("data/mid_cache/page_013/liteparse/pages.json")
    if not cache.exists():
        print("skip: no page_013 cache")
        return
    lite = json.loads(cache.read_text(encoding="utf-8"))
    tables = DeduplicationEngine().dedup_adjacent(
        document_to_legacy_list(DocumentBuilder().build(str(cache)))
    )
    payload = {"tables": copy.deepcopy(tables)}
    ensure_anomaly_reports(
        payload,
        pdf_path=lite.get("pdf_path"),
        liteparse_data=lite,
        force=True,
    )
    found = False
    for t in payload["tables"]:
        data = t.get("data") or []
        if not (isinstance(data, list) and data and isinstance(data[0], list)):
            continue
        for row in data:
            cells = [str(c).strip() for c in row]
            if "资产" in cells:
                found = True
                assert cells[0] == "资产", row
                assert cells[1] == "", row
    assert found, "资产 row missing"


if __name__ == "__main__":
    test_cc2_col_index_keeps_label_out_of_serial_band()
    test_assign_pillar_cc2_section_and_label_columns()
    test_cc2_page13_section_headers_in_serial_column()
    test_grid_nucleus_keeps_section_lead_in_serial_col()
    print("OK")
