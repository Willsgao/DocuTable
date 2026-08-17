# -*- coding: utf-8 -*-
"""相邻完整年表共享列头时，dedup 不得清空后表列头文本。"""

from codes.table_validator.dedup_engine import DeduplicationEngine


def _var_table(year: str, first_val: str) -> dict:
    return {
        "type": "table",
        "page": 1,
        "data": [
            ["", "", "", year, "", ""],
            ["", "注释", "12月31日", "平均值", "最大值", "最小值"],
            ["交易账簿风险价值", "", first_val, "265", "331", "199"],
            ["  －利率风险", "", "75", "37", "88", "22"],
        ],
    }


def test_dedup_keeps_identical_column_headers_on_year_tables() -> None:
    a = _var_table("2025年", "231")
    b = _var_table("2024年", "300")
    out = DeduplicationEngine().dedup_adjacent([a, b])
    assert len(out) == 2
    header_b = out[1]["data"][1]
    assert "注释" in header_b
    assert "平均值" in header_b
    assert "最大值" in header_b
    assert any(c.strip() for c in header_b), "后表列头不得被清空"


def test_dedup_skips_text_entries() -> None:
    tables = [
        {"type": "text", "page": 1, "data": "说明文字"},
        _var_table("2025年", "231"),
        _var_table("2024年", "300"),
    ]
    out = DeduplicationEngine().dedup_adjacent(tables)
    assert out[2]["data"][1][1] == "注释"


if __name__ == "__main__":
    test_dedup_keeps_identical_column_headers_on_year_tables()
    test_dedup_skips_text_entries()
    print("OK")
