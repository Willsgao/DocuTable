# -*- coding: utf-8 -*-
"""（四）（五）误并巨表：后表也有数值时仍必须在小节标题处拆开。"""

from codes.table_engine.split.fragment_rejoin import (
    _text_is_merge_spacer,
    _text_is_annual_subsection_caption,
)
from codes.table_engine.split.row_classify import row_is_annual_subsection_caption_row
from codes.table_engine.split.structure_split import (
    find_annual_subsection_caption_break,
    find_structure_break_row,
)
from codes.table_engine.models import TextBlock


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print(f"  OK  {name}")


def _glued_si_wu_tables():
    """模拟用户截图：前表贷款数据 +（五）+ 后表资本构成（后表含数值）。"""
    return [
        ["项目", "2024年12月31日", "2023年12月31日", "2022年12月31日"],
        ["吸收存款", "100", "90", "80"],
        ["活期公司存款", "10", "9", "8"],
        ["小计", "865707137", "1", "1"],
        ["吸收存款总额", "2", "2", "2"],
        ["发放贷款和垫款", "3", "3", "3"],
        ["发放贷款和垫款总额", "4", "4", "4"],
        ["（五）资本构成及变化情况", "", "", ""],
        ["单位：千元", "", "", ""],
        ["项目", "2024年12月31日", "2023年12月31日", "2022年12月31日"],
        ["", "并表", "非并表", "并表"],
        ["核心一级资本净额", "500", "400", "300"],
        ["一级资本净额", "600", "500", "400"],
    ]


if __name__ == "__main__":
    print("=== annual subsection force split ===")
    rows = _glued_si_wu_tables()
    check("识别（五）标题行", row_is_annual_subsection_caption_row(rows[7]))
    br = find_annual_subsection_caption_break(rows)
    check("小节切点=（五）行", br == 7)
    # 关键：后表有数值 → last_body 在后表，旧逻辑会漏切
    br2 = find_structure_break_row(rows)
    check("structure_break 强制在（五）", br2 == 7)
    br3 = find_structure_break_row(rows, region_continuation_merged=True)
    check("continuation 标记下仍强制拆", br3 == 7)

    block = TextBlock(page=16, y0=0, y1=10, text="（五）资本构成及变化情况")
    check("识别年报小节文本", _text_is_annual_subsection_caption(block))
    check("禁止 rejoin 拼回", _text_is_merge_spacer(block) is False)

    # 重复表头硬切 + 格式纠错预拆分
    from codes.format_corrector.structure_pre_split import (
        expand_tables_with_structure_split,
        split_table_data_by_structure,
    )
    from codes.table_engine.split.structure_split import find_repeated_annual_column_header_break

    dup_hdr = [
        ["项目", "2024年", "2023年", "本报告期末比上年度"],
        ["营业收入", "1", "2", "3"],
        ["归属于母公司股东的净利润", "1", "2", "3"],
        ["经营活动产生的现金流量净额", "1", "2", "3"],
        ["项目", "2024年", "2023年", "本报告期末比上年度"],
        ["12月31日", "12月31日", "", ""],
        ["资产总额", "1", "2", "3"],
        ["负债总额", "1", "2", "3"],
    ]
    check("重复表头切点", find_repeated_annual_column_header_break(dup_hdr) == 4)
    check("structure_break 强制重复表头", find_structure_break_row(dup_hdr) == 4)
    parts = split_table_data_by_structure(dup_hdr)
    check("预拆分两段", len(parts) == 2)
    check("后段以项目表头起", parts[1][0][0] == "项目")

    tables = [{"page": 14, "data": dup_hdr, "type": "table"}]
    expanded, notes = expand_tables_with_structure_split(tables)
    check("expand 成 2 张表", len(expanded) == 2)
    check("有拆分说明", len(notes) == 1)

    # （五）资本构成：自带列头 → 不得当缺表头续表去拼前表
    from codes.format_corrector.candidates import build_candidate_tasks
    from codes.format_corrector.conservation import (
        table_has_own_column_header,
        table_missing_header,
        table_starts_with_subsection_caption,
    )
    from codes.format_corrector.models import TaskType

    capital = {
        "page": 16,
        "type": "table",
        "data": [
            ["（五）资本构成及变化情况", "", "", ""],
            ["", "", "", "单位：千元"],
            ["项目", "2024年12月31日", "2023年12月31日", "2022年12月31日"],
            ["", "并表", "非并表", "并表"],
            ["核心一级资本净额", "1", "2", "3"],
        ],
        "_anomaly": {"header_missing": True},
        "table_category": "数据表(缺表头)",
    }
    loan = {
        "page": 16,
        "type": "table",
        "data": [
            ["项目", "2024年12月31日", "2023年12月31日", "2022年12月31日"],
            ["发放贷款和垫款总额", "1", "2", "3"],
        ],
    }
    check("识别（五）小节", table_starts_with_subsection_caption(capital))
    check("识别自有列头", table_has_own_column_header(capital))
    check("不算缺表头", table_missing_header(capital) is False)
    tasks = build_candidate_tasks([loan, capital], None)
    merges = [t for t in tasks if t.task_type == TaskType.CROSS_PAGE_MERGE]
    hdrs = [
        t for t in tasks
        if t.task_type == TaskType.HEADER_CROSS_PAGE and t.related_indices
    ]
    check("不生成合并任务", len(merges) == 0)
    check("不挂前表关联的缺表头任务", len(hdrs) == 0)

    print("ALL PASS")
