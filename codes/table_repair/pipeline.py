# -*- coding: utf-8 -*-
"""表级修复流水线：全量检查 → Phase A 规则修 → 复检标注 → 可选 Phase B 单次 LLM。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from codes.table_repair.checklist import run_full_checklist
from codes.table_repair.column_roles import infer_column_roles
from codes.table_repair.problem_model import build_problem_report


def _update_finding_status(checklist: Dict[str, Any], check_id: str, status: str, msg: str = ""):
    for f in checklist.get("findings") or []:
        if f.get("check_id") == check_id:
            f["fix_status"] = status
            if msg:
                f["message"] = (f.get("message") or "") + " | " + msg
            if status == "fixed":
                f["passed"] = True
            break


def _recompute_summary(checklist: Dict[str, Any]) -> None:
    findings = checklist.get("findings") or []
    failed = [
        f for f in findings
        if not f.get("passed") and f.get("fix_status") not in ("na", "fixed", "ok")
    ]
    # 已 fixed 的算通过
    for f in findings:
        if f.get("fix_status") == "fixed":
            f["passed"] = True
    failed = [
        f for f in findings
        if not f.get("passed") and f.get("fix_status") not in ("na",)
    ]
    checklist["summary"] = {
        "total_checks": len(findings),
        "failed": len(failed),
        "needs_auto": sum(1 for f in failed if f.get("disposition") == "auto"),
        "needs_llm": sum(1 for f in failed if f.get("disposition") == "llm"),
        "needs_human": sum(1 for f in failed if f.get("disposition") == "human"),
        "failed_ids": [f.get("check_id") for f in failed],
        "fixed_ids": [
            f.get("check_id") for f in findings if f.get("fix_status") == "fixed"
        ],
    }


def _phase_a_fixes(table: Dict[str, Any], checklist: Dict[str, Any]) -> List[str]:
    """对 disposition=auto 且未通过的项尝试规则修复（对齐 error_types 原则）。"""
    notes: List[str] = []
    roles = checklist.get("column_roles") or {}
    label_col = int(roles.get("primary_label_col") or 0)
    failed_auto = {
        f["check_id"]
        for f in (checklist.get("findings") or [])
        if (not f.get("passed")) and f.get("disposition") == "auto"
    }
    try:
        from codes.table_repair.error_types import errors_from_checklist_findings
        from codes.table_repair.typed_repair import apply_typed_rule_fixes

        errs = errors_from_checklist_findings(checklist.get("findings") or [])
        grid, tnotes, fixed = apply_typed_rule_fixes(table.get("data") or [], errs)
        if tnotes:
            table["data"] = grid
            table["rows"] = len(grid)
            table["cols"] = max((len(r) for r in grid), default=0)
            notes.extend(tnotes)
            if "H_TITLE" in fixed:
                _update_finding_status(checklist, "H05", "fixed", "strip_title")
                _update_finding_status(checklist, "I03", "fixed", "strip_title")
    except Exception as exc:
        notes.append(f"typed_rule_error: {exc}")

    if not failed_auto and not notes:
        return notes

    if "G05" in failed_auto and not table.get("_glue_repaired"):
        try:
            from codes.v2_steps.table_glue_repair import repair_table_numeric_text_glue

            gnotes = repair_table_numeric_text_glue(table)
            if gnotes:
                notes.extend(gnotes)
                _update_finding_status(checklist, "G05", "fixed", "glue_repair")
        except Exception as exc:
            notes.append(f"glue_repair_error: {exc}")
            _update_finding_status(checklist, "G05", "needs_llm", str(exc))

    if failed_auto & {"L01", "L02", "L03", "H03", "L04"}:
        try:
            from codes.table_repair.wrap_repair import repair_table_wrap_split

            wnotes = repair_table_wrap_split(table, label_col=label_col)
            if wnotes:
                notes.extend(wnotes)
                for cid in ("L01", "L02", "L03", "H03"):
                    if cid in failed_auto:
                        _update_finding_status(checklist, cid, "fixed", "wrap/hierarchy")
        except Exception as exc:
            notes.append(f"wrap_repair_error: {exc}")

    if "G03" in failed_auto:
        data = table.get("data")
        if isinstance(data, list):
            before_n = len(data)
            new_data = [
                r for r in data
                if not isinstance(r, list) or any(str(c or "").strip() for c in r)
            ]
            if new_data and len(new_data) < before_n and len(new_data) >= before_n * 0.5:
                table["data"] = new_data
                table["rows"] = len(new_data)
                notes.append(f"压缩全空行 {before_n}→{len(new_data)}")
                _update_finding_status(checklist, "G03", "fixed", "drop_empty_rows")

    if "G04" in failed_auto:
        data = table.get("data")
        if isinstance(data, list) and data:
            n_cols = max(len(r) for r in data if isinstance(r, list))
            keep, dropped = [], []
            for j in range(n_cols):
                if all(
                    not str((r[j] if j < len(r) else "") or "").strip()
                    for r in data if isinstance(r, list)
                ):
                    dropped.append(j)
                else:
                    keep.append(j)
            if dropped and keep:
                table["data"] = [
                    [r[j] if j < len(r) else "" for j in keep]
                    if isinstance(r, list) else r
                    for r in data
                ]
                table["cols"] = len(keep)
                notes.append(f"删除全空列 {dropped}")
                _update_finding_status(checklist, "G04", "fixed", "drop_empty_cols")

    return notes


def _mark_unresolved(checklist: Dict[str, Any]) -> None:
    """不能自动解决的失败项标注 needs_llm / needs_human。"""
    for f in checklist.get("findings") or []:
        if f.get("passed") or f.get("fix_status") in ("fixed", "ok", "na"):
            continue
        disp = f.get("disposition")
        if disp == "human":
            f["fix_status"] = "needs_human"
        elif disp == "llm":
            f["fix_status"] = "needs_llm"
        elif disp == "auto":
            # 规则未能修掉
            f["fix_status"] = "needs_llm"
            f["message"] = (f.get("message") or "") + " | 规则未消除，升级LLM"
        elif disp == "info":
            f["fix_status"] = "na"


def _derive_status(checklist: Dict[str, Any], tags: List[str]) -> str:
    findings = checklist.get("findings") or []
    if any(f.get("fix_status") == "needs_human" for f in findings if not f.get("passed")):
        return "human_needed"
    if "data_loss" in tags:
        return "human_needed"
    if any(f.get("fix_status") == "needs_llm" for f in findings if not f.get("passed")):
        return "llm_candidate"
    if any(f.get("fix_status") == "fixed" for f in findings):
        # 仍有失败则不能标 rule_fixed
        if any(
            (not f.get("passed")) and f.get("fix_status") not in ("na",)
            for f in findings
        ):
            return "llm_candidate"
        return "rule_fixed"
    if any(not f.get("passed") and f.get("fix_status") not in ("na",) for f in findings):
        return "llm_candidate"
    return "none"


def run_table_repair_pipeline(
    table: Dict[str, Any],
    *,
    redetect_anomaly=None,
    run_llm: bool = False,
    llm_apply: bool = False,
) -> Dict[str, Any]:
    """单表完整修复流水线。

    返回写入 table['_repair_checklist'] 的 dict，并更新 repair_status / _problem_report。
    """
    if not table:
        return {}

    cat = str(table.get("table_category") or "")
    if table.get("type") in ("text", "paragraph") or cat in ("页眉", "页脚", "文本段落"):
        checklist = run_full_checklist(table)
        checklist["phase"] = "skip"
        table["_repair_checklist"] = checklist
        table["repair_status"] = "none"
        table["_problem_report"] = build_problem_report(table).to_dict()
        return checklist

    # 分流：目录 / 非数据表不跑规则修与 LLM
    try:
        from codes.table_repair.table_kind import attach_table_kind, should_run_repair_pipeline

        kind = attach_table_kind(table)
        if not should_run_repair_pipeline(table):
            checklist = {
                "phase": "skip_kind",
                "findings": [],
                "summary": {
                    "total_checks": 0,
                    "failed": 0,
                    "failed_ids": [],
                    "skipped_kind": kind.kind,
                    "skip_reasons": list(kind.reasons),
                },
                "actions": [f"skip_{kind.kind}:" + ";".join(kind.reasons[:3])],
            }
            table["_repair_checklist"] = checklist
            table["repair_status"] = "skipped_non_data"
            table["_repair_notes"] = list(table.get("_repair_notes") or []) + checklist["actions"]
            report = build_problem_report(table)
            report.repair_status = "skipped_non_data"
            report.evidence = dict(report.evidence or {})
            report.evidence["table_kind"] = kind.to_dict()
            table["_problem_report"] = report.to_dict()
            return checklist
    except Exception as exc:
        # 分流失败不阻断后续
        pass

    actions: List[str] = []
    before_snapshot = deepcopy(table.get("data"))

    # ① 全量检查
    roles = infer_column_roles(table.get("data") or [])
    checklist = run_full_checklist(table, roles=roles)
    checklist["phase"] = "A"
    actions.append(
        f"checklist_failed={checklist.get('summary', {}).get('failed_ids')}"
    )

    # ② Phase A
    a_notes = _phase_a_fixes(table, checklist)
    actions.extend(a_notes)

    if a_notes and redetect_anomaly is not None:
        try:
            redetect_anomaly(table)
        except Exception as exc:
            actions.append(f"redetect_error: {exc}")

    # ③ 复检
    roles2 = infer_column_roles(table.get("data") or [])
    checklist2 = run_full_checklist(table, roles=roles2)
    # 保留已 fixed 标记：若复检通过则 ok；若复检仍失败则按未解决标
    prev_fixed = {
        f.get("check_id")
        for f in (checklist.get("findings") or [])
        if f.get("fix_status") == "fixed"
    }
    for f in checklist2.get("findings") or []:
        if f.get("check_id") in prev_fixed and f.get("passed"):
            f["fix_status"] = "fixed"
    checklist = checklist2
    checklist["phase"] = "A_done"
    _mark_unresolved(checklist)
    _recompute_summary(checklist)

    report = build_problem_report(table)
    tags = list(report.problem_tags)
    status = _derive_status(checklist, tags)

    # ④ Phase B：按错误类型的 LLM 纠正（error_types + typed_repair）
    llm_ids = [
        f.get("check_id")
        for f in (checklist.get("findings") or [])
        if f.get("fix_status") == "needs_llm" and not f.get("passed")
    ]
    if run_llm and status == "llm_candidate" and llm_ids and "data_loss" not in tags:
        checklist["phase"] = "B_typed"
        try:
            from codes.table_repair.typed_repair import run_typed_repair_on_table
            from codes.table_repair.error_types import errors_from_checklist_findings

            typed = run_typed_repair_on_table(
                table,
                findings=checklist.get("findings") or [],
                run_llm=True,
                apply=llm_apply,
            )
            actions.extend(typed.actions)
            actions.append(
                f"typed_repair success={typed.success} applied={typed.applied} "
                f"errors={typed.error_ids} err={typed.llm_error or typed.validation_errors}"
            )
            checklist["typed_errors"] = [
                e.to_dict() for e in errors_from_checklist_findings(
                    checklist.get("findings") or []
                )
            ]
            if typed.applied and redetect_anomaly is not None:
                try:
                    redetect_anomaly(table)
                except Exception as exc:
                    actions.append(f"redetect_error: {exc}")
            checklist = run_full_checklist(table)
            _mark_unresolved(checklist)
            _recompute_summary(checklist)
            report = build_problem_report(table)
            tags = list(report.problem_tags)
            status = str(table.get("repair_status") or _derive_status(checklist, tags))
            if typed.success and not typed.applied:
                status = "llm_proposed"
            elif typed.applied:
                status = "llm_applied"
            elif typed.escalate_human or not typed.success:
                status = "human_needed"
        except Exception as exc:
            actions.append(f"typed_llm_error: {exc}")
            status = "human_needed"
            checklist["phase"] = "B_failed"

    # ⑤ 仍有 human 项
    if any(
        f.get("fix_status") == "needs_human" and not f.get("passed")
        for f in (checklist.get("findings") or [])
    ):
        status = "human_needed"
        checklist["phase"] = "C"

    checklist["actions"] = actions
    checklist["phase_final"] = checklist.get("phase")
    table["_repair_checklist"] = checklist
    table["repair_status"] = status
    report = build_problem_report(table)
    report.repair_status = status
    report.evidence = dict(report.evidence or {})
    report.evidence["checklist_summary"] = checklist.get("summary")
    report.evidence["router_actions"] = actions
    table["_problem_report"] = report.to_dict()
    if actions:
        table["_repair_notes"] = list(table.get("_repair_notes") or []) + actions

    # 守恒：Phase A 不得空造金额
    try:
        from codes.table_repair.validator import amounts_invented

        inv = amounts_invented(before_snapshot or [], table.get("data") or [])
        if inv:
            table["data"] = before_snapshot
            actions.append(f"phaseA_rollback_invented={inv[:5]}")
            status = "human_needed"
            table["repair_status"] = status
            for f in checklist.get("findings") or []:
                if f.get("check_id") == "N03":
                    f["passed"] = False
                    f["fix_status"] = "needs_human"
                    f["message"] = "PhaseA 疑似空造金额已回滚"
            _recompute_summary(checklist)
            table["_repair_checklist"] = checklist
    except Exception:
        pass

    return checklist
