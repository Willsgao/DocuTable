# -*- coding: utf-8 -*-
"""format_corrector 单元测试（不依赖真实 PDF / LLM）。"""

from codes.format_corrector.conservation import (
    assert_no_content_loss,
    merge_tables_preserve,
    nonempty_multiset,
)
from codes.format_corrector.candidates import build_candidate_tasks
from codes.format_corrector.cross_page_merge import apply_merges, propose_merge
from codes.format_corrector.empty_split import apply_patches, propose_empty_split
from codes.format_corrector.engine import FormatCorrectorEngine
from codes.format_corrector.models import Confidence, FormatTask, TaskType


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print(f"  OK  {name}")


def test_conservation_merge_keeps_duplicates():
    prev = [["项目", "2024"], ["现金", "1"], ["现金", "1"]]
    nxt = [["项目", "2024"], ["存款", "2"]]
    merged, allowed, skip, note = merge_tables_preserve(prev, nxt)
    ok, detail = assert_no_content_loss(prev + nxt, merged, allowed_remove=allowed)
    check("合并守恒通过", ok)
    check("跳过重复表头1行", skip == 1)
    # 重复「现金」「1」仍在
    ms = nonempty_multiset(merged)
    check("重复现金未删", ms.get("现金", 0) >= 2)
    check("重复数值未删", ms.get("1", 0) >= 2)


def test_refuse_delete_duplicate_data():
    before = [["A"], ["A"]]
    after = [["A"]]
    ok, _ = assert_no_content_loss(before, after)
    check("删重复数据行应失败", not ok)


def test_empty_col_drop():
    data = [["a", "", "b"], ["1", "", "2"]]
    trial, ok, detail = apply_patches(data, [{"action": "drop_empty_columns", "cols": [1]}])
    check("删空列守恒", ok)
    check("列数变2", max(len(r) for r in trial) == 2)
    check("内容仍在", nonempty_multiset(trial) == nonempty_multiset([["a", "b"], ["1", "2"]]))


def test_glue_split():
    data = [["项目", "12.3%收入增加"]]
    trial, ok, _ = apply_patches(
        data,
        [{
            "action": "split_cell_horizontal",
            "row": 0,
            "col": 1,
            "parts": ["12.3%", "收入增加"],
            "original": "12.3%收入增加",
        }],
    )
    check("粘连拆分守恒", ok)
    check("拆出两段", "12.3%" in trial[0] and "收入增加" in trial[0])


def test_candidates_and_merge_apply():
    tables = [
        {"page": 1, "data": [["项目", "2024年"], ["资产", "100"]], "y0": 0, "y1": 100},
        {
            "page": 2,
            "data": [["负债", "50"], ["合计", "150"]],
            "y0": 0,
            "y1": 80,
            "_anomaly": {"header_missing": True, "needs_review": True},
            "table_category": "数据表(缺表头)",
        },
    ]
    tasks = build_candidate_tasks(tables, liteparse_data=None)
    types = {t.task_type for t in tasks}
    check("有缺表头任务", TaskType.HEADER_CROSS_PAGE in types)
    check("有合并任务", TaskType.CROSS_PAGE_MERGE in types)
    # location 按文档顺序
    hdr = next(t for t in tasks if t.task_type == TaskType.HEADER_CROSS_PAGE)
    check("location 为相邻页", (hdr.evidence or {}).get("location") == "P1_1+P2_1")

    merge_tasks = [t for t in tasks if t.task_type == TaskType.CROSS_PAGE_MERGE]
    for t in merge_tasks:
        t.proposal["auto_apply"] = True
        t.confidence = Confidence.HIGH
        propose_merge(t, tables)
    new_tables, updated, notes = apply_merges(tables, merge_tasks, only_auto=True)
    check("合并后前表有行", len(new_tables[0]["data"]) >= 4)
    check("后表隐藏", new_tables[1].get("_format_hidden") is True)
    check("后表快照保全", bool(new_tables[1].get("_format_merged_snapshot")))


