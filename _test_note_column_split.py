# -*- coding: utf-8 -*-
"""注释列不得被「本集团」等表题挤压进科目列。"""

from __future__ import annotations

from codes.table_engine.geometry.grid_infer import (
    _infer_note_column_bounds,
    _ranges_from_numeric_gutters,
)


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    extra = f" ({detail})" if detail and not ok else ""
    print(f"  [{status}] {name}{extra}")
    if not ok:
        raise AssertionError(name)


def _interest_rate_rows_with_entity_scope() -> list[dict]:
    """模拟利率风险表：本集团 + 注释|不计息 + (i) 标记。"""
    return [
        {
            "items": [
                {"text": "本集团", "x0": 109.3, "x1": 130.0, "y0": 177.0, "y1": 186.0},
            ]
        },
        {
            "items": [
                {"text": "注释", "x0": 230.8, "x1": 245.4, "y0": 210.0, "y1": 219.0},
                {"text": "不计息", "x0": 270.1, "x1": 292.0, "y0": 210.0, "y1": 219.0},
                {"text": "3个月以内", "x0": 310.0, "x1": 350.0, "y0": 210.0, "y1": 219.0},
                {"text": "合计", "x0": 480.0, "x1": 510.0, "y0": 210.0, "y1": 219.0},
            ]
        },
        {
            "items": [
                {
                    "text": "发放贷款和垫款",
                    "x0": 116.2,
                    "x1": 167.6,
                    "y0": 271.0,
                    "y1": 280.0,
                },
                {"text": "(i)", "x0": 235.3, "x1": 241.0, "y0": 271.0, "y1": 280.0},
                {"text": "31,704", "x0": 265.2, "x1": 295.0, "y0": 271.0, "y1": 280.0},
                {"text": "13,373,913", "x0": 310.0, "x1": 360.0, "y0": 271.0, "y1": 280.0},
                {"text": "26,926,790", "x0": 480.0, "x1": 530.0, "y0": 271.0, "y1": 280.0},
            ]
        },
        {
            "items": [
                {"text": "投资", "x0": 116.2, "x1": 130.8, "y0": 282.0, "y1": 291.0},
                {"text": "(ii)", "x0": 234.4, "x1": 241.8, "y0": 282.0, "y1": 291.0},
                {"text": "411,653", "x0": 265.2, "x1": 300.0, "y0": 282.0, "y1": 291.0},
                {"text": "985,382", "x0": 310.0, "x1": 350.0, "y0": 282.0, "y1": 291.0},
                {"text": "12,925,133", "x0": 480.0, "x1": 535.0, "y0": 282.0, "y1": 291.0},
            ]
        },
    ]


def test_note_column_not_swallowed_by_entity_scope() -> None:
    print("--- 注释列：本集团不得挤压列界 ---")
    rows = _interest_rate_rows_with_entity_scope()
    nb = _infer_note_column_bounds(rows)
    check("推断出注释列界", nb is not None, str(nb))
    assert nb is not None
    note_lo, note_hi = nb
    check("注释列在标签右侧", note_lo > 180.0, str(nb))
    check("注释列在不计息左侧", note_hi < 270.0, str(nb))

    all_items = [it for r in rows for it in r["items"]]
    x_lo = min(float(it["x0"]) for it in all_items)
    x_hi = max(float(it["x1"]) for it in all_items)
    gut = _ranges_from_numeric_gutters(rows, all_items, x_lo, x_hi)
    check("gutters 有列界", len(gut) >= 4, str(gut))

    # 必须存在覆盖「注释」字心的独立窄列
    note_cx = 238.0
    note_cols = [
        (lo, hi) for lo, hi in gut if lo <= note_cx < hi and (hi - lo) <= 60.0
    ]
    check("注释落在独立窄列", len(note_cols) == 1, str(gut))
    # 科目字与注释不得同列
    label_cx = 140.0
    same = any(lo <= label_cx < hi and lo <= note_cx < hi for lo, hi in gut)
    check("科目与注释不同列", not same, str(gut))


if __name__ == "__main__":
    test_note_column_not_swallowed_by_entity_scope()
    print("ALL OK")
