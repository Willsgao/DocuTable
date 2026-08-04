# -*- coding: utf-8 -*-
"""统一 LLM 修复门面：按 problem_tags 构造上下文 → 调用现有 LLM → 校验 → 写回/升级人工。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from codes.table_repair.problem_model import build_problem_report
from codes.table_repair.validator import normalize_grid, validate_repair

# 允许自动尝试 LLM 的标签（data_loss 永远不走自动补数）
_LLM_ELIGIBLE_TAGS = frozenset({
    "wrap_split",
    "hierarchy_lost",
    "cell_glue",
    "misalignment",
    "merge_lost",
})

_TAG_HINTS = {
    "wrap_split": "优先合并被错误拆开的同一标签折行；不要把层级父项与「－」分项合并。",
    "hierarchy_lost": (
        "保持科目层级：父级科目单独成行（可无金额），"
        "「－债券」「－权益工具」等分项各自成行并保留其数值。"
        "禁止把上层与第一个分项粘成一行。"
    ),
    "cell_glue": "拆开同一单元格内粘连的「文本+金额」或双表头。",
    "misalignment": "按表头语义纠正错位单元格；不要发明表格中不存在的数字。",
    "merge_lost": "恢复表头跨列/纵并的逻辑层次，用重复填充或标注层级，勿改金额。",
    "data_loss": "禁止补造任何数字；若缺数请保持原样并在说明中指出。",
}


@dataclass
class FacadeResult:
    success: bool = False
    applied: bool = False
    repaired_table: List[List[str]] = field(default_factory=list)
    confidence: float = 0.0
    problem_tags: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)
    repairs_applied: List[Any] = field(default_factory=list)
    reasoning_summary: str = ""
    llm_error: str = ""
    escalate_human: bool = False
    report_text: str = ""
    # 兼容旧 UI：挂载底层 RepairResult
    raw_result: Any = None

    @property
    def llm_raw_compatible(self) -> Any:
        """供 UI 当作旧 RepairResult 使用的薄包装。"""
        return self


def build_llm_context(
    *,
    problem_tags: Sequence[str],
    base_context: str = "",
    evidence: Optional[Dict[str, Any]] = None,
) -> str:
    tags = [t for t in problem_tags if t]
    lines = [
        "你正在修复银行年报/财务报表提取表。",
        "硬性约束：禁止补造、猜测任何原表中不存在的金额数字；不确定则保持原单元格。",
    ]
    if tags:
        lines.append("已识别问题标签: " + ", ".join(tags))
        for t in tags:
            hint = _TAG_HINTS.get(t)
            if hint:
                lines.append(f"- [{t}] {hint}")
    if evidence:
        rules = evidence.get("rule_ids") or []
        if rules:
            lines.append("质检 rule_ids: " + ", ".join(str(r) for r in rules))
    if base_context:
        lines.append("补充上下文: " + str(base_context).strip())
    return "\n".join(lines)


def repair_with_facade(
    data: Sequence[Sequence[Any]],
    *,
    problem_tags: Optional[Sequence[str]] = None,
    context: str = "",
    evidence: Optional[Dict[str, Any]] = None,
    apply_min_confidence: float = 0.55,
    api_key: Optional[str] = None,
) -> FacadeResult:
    """对二维表跑 LLM 修复并校验。不直接改 table dict。"""
    before = normalize_grid(data)
    tags = [t for t in (problem_tags or []) if t in _LLM_ELIGIBLE_TAGS or t == "data_loss"]

    out = FacadeResult(problem_tags=list(tags or problem_tags or []))

    if "data_loss" in (problem_tags or []) and not any(
        t in _LLM_ELIGIBLE_TAGS for t in (problem_tags or [])
    ):
        out.escalate_human = True
        out.llm_error = "data_loss 仅允许人工处理，不调用 LLM 补数"
        return out

    ctx = build_llm_context(
        problem_tags=tags or (problem_tags or []),
        base_context=context,
        evidence=evidence,
    )

    try:
        from codes.table_validator.llm_table_repair import (
            generate_repair_report,
            repair_table_with_llm,
        )
        from codes.pdf_extractor.utils import load_config
    except Exception as exc:
        out.llm_error = f"无法加载 llm_table_repair: {exc}"
        out.escalate_human = True
        return out

    # 仅使用「配置」页保存的 DeepSeek 设置，禁止静默使用环境变量替代
    cfg = load_config()
    key = (api_key or cfg.get("deepseek_api_key") or "").strip()
    endpoint = str(cfg.get("deepseek_endpoint") or "api.deepseek.com").strip()
    model = str(cfg.get("deepseek_model") or "deepseek-chat").strip()
    if not key:
        out.llm_error = (
            "未配置 DeepSeek API Key。请在「配置」页填写并点击「保存配置」。"
        )
        out.escalate_human = True
        return out

    raw = repair_table_with_llm(
        before,
        context=ctx,
        api_key=key,
        endpoint=endpoint,
        model=model,
    )
    out.raw_result = raw
    out.confidence = float(getattr(raw, "overall_confidence", 0.0) or 0.0)
    out.repairs_applied = list(getattr(raw, "repairs_applied", None) or [])
    out.reasoning_summary = str(getattr(raw, "reasoning_summary", "") or "")

    if not getattr(raw, "success", False):
        out.llm_error = str(getattr(raw, "llm_error", "") or "LLM 修复失败")
        out.escalate_human = True
        return out

    after = normalize_grid(getattr(raw, "repaired_table", None) or [])
    ok, reasons = validate_repair(
        before,
        after,
        min_confidence=apply_min_confidence,
        confidence=out.confidence,
    )
    out.repaired_table = after
    out.validation_errors = reasons
    try:
        out.report_text = generate_repair_report(raw)
    except Exception:
        out.report_text = out.reasoning_summary

    if not ok:
        out.success = False
        out.applied = False
        out.escalate_human = True
        out.llm_error = "校验未通过: " + "; ".join(reasons)
        return out

    out.success = True
    out.applied = False  # 由调用方决定是否写回
    return out


def repair_table_dict_with_facade(
    table: Dict[str, Any],
    *,
    context: str = "",
    apply: bool = True,
    api_key: Optional[str] = None,
) -> FacadeResult:
    """对 legacy 表 dict 修复；apply=True 时写回 data 并更新 repair_status。"""
    report = table.get("_problem_report") or build_problem_report(table).to_dict()
    tags = list(report.get("problem_tags") or [])
    if not tags and table.get("repair_status") == "llm_candidate":
        tags = ["misalignment"]

    evidence = dict(report.get("evidence") or {})
    evidence["rule_ids"] = report.get("rule_ids") or (
        (table.get("_anomaly") or {}).get("rule_ids") or []
    )

    data = table.get("data") or []
    result = repair_with_facade(
        data,
        problem_tags=tags,
        context=context or str(table.get("caption") or ""),
        evidence=evidence,
        api_key=api_key,
    )

    from codes.table_repair.human_queue import store_llm_proposal

    # 存完整前后表，供人工队列审阅
    store_llm_proposal(
        table,
        before_data=data,
        repaired_table=result.repaired_table,
        success=result.success and bool(result.repaired_table),
        confidence=result.confidence,
        validation_errors=result.validation_errors,
        llm_error=result.llm_error,
        problem_tags=result.problem_tags,
        reasoning_summary=result.reasoning_summary,
        report_text=result.report_text,
        set_status=True,
    )

    if result.escalate_human or not result.success:
        return result

    if apply and result.repaired_table:
        from codes.table_repair.human_queue import apply_proposal_to_table

        apply_proposal_to_table(table, result.repaired_table, status="llm_applied")
        result.applied = True

    return result


class _UiCompatResult:
    """让 UI 旧回调仍可读 success / repaired_table / llm_error 等字段。"""

    def __init__(self, facade: FacadeResult):
        self._f = facade
        self.success = facade.success and bool(facade.repaired_table)
        self.repaired_table = facade.repaired_table
        self.llm_error = facade.llm_error
        self.overall_confidence = facade.confidence
        self.repairs_applied = facade.repairs_applied
        self.reasoning_summary = facade.reasoning_summary
        self.original_row_count = 0
        self.repaired_row_count = len(facade.repaired_table)
        self._report = facade.report_text
        if facade.raw_result is not None:
            raw = facade.raw_result
            self.original_row_count = getattr(raw, "original_row_count", 0)
            if not self._report:
                try:
                    from codes.table_validator.llm_table_repair import (
                        generate_repair_report,
                    )
                    self._report = generate_repair_report(raw)
                except Exception:
                    pass


def repair_for_ui(
    table_data: Sequence[Sequence[Any]],
    *,
    context: str = "",
    problem_tags: Optional[Sequence[str]] = None,
    table: Optional[Dict[str, Any]] = None,
) -> Any:
    """UI「LLM 结构修复」入口：返回兼容旧 RepairResult 的对象。"""
    tags = list(problem_tags or [])
    evidence = None
    if table is not None:
        pr = table.get("_problem_report") or {}
        if not tags:
            tags = list(pr.get("problem_tags") or [])
        evidence = {
            "rule_ids": pr.get("rule_ids")
            or ((table.get("_anomaly") or {}).get("rule_ids") or []),
        }
    result = repair_with_facade(
        table_data,
        problem_tags=tags,
        context=context,
        evidence=evidence,
    )
    if table is not None:
        from codes.table_repair.human_queue import store_llm_proposal

        store_llm_proposal(
            table,
            before_data=table_data,
            repaired_table=result.repaired_table,
            success=result.success and bool(result.repaired_table),
            confidence=result.confidence,
            validation_errors=result.validation_errors,
            llm_error=result.llm_error,
            problem_tags=result.problem_tags or tags,
            reasoning_summary=result.reasoning_summary,
            report_text=result.report_text,
            set_status=True,
        )
    compat = _UiCompatResult(result)
    if not result.success and result.validation_errors:
        compat.llm_error = result.llm_error or (
            "校验未通过: " + "; ".join(result.validation_errors)
        )
        compat.success = False
    return compat