def test_no_far_page_pairing():
    """绝不能把 P4 和 P2 当成跨页对。"""
    from codes.format_corrector.candidates import find_prev_adjacent_table, build_candidate_tasks

    tables = [
        {"page": 2, "type": "table", "data": [["a", "b"], ["1", "2"]]},
        {"page": 2, "type": "text", "data": "（一）短期标题"},  # 短表头文本可跳过
        {"page": 4, "type": "table", "data": [["3", "4"]],  # 缺表头
         "_anomaly": {"header_missing": True}, "table_category": "数据表(缺表头)"},
    ]
    prev = find_prev_adjacent_table(tables, 2)
    check("P4 不应关联到 P2", prev is None)

    tasks = build_candidate_tasks(tables, None)
    hdr = [t for t in tasks if t.task_type == TaskType.HEADER_CROSS_PAGE]
    check("有缺表头任务", len(hdr) == 1)
    check("不挂远页 related", hdr[0].related_indices == [])
    loc = (hdr[0].evidence or {}).get("location", "")
    check("location 不含跨远页", "+" not in loc or "P2" not in loc)

    merges = [t for t in tasks if t.task_type == TaskType.CROSS_PAGE_MERGE]
    check("不生成 P2-P4 合并", len(merges) == 0)


def test_engine_scan():
    tables = [
        {"page": 1, "data": [["a", "b"], ["1", "2"]]},
        {"page": 1, "data": [["x", "", "y"], ["", "", ""], ["", "", ""], ["3", "", "4"]],
         "_anomaly": {"header_missing": False}},
    ]
    eng = FormatCorrectorEngine(use_llm=False, auto_apply=False)
    report = eng.run_on_tables(tables, None)
    check("产出任务", report.summary.get("task_count", 0) >= 1)


def test_bidirectional_merge_conflict():
    """中间表同时挂前后合并 → 弱侧否决，强侧也不自动应用。"""
    tables = [
        {
            "page": 10,
            "type": "table",
            "data": [
                ["项目", "2024年12月31日", "2023年12月31日"],
                ["资产", "1", "2"],
                ["合计", "3", "4"],
            ],
        },
        {
            # 缺表头续表信号强 → 与前表合并应更强
            "page": 11,
            "type": "table",
            "data": [["续行甲", "5", "6"], ["续行乙", "7", "8"]],
            "_anomaly": {"header_missing": True},
            "table_category": "数据表(缺表头)",
        },
        {
            # 首行像列头但不算「项目+报告期」→ 不缺表头，仅弱跨页信号
            "page": 12,
            "type": "table",
            "data": [["科目", "金额", "备注"], ["另一张表", "9", "10"]],
        },
    ]
    tasks = build_candidate_tasks(tables, None)
    merges = [t for t in tasks if t.task_type == TaskType.CROSS_PAGE_MERGE]
    pairs = {
        (t.table_index, int(t.related_indices[0]))
        for t in merges
        if t.related_indices
    }
    # 中间表#1 双向冲突：弱侧 1+2 应被拿掉，强侧 0+1 可保留但不 auto_apply
    check("保留前侧合并 0+1", (0, 1) in pairs)
    check("否决后侧合并 1+2", (1, 2) not in pairs)
    kept = next(t for t in merges if t.related_indices == [1] and t.table_index == 0)
    check("冲突标记", bool((kept.evidence or {}).get("bidirectional_merge_conflict")))
    check("冲突后禁止自动合并", (kept.proposal or {}).get("auto_apply") is False)

    # 两侧都强时：不断开，但都降权且禁止自动合并
    strong = [
        {
            "page": 20,
            "type": "table",
            "data": [["项目", "2024", "2023"], ["a", "1", "2"]],
        },
        {
            "page": 21,
            "type": "table",
            "data": [["b", "3", "4"]],
            "_anomaly": {"header_missing": True},
            "table_category": "数据表(缺表头)",
        },
        {
            "page": 22,
            "type": "table",
            "data": [["c", "5", "6"]],
            "_anomaly": {"header_missing": True},
            "table_category": "数据表(缺表头)",
        },
    ]
    tasks2 = build_candidate_tasks(strong, None)
    merges2 = [t for t in tasks2 if t.task_type == TaskType.CROSS_PAGE_MERGE]
    check("两侧都强时仍保留两条边", len(merges2) == 2)
    for t in merges2:
        check("强冲突有标记", bool((t.evidence or {}).get("bidirectional_merge_conflict")))
        check("强冲突禁止自动", (t.proposal or {}).get("auto_apply") is False)
        check("强冲突已降权", t.confidence != Confidence.HIGH)


if __name__ == "__main__":
    print("=== format_corrector tests ===")
    test_conservation_merge_keeps_duplicates()
    test_refuse_delete_duplicate_data()
    test_empty_col_drop()
    test_glue_split()
    test_candidates_and_merge_apply()
    test_no_far_page_pairing()
    test_engine_scan()
    test_bidirectional_merge_conflict()
    print("ALL PASS")
