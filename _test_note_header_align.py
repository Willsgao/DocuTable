# -*- coding: utf-8 -*-
"""注释列不得被 header_align 并进金额列。"""

from __future__ import annotations

from codes.reconstruct.grid_nucleus.header_align import align_header_to_body_columns


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    extra = f" ({detail})" if detail and not ok else ""
    print(f"  [{status}] {name}{extra}")
    if not ok:
        raise AssertionError(name)


def test_note_header_not_merged_into_non_interest() -> None:
    print("--- header_align 保留注释列 ---")
    data = [
        ["", "注释", "不计息", "3个月以内", "合计"],
        ["现金", "", "106,312", "2,931,368", "3,038,074"],
        ["发放贷款和垫款", "(i)", "30,346", "13,077,878", "26,517,673"],
        ["投资", "(ii)", "320,745", "907,750", "12,356,606"],
        ["其他", "", "100", "–", "100"],
    ]
    out, _, metrics = align_header_to_body_columns(data)
    hdr = out[0]
    check("注释仍独立", "注释" in hdr and hdr[hdr.index("注释")] == "注释", str(hdr))
    check("不计息仍独立", "不计息" in hdr, str(hdr))
    check("未粘成不计息 注释", "不计息 注释" not in hdr and "注释 不计息" not in hdr, str(hdr))
    check("注释在不计息左侧", hdr.index("注释") < hdr.index("不计息"), str(hdr))
    check("(i) 仍在注释列", out[2][hdr.index("注释")] == "(i)", str(out[2]))
    actions = metrics.get("bottom_actions") or []
    check(
        "有 note keep 或无 1->2 merge",
        any("note_keep" in a for a in actions)
        or not any(a.startswith("bottom:1->") for a in actions),
        str(actions),
    )


if __name__ == "__main__":
    test_note_header_not_merged_into_non_interest()
    print("ALL OK")
