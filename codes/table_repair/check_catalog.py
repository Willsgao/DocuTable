# -*- coding: utf-8 -*-
"""表级全量检查目录：每项有 id、类别、默认处置阶段。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


# disposition: auto | llm | human | skip_non_table
DISPOSITION_AUTO = "auto"
DISPOSITION_LLM = "llm"
DISPOSITION_HUMAN = "human"
DISPOSITION_INFO = "info"


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    category: str
    title: str
    disposition: str  # auto / llm / human / info
    description: str


# 全量清单（检测必须覆盖；修复按 disposition）
CHECK_CATALOG: Tuple[CheckSpec, ...] = (
    # —— 身份与边界 ——
    CheckSpec("I01", "identity", "真表判定", DISPOSITION_INFO, "非表/页眉页脚应降级"),
    CheckSpec("I02", "identity", "表区切断/跨页续表", DISPOSITION_LLM, "无表头数据块或跨页候选"),
    CheckSpec("I03", "identity", "文表混杂", DISPOSITION_LLM, "表中夹说明段/脚注"),
    # —— 列角色 ——
    CheckSpec("R01", "roles", "列角色识别", DISPOSITION_AUTO, "序号列/标签列区/数值列区"),
    # —— 表头 ——
    CheckSpec("H01", "header", "表头带缺失", DISPOSITION_LLM, "顶部无表头带"),
    CheckSpec("H02", "header", "表头合并作用域", DISPOSITION_LLM, "合并表头丢失 colspan/覆盖列"),
    CheckSpec("H03", "header", "表头折行未归并", DISPOSITION_AUTO, "表头长文被拆多行"),
    CheckSpec("H04", "header", "多级表头列对齐", DISPOSITION_LLM, "表头与数据列错位"),
    CheckSpec("H05", "header", "表头碎片落入表体", DISPOSITION_AUTO, "表头残片进数据行"),
    CheckSpec("H06", "header", "重复表头", DISPOSITION_AUTO, "跨页/误重复表头行"),
    # —— 标签列（非死盯第0列）——
    CheckSpec("L01", "label", "标签列折行", DISPOSITION_AUTO, "科目名被拆多行"),
    CheckSpec("L02", "label", "标签列层级/父级作用域", DISPOSITION_AUTO, "父行丢失或与子项粘连"),
    CheckSpec("L03", "label", "层级粘连拆分", DISPOSITION_AUTO, "父+－子同一格"),
    CheckSpec("L04", "label", "序号与标签粘连", DISPOSITION_AUTO, "行号与科目糊在一格"),
    # —— 网格 ——
    CheckSpec("G01", "grid", "列数剧烈跳动", DISPOSITION_LLM, "数据区列数不稳定"),
    CheckSpec("G02", "grid", "列错位/串列", DISPOSITION_LLM, "金额落错列"),
    CheckSpec("G03", "grid", "连续空行空列", DISPOSITION_AUTO, "装饰空洞或误分割"),
    CheckSpec("G04", "grid", "鬼列", DISPOSITION_AUTO, "几乎无内容的多余列"),
    CheckSpec("G05", "grid", "单元格粘连", DISPOSITION_AUTO, "文本+金额同格"),
    # —— 数值与守恒 ——
    CheckSpec("N01", "numeric", "数值列形态异常", DISPOSITION_LLM, "金额列大量非数字"),
    CheckSpec("N02", "numeric", "疑似丢数/守恒失败", DISPOSITION_HUMAN, "禁止自动补数"),
    CheckSpec("N03", "numeric", "修复后空造金额", DISPOSITION_HUMAN, "校验拦截"),
    # —— 源一致性 ——
    CheckSpec("S01", "source", "源文本覆盖不足", DISPOSITION_HUMAN, "非空格缺源映射"),
)


CHECK_BY_ID: Dict[str, CheckSpec] = {c.check_id: c for c in CHECK_CATALOG}


def catalog_ids() -> List[str]:
    return [c.check_id for c in CHECK_CATALOG]


def specs_by_category() -> Dict[str, List[CheckSpec]]:
    out: Dict[str, List[CheckSpec]] = {}
    for c in CHECK_CATALOG:
        out.setdefault(c.category, []).append(c)
    return out
