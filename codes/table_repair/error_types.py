# -*- coding: utf-8 -*-
"""
表格错误类型与纠正原则（专用功能区）
====================================

本模块是「有什么错、先怎么修、LLM 做什么、如何验收」的单一事实来源。
检测清单（check_catalog）与流水线（pipeline / typed_repair）均应对齐此处。

总原则（不可破）：
1. 先锁定数据区（连续行 + 同列同型），以数据列界为真理。
2. 最底层表头必须与数据列一一对齐；上层表头只表达 span/覆盖。
3. 禁止补造金额；非空文本不得无故丢失。
4. 能规则修的不上 LLM；LLM 按错误类型对症下药，禁止无约束整表乱改。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 全局纠正原则（给代码与 LLM 共用）
# ---------------------------------------------------------------------------

GLOBAL_PRINCIPLES: Tuple[str, ...] = (
    "数据区列界是真理：先找连续、同列同型的数据行，再回头修表头。",
    "最底层表头列数与位置必须与数据列一一对齐。",
    "上层表头只描述合并覆盖（span），不得破坏底层对齐。",
    "同一数据列格式应一致（金额列几乎全是数，标签列几乎全是文）。",
    "禁止补造、猜测原表中不存在的金额；不确定则保持原单元格。",
    "标题/报告名/页眉不是表体，应剔除或降为 caption。",
    "标签列不一定是第 0 列：先识别序号列 / 标签列区 / 数值列区。",
)

# disposition: auto | llm | human
# llm_task: 专项任务名（typed_repair 分发用）


@dataclass(frozen=True)
class ErrorTypeSpec:
    error_id: str
    category: str
    title: str
    disposition: str
    check_ids: Tuple[str, ...]
    problem_tags: Tuple[str, ...]
    detect: str
    rule_fix: str
    llm_task: str
    llm_instruction: str
    accept: str

    def to_dict(self) -> Dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 错误类型目录
# ---------------------------------------------------------------------------

ERROR_TYPES: Tuple[ErrorTypeSpec, ...] = (
    # —— 表头 ——
    ErrorTypeSpec(
        error_id="H_TITLE",
        category="header",
        title="标题/报告名误入表",
        disposition="auto",
        check_ids=("H05", "I03"),
        problem_tags=("merge_lost",),
        detect="顶行无金额、含「报告/资本管理/信息披露」等，且与数据列不对齐",
        rule_fix="剔除为表外标题/caption，不进入数据区",
        llm_task="fix_strip_titles",
        llm_instruction="只指出哪些行是标题/报告名应删除；不要改任何金额单元格。",
        accept="删除后底层表头仍可对齐数据列；金额多重集不变",
    ),
    ErrorTypeSpec(
        error_id="H_UNIT",
        category="header",
        title="单位/报告期行错位",
        disposition="llm",
        check_ids=("H04", "H01"),
        problem_tags=("misalignment", "merge_lost"),
        detect="含人民币/百万元/年月日等，落在错误列",
        rule_fix="尽量归入表头带固定层",
        llm_task="fix_header_meta_rows",
        llm_instruction="只把单位行、报告期行归位到表头带正确层/列；禁止改数据区金额。",
        accept="元信息行不破坏数据列对齐",
    ),
    ErrorTypeSpec(
        error_id="H_FRAG",
        category="header",
        title="列标碎片（如 a / b c）",
        disposition="llm",
        check_ids=("H03", "H04", "H02"),
        problem_tags=("merge_lost", "misalignment"),
        detect="表头区短字母/编号碎片，列数与数据列不一致",
        rule_fix="若能按数据列数确定性拆开则规则拆",
        llm_task="fix_header_fragments",
        llm_instruction=(
            "只修复列标行：输出与数据列数相等的底层列标；"
            "把粘连的「b c」拆开；不要改数据行。"
        ),
        accept="底层列标行长度 == 数据列数",
    ),
    ErrorTypeSpec(
        error_id="H_SPAN",
        category="header",
        title="合并表头丢失作用范围",
        disposition="llm",
        check_ids=("H02", "H04"),
        problem_tags=("merge_lost",),
        detect="上层短标签 + 空单元格；子头未落在父头覆盖列",
        rule_fix="有 bbox 时用覆盖列集推 colspan；导出对齐数据列",
        llm_task="fix_header_span",
        llm_instruction=(
            "在「底层表头已与数据列对齐」的前提下，"
            "只标注/恢复上层表头的列覆盖（可用重复填充表达 span）；禁止改金额。"
        ),
        accept="上层覆盖列并集 == 对应子列；数据区不变",
    ),
    ErrorTypeSpec(
        error_id="H_WRAP",
        category="header",
        title="表头长文被拆多行",
        disposition="auto",
        check_ids=("H03",),
        problem_tags=("wrap_split", "merge_lost"),
        detect="表头带内同列短续写、无金额",
        rule_fix="表头带同列纵合并后再对齐数据列",
        llm_task="fix_header_wrap",
        llm_instruction="只合并被拆开的同一表头单元格；不要合并不同列的表头。",
        accept="表头带行数合理下降；底层仍对齐数据列",
    ),
    ErrorTypeSpec(
        error_id="H_ALIGN",
        category="header",
        title="底层表头与数据列不对齐",
        disposition="llm",
        check_ids=("H04", "G01", "G02"),
        problem_tags=("misalignment", "merge_lost"),
        detect="底层表头列数≠数据列数，或错位",
        rule_fix="以数据列为准平移/拆格表头",
        llm_task="fix_header_align",
        llm_instruction=(
            "核心任务：让最底层表头与数据列一一对齐。"
            "以数据区列数为真理；只改表头区，禁止改数据区金额与科目数值。"
        ),
        accept="底层表头列数 == 数据列数；抽查同列同型",
    ),
    # —— 标签列 ——
    ErrorTypeSpec(
        error_id="L_WRAP",
        category="label",
        title="标签列折行",
        disposition="auto",
        check_ids=("L01",),
        problem_tags=("wrap_split",),
        detect="下行标签有文、金额空、非「－」子项",
        rule_fix="在 primary_label_col 上合并真续写",
        llm_task="fix_label_wrap",
        llm_instruction="只合并标签列续行；禁止把父级与「－」子项合并；禁止改金额列。",
        accept="金额行数不无故减少；－子项仍独立",
    ),
    ErrorTypeSpec(
        error_id="L_GLUE",
        category="label",
        title="父级与－子项粘连",
        disposition="auto",
        check_ids=("L02", "L03"),
        problem_tags=("hierarchy_lost", "wrap_split"),
        detect="标签可拆出父+－子",
        rule_fix="拆行，金额归子项",
        llm_task="fix_label_hierarchy",
        llm_instruction="只拆开粘连的层级标签；父行可无金额；子项保留数值。",
        accept="父行独立；子项有金额",
    ),
    ErrorTypeSpec(
        error_id="L_SERIAL",
        category="label",
        title="序号与标签粘连",
        disposition="auto",
        check_ids=("L04", "R01"),
        problem_tags=("cell_glue",),
        detect="标签列带行号前缀且无独立序号列",
        rule_fix="识别列角色后拆分或保留业务习惯",
        llm_task="fix_label_serial",
        llm_instruction="在已有列角色前提下，只处理序号与科目粘连；勿改金额。",
        accept="序号列或标签列语义清晰",
    ),
    # —— 网格 ——
    ErrorTypeSpec(
        error_id="G_SHIFT",
        category="grid",
        title="列错位/串列",
        disposition="llm",
        check_ids=("G02", "N01"),
        problem_tags=("misalignment",),
        detect="金额进标签列或同列同型被破坏",
        rule_fix="按坐标重投列（有 bbox 时）",
        llm_task="fix_column_shift",
        llm_instruction="只把错位单元格归到正确列；以同列同型为准；禁止发明数字。",
        accept="数值列同型恢复；金额多重集不新增",
    ),
    ErrorTypeSpec(
        error_id="G_GLUE",
        category="grid",
        title="单元格粘连",
        disposition="auto",
        check_ids=("G05",),
        problem_tags=("cell_glue",),
        detect="同格中文+金额",
        rule_fix="glue 规则拆到标签列与金额列",
        llm_task="fix_cell_glue",
        llm_instruction="只拆开粘连单元格到正确列；禁止改数字内容。",
        accept="粘连格消失；金额仍可解析",
    ),
    ErrorTypeSpec(
        error_id="G_EMPTY",
        category="grid",
        title="连续空行/空列",
        disposition="auto",
        check_ids=("G03", "G04"),
        problem_tags=(),
        detect="连续全空行或全空列",
        rule_fix="源确认无字则压缩；有源则标丢数",
        llm_task="",
        llm_instruction="",
        accept="不删有源内容",
    ),
    ErrorTypeSpec(
        error_id="G_MIX",
        category="grid",
        title="文表混杂",
        disposition="llm",
        check_ids=("I03",),
        problem_tags=("misalignment",),
        detect="表中部长叙述无金额",
        rule_fix="标记/拆出文本行",
        llm_task="fix_text_in_table",
        llm_instruction="只标出应移出表外的说明行；不要删除金额行；不要改数。",
        accept="说明行被标出；数据行保留",
    ),
    # —— 边界（格式纠错主场，此处登记原则）——
    ErrorTypeSpec(
        error_id="B_CROSS",
        category="boundary",
        title="跨页/缺表头/误分割",
        disposition="llm",
        check_ids=("I02", "H01"),
        problem_tags=("merge_lost",),
        detect="缺表头、邻页续表、空行误拆",
        rule_fix="格式纠错候选：合并/空分割",
        llm_task="judge_cross_page_merge",
        llm_instruction="只判断是否合并及去掉哪段重复表头；禁止改金额。",
        accept="合并后底层表头对齐；守恒通过",
    ),
    # —— 禁止自动补数 ——
    ErrorTypeSpec(
        error_id="N_LOSS",
        category="numeric",
        title="疑似丢数/守恒失败",
        disposition="human",
        check_ids=("N02", "S01", "N03"),
        problem_tags=("data_loss",),
        detect="守恒失败、源覆盖不足、或修复空造金额",
        rule_fix="禁止自动补数；标 human_needed",
        llm_task="",
        llm_instruction="禁止补数；最多指出可能缺哪一列/行。",
        accept="未经人工不得标 OK",
    ),
)


ERROR_BY_ID: Dict[str, ErrorTypeSpec] = {e.error_id: e for e in ERROR_TYPES}


def list_error_types(*, category: Optional[str] = None) -> List[ErrorTypeSpec]:
    if not category:
        return list(ERROR_TYPES)
    return [e for e in ERROR_TYPES if e.category == category]


def map_check_id_to_errors(check_id: str) -> List[ErrorTypeSpec]:
    cid = str(check_id or "")
    return [e for e in ERROR_TYPES if cid in e.check_ids]


def map_problem_tag_to_errors(tag: str) -> List[ErrorTypeSpec]:
    t = str(tag or "")
    return [e for e in ERROR_TYPES if t in e.problem_tags]


def errors_from_checklist_findings(
    findings: Sequence[Dict],
) -> List[ErrorTypeSpec]:
    """从 checklist findings 收集未通过项对应的错误类型（去重保序）。"""
    seen = set()
    out: List[ErrorTypeSpec] = []
    for f in findings or []:
        if f.get("passed") or f.get("fix_status") in ("ok", "fixed", "na"):
            continue
        for e in map_check_id_to_errors(str(f.get("check_id") or "")):
            if e.error_id not in seen:
                seen.add(e.error_id)
                out.append(e)
    return out


def partition_errors(
    errors: Sequence[ErrorTypeSpec],
) -> Dict[str, List[ErrorTypeSpec]]:
    """按 disposition 分组。"""
    buckets = {"auto": [], "llm": [], "human": []}
    for e in errors:
        buckets.setdefault(e.disposition, []).append(e)
    return buckets


def build_typed_llm_instructions(errors: Sequence[ErrorTypeSpec]) -> str:
    """生成按错误类型分段的 LLM 指令（一次调用内分任务）。"""
    lines = [
        "你正在按「错误类型」修复表格结构，不是自由改写。",
        "全局原则：",
    ]
    for p in GLOBAL_PRINCIPLES:
        lines.append(f"- {p}")
    llm_errors = [e for e in errors if e.disposition == "llm" and e.llm_task]
    if not llm_errors:
        lines.append("当前无需要 LLM 的错误类型。")
        return "\n".join(lines)
    lines.append("")
    lines.append("请按下列任务依次处理（只做列出的事）：")
    for i, e in enumerate(llm_errors, 1):
        lines.append(f"{i}. [{e.error_id}/{e.llm_task}] {e.title}")
        lines.append(f"   要求：{e.llm_instruction}")
        lines.append(f"   验收：{e.accept}")
    lines.append("")
    lines.append(
        "输出修复后的完整二维表。数据区金额必须来自原表；"
        "优先保证「最底层表头与数据列对齐」。"
    )
    return "\n".join(lines)


def catalog_as_markdown() -> str:
    """供文档/调试导出。"""
    parts = ["# 表格错误类型与纠正原则", "", "## 全局原则", ""]
    for p in GLOBAL_PRINCIPLES:
        parts.append(f"- {p}")
    parts.append("")
    cur = None
    for e in ERROR_TYPES:
        if e.category != cur:
            cur = e.category
            parts.append(f"## 类别：{cur}")
            parts.append("")
        parts.append(f"### {e.error_id} {e.title}")
        parts.append(f"- 处置：`{e.disposition}` / LLM任务：`{e.llm_task or '—'}`")
        parts.append(f"- 检查项：{', '.join(e.check_ids) or '—'}")
        parts.append(f"- 识别：{e.detect}")
        parts.append(f"- 规则：{e.rule_fix}")
        parts.append(f"- LLM：{e.llm_instruction or '—'}")
        parts.append(f"- 验收：{e.accept}")
        parts.append("")
    return "\n".join(parts)
