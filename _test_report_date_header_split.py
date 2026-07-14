# -*- coding: utf-8 -*-
"""复合报告期表头拆分。"""
from __future__ import annotations

from codes.table_engine.geometry.numeric import (
    is_report_date_header_compound_text,
    split_report_date_header_compound_text,
)


def test_split_compound_report_date_header():
    parts = split_report_date_header_compound_text(
        "2024 年 12 月 31 日 2023 年 12 月 31 日 增减幅度"
    )
    assert len(parts) == 3, parts
    assert "2024" in parts[0] and "2023" in parts[1]
    assert parts[2] == "增减幅度"
    assert is_report_date_header_compound_text(
        "2024 年 12 月 31 日 2023 年 12 月 31 日 增减幅度"
    )


if __name__ == "__main__":
    test_split_compound_report_date_header()
    print("report date header split OK")
