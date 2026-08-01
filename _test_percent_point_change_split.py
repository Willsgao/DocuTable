# -*- coding: utf-8 -*-
"""百分点增减跨列粘连：与变化原因拆分分流。"""
from __future__ import annotations

from codes.table_engine.geometry.numeric import (
    looks_like_percent_point_change_phrase,
    split_percent_point_change_text,
    split_percent_trailing_text,
)
from codes.table_engine.geometry.cell_numeric_repair import (
    expand_percent_point_change_glued_row_items,
    expand_percent_reason_glued_row_items,
)


def test_split_percent_point_change():
    assert split_percent_point_change_text("18.78% 下降 0.97 个百分点") == (
        "18.78%",
        "下降 0.97 个百分点",
    )
    assert split_percent_point_change_text("18.44% 下降 0.68 个百分点") == (
        "18.44%",
        "下降 0.68 个百分点",
    )
    assert split_percent_point_change_text("10.47%上升1.2个百分点") == (
        "10.47%",
        "上升1.2个百分点",
    )
    # 变化原因不得误判为百分点
    assert split_percent_point_change_text("-41.49%代理业务支出减少") is None
    assert split_percent_trailing_text("18.78% 下降 0.97 个百分点") is None
    assert looks_like_percent_point_change_phrase("下降 0.97 个百分点")


def test_expand_places_into_value_and_change_cols():
    # 模拟 5 列：项目 | 2024 | 2023 | 增减 | 2022
    cols = [(50, 150), (150, 280), (280, 380), (380, 480), (480, 560)]
    items = [
        {
            "text": "17.81%",
            "x0": 234.8,
            "x1": 270.3,
            "y0": 352.0,
            "y1": 366.0,
            "item_index": "a",
        },
        {
            "text": "18.78% 下降 0.97 个百分点",
            "x0": 315.1,
            "x1": 450.5,
            "y0": 352.0,
            "y1": 366.0,
            "item_index": "b",
        },
        {
            "text": "19.48%",
            "x0": 495.5,
            "x1": 530.9,
            "y0": 352.0,
            "y1": 366.0,
            "item_index": "c",
        },
    ]
    out = expand_percent_point_change_glued_row_items(items, cols)
    texts = [str(it["text"]).strip() for it in out]
    assert "18.78%" in texts
    assert "下降 0.97 个百分点" in texts
    assert "18.78% 下降 0.97 个百分点" not in texts
    # 落列：2023 列与增减列
    pct_it = next(it for it in out if it["text"] == "18.78%")
    chg_it = next(it for it in out if "个百分点" in it["text"])
    assert cols[2][0] <= (float(pct_it["x0"]) + float(pct_it["x1"])) / 2 <= cols[2][1]
    assert cols[3][0] <= (float(chg_it["x0"]) + float(chg_it["x1"])) / 2 <= cols[3][1]
    # 变化原因路径不得再拆百分点串
    still = expand_percent_reason_glued_row_items(
        [{"text": "18.78% 下降 0.97 个百分点", "x0": 315, "x1": 450, "y0": 1, "y1": 2}],
        cols,
    )
    assert len(still) == 1


def test_change_reason_split_still_works():
    assert split_percent_trailing_text("-41.49%代理业务支出减少") == (
        "-41.49%",
        "代理业务支出减少",
    )
    cols = [(50, 150), (150, 250), (250, 350), (350, 450), (450, 560)]
    out = expand_percent_reason_glued_row_items(
        [
            {
                "text": "-41.49%代理业务支出减少",
                "x0": 360,
                "x1": 520,
                "y0": 1,
                "y1": 2,
                "item_index": "x",
            }
        ],
        cols,
    )
    texts = [it["text"] for it in out]
    assert texts == ["-41.49%", "代理业务支出减少"]


if __name__ == "__main__":
    test_split_percent_point_change()
    test_expand_places_into_value_and_change_cols()
    test_change_reason_split_still_works()
    print("all checks passed")
