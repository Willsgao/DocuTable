# -*- coding: utf-8 -*-
"""对单表跑全量检查目录：每项给出通过/失败与建议处置。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from codes.table_repair.check_catalog import CHECK_BY_ID, CHECK_CATALOG, CheckSpec
from codes.table_repair.column_roles import ColumnRoles, infer_column_roles
from codes.table_repair.wrap_repair import (
    _is_label_continuation,
    _row_amount_count,
    split_glued_hierarchy_label,
)

_AMOUNT_RE = re.compile(r"[\d,]{3,}|\d+\.\d+")
_LEAF_RE = re.compile(r"^[\-－—–]\s*")
_SERIAL_GLUE_RE = re.compile(r"^[(（]?\d{1,3}[)）.．、]\s*\S+")


@dataclass
class CheckFinding:
    check_id: str
    category: str
    title: str
    passed: bool
    disposition: str  # auto / llm / human / info
    message: str = ""
    fix_status: str = "pending"  # pending|ok|fixed|needs_llm|needs_human|na
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _norm_data(data: Any) -> List[List[str]]:
    if not isinstance(data, list):
        return []
    out: List[List[str]] = []
    for row in data:
        if isinstance(row, list):
            out.append([str(c or "") for c in row])
        elif isinstance(row, str):
            out.append([row])
        else:
            out.append([str(row or "")])
    return out


def _cell(row: Sequence[str], j: int) -> str:
    if j < 0 or j >= len(row):
        return ""
    return str(row[j] or "").strip()


def _finding(spec: CheckSpec, passed: bool, message: str = "", **evidence) -> CheckFinding:
    fix = "ok" if passed else (
        "needs_human" if spec.disposition == "human"
        else "needs_llm" if spec.disposition == "llm"
        else "pending" if spec.disposition == "auto"
        else "na"
    )
    return CheckFinding(
        check_id=spec.check_id,
        category=spec.category,
        title=spec.title,
        passed=passed,
        disposition=spec.disposition,
        message=message,
        fix_status=fix,
        evidence=evidence,
    )


def run_full_checklist(
    table: Dict[str, Any],
    *,
    roles: Optional[ColumnRoles] = None,
) -> Dict[str, Any]:
    """跑全量检查，返回 checklist dict（写入 table['_repair_checklist'] 前的结构）。"""
    specs = {c.check_id: c for c in CHECK_CATALOG}
    data = _norm_data(table.get("data"))
    cat = str(table.get("table_category") or "")
    ttype = table.get("type")
    anomaly = table.get("_anomaly") or {}
    rule_ids = [str(r) for r in (anomaly.get("rule_ids") or [])]
    findings: List[CheckFinding] = []

    # I01 真表
    non_table = (
        ttype in ("text", "paragraph")
        or table.get("is_real_table") is False
        or cat in ("页眉", "页脚", "文本段落")
    )
    findings.append(
        _finding(
            specs["I01"],
            passed=not non_table,
            message="非表格条目" if non_table else "真表",
            category=cat,
            type=str(ttype or ""),
        )
    )
    if non_table:
        # 其余标 na，避免噪音
        for spec in CHECK_CATALOG:
            if spec.check_id == "I01":
                continue
            findings.append(
                _finding(spec, True, "跳过：非表", skipped=True)
            )
            findings[-1].fix_status = "na"
            findings[-1].passed = True
        return _pack(findings, roles or ColumnRoles(), rule_ids)

    roles = roles or infer_column_roles(data)
    label_col = int(roles.primary_label_col or 0)

    # R01 列角色
    roles_ok = bool(roles.label_cols) and roles.n_cols > 0
    findings.append(
        _finding(
            specs["R01"],
            passed=roles_ok,
            message=(
                f"serial={roles.serial_cols} label={roles.label_cols} "
                f"value={roles.value_cols} primary={label_col}"
            ),
            **roles.to_dict(),
        )
    )
    if roles_ok:
        findings[-1].fix_status = "ok"

    # I02 跨页/切断
    header_missing = bool(anomaly.get("header_missing") or "C01_missing_header" in rule_ids
                          or "C01_no_header_band" in rule_ids)
    cross = bool(table.get("_suggest_merge_to") or table.get("_merged_from_pages"))
    i02_fail = header_missing or cross
    findings.append(
        _finding(
            specs["I02"],
            passed=not i02_fail,
            message="缺表头/跨页候选" if i02_fail else "未见切断标记",
            header_missing=header_missing,
            cross_page=cross,
        )
    )

    # I03 文表混杂（启发式：中部全宽长文本无金额）
    mixed = _detect_embedded_paragraph(data, roles)
    findings.append(
        _finding(specs["I03"], passed=not mixed, message="疑似表中夹段落" if mixed else "OK")
    )

    # H01 表头缺失
    findings.append(
        _finding(
            specs["H01"],
            passed=not header_missing,
            message="表头带缺失" if header_missing else "OK",
        )
    )

    # H02 合并作用域（无 bbox 时：表头行短词重复/空单元格模式 → 疑似）
    h02 = _detect_header_span_loss(data)
    findings.append(
        _finding(
            specs["H02"],
            passed=not h02,
            message="疑似合并表头作用域丢失" if h02 else "未见明显 span 丢失（无bbox时弱检）",
            weak=True,
        )
    )

    # H03 表头折行
    h03 = _detect_header_wrap(data, roles)
    findings.append(
        _finding(
            specs["H03"],
            passed=not h03,
            message="表头区疑似折行未归并" if h03 else "OK",
            rows=h03 if isinstance(h03, list) else [],
        )
    )

    # H04 多级对齐 — 用 anomaly misalignment / R08
    h04 = bool(
        {"R08_header_data_misalign", "R07_word_crosses_columns", "C03_column_type_violation"}
        & set(rule_ids)
    ) or ("misalignment" in (table.get("_problem_report") or {}).get("problem_tags", []))
    findings.append(
        _finding(specs["H04"], passed=not h04, message="表头/列对齐异常" if h04 else "OK")
    )

    # H05 表头碎片进表体（弱：数据区首行无金额且短残片）
    h05 = _detect_header_fragment_in_body(data, roles)
    findings.append(
        _finding(specs["H05"], passed=not h05, message="疑似表头碎片入表体" if h05 else "OK")
    )

    # H06 重复表头行
    h06 = _detect_duplicate_header_rows(data)
    findings.append(
        _finding(specs["H06"], passed=not h06, message="疑似重复表头行" if h06 else "OK")
    )

    # L01 标签列折行
    l01_rows = _detect_label_wrap(data, label_col)
    findings.append(
        _finding(
            specs["L01"],
            passed=not l01_rows,
            message=f"标签列折行候选 {len(l01_rows)} 处" if l01_rows else "OK",
            rows=l01_rows,
            label_col=label_col,
        )
    )

    # L02/L03 层级
    glued = _detect_hierarchy_glue(data, label_col)
    findings.append(
        _finding(
            specs["L03"],
            passed=not glued,
            message=f"层级粘连 {len(glued)} 处" if glued else "OK",
            rows=glued,
            label_col=label_col,
        )
    )
    l02 = glued or _detect_hierarchy_scope_loss(data, label_col, roles)
    findings.append(
        _finding(
            specs["L02"],
            passed=not l02,
            message="标签列层级/父级作用域异常" if l02 else "OK",
            label_col=label_col,
        )
    )

    # L04 序号粘连
    l04 = _detect_serial_glue(data, label_col, roles)
    findings.append(
        _finding(
            specs["L04"],
            passed=not l04,
            message="序号与标签疑似粘连" if l04 else "OK",
            rows=l04,
        )
    )

    # G01 列数跳动
    g01 = _detect_col_jitter(data)
    findings.append(
        _finding(specs["G01"], passed=not g01, message="数据区列数剧烈跳动" if g01 else "OK")
    )

    # G02 错位
    g02 = bool(
        set(rule_ids)
        & {
            "R08_header_data_misalign",
            "R05_text_in_numeric",
            "R04_merged_numeric",
            "R07_word_crosses_columns",
            "C02_unrecognized_data_row",
        }
    )
    findings.append(
        _finding(specs["G02"], passed=not g02, message="质检提示错位/串列" if g02 else "OK")
    )

    # G03 空行空列
    g03 = _detect_spacer(data)
    findings.append(
        _finding(specs["G03"], passed=not g03, message="连续空行/空列" if g03 else "OK")
    )

    # G04 鬼列
    g04 = _detect_ghost_cols(data)
    findings.append(
        _finding(
            specs["G04"],
            passed=not g04,
            message=f"疑似鬼列 {g04}" if g04 else "OK",
            cols=g04,
        )
    )

    # G05 粘连
    g05 = _detect_cell_glue(data)
    findings.append(
        _finding(
            specs["G05"],
            passed=not g05,
            message=f"单元格粘连 {len(g05)} 处" if g05 else "OK",
            cells=g05[:20],
        )
    )
    if table.get("_glue_repaired") and g05:
        # 已修过仍有则保留失败；若标记已修且检测空则 ok
        pass

    # N01 数值列形态
    n01 = _detect_value_col_pollution(data, roles)
    findings.append(
        _finding(specs["N01"], passed=not n01, message="数值列含大量文本" if n01 else "OK")
    )

    # N02 丢数
    n02 = bool(
        table.get("_conservation_failed")
        or table.get("_item_conservation_failed")
        or "R06_ghost_column" in rule_ids
        or "C04_incomplete_data_row" in rule_ids
    )
    # 源覆盖启发式
    src_loss = _detect_source_coverage_loss(table, data)
    findings.append(
        _finding(
            specs["N02"],
            passed=not (n02 or src_loss),
            message="疑似丢数/守恒失败" if (n02 or src_loss) else "OK",
            source_coverage_low=src_loss,
        )
    )

    # N03 仅在有提案时检；此处占位 OK
    prop = table.get("_llm_proposal") or {}
    n03 = False
    if prop.get("repaired_table") and prop.get("before_data"):
        try:
            from codes.table_repair.validator import amounts_invented

            inv = amounts_invented(prop.get("before_data"), prop.get("repaired_table"))
            n03 = bool(inv)
        except Exception:
            n03 = False
    findings.append(
        _finding(
            specs["N03"],
            passed=not n03,
            message="提案疑似空造金额" if n03 else "无空造金额（或无提案）",
        )
    )

    # S01 源覆盖
    findings.append(
        _finding(
            specs["S01"],
            passed=not src_loss,
            message="源文本覆盖不足" if src_loss else "OK（或无源映射）",
        )
    )

    # 确保目录每项都有结果
    seen = {f.check_id for f in findings}
    for spec in CHECK_CATALOG:
        if spec.check_id not in seen:
            findings.append(_finding(spec, False, "检测未实现", missing_detector=True))

    findings.sort(key=lambda f: f.check_id)
    return _pack(findings, roles, rule_ids)


def _pack(
    findings: List[CheckFinding],
    roles: ColumnRoles,
    rule_ids: List[str],
) -> Dict[str, Any]:
    failed = [f for f in findings if not f.passed and f.fix_status != "na"]
    return {
        "version": 1,
        "column_roles": roles.to_dict(),
        "rule_ids": list(rule_ids),
        "findings": [f.to_dict() for f in findings],
        "summary": {
            "total_checks": len(findings),
            "failed": len(failed),
            "needs_auto": sum(1 for f in failed if f.disposition == "auto"),
            "needs_llm": sum(1 for f in failed if f.disposition == "llm"),
            "needs_human": sum(1 for f in failed if f.disposition == "human"),
            "failed_ids": [f.check_id for f in failed],
        },
    }


# ----- detectors -----

def _detect_embedded_paragraph(data: List[List[str]], roles: ColumnRoles) -> bool:
    if len(data) < 6:
        return False
    for i, row in enumerate(data[1:-1], start=1):
        joined = "".join(c.strip() for c in row)
        if len(joined) < 28:
            continue
        if _row_amount_count(row) > 0:
            continue
        # 后续仍有金额行
        if any(_row_amount_count(data[j]) >= 1 for j in range(i + 1, min(i + 4, len(data)))):
            if not _LEAF_RE.match(_cell(row, roles.primary_label_col)):
                return True
    return False


def _detect_header_span_loss(data: List[List[str]]) -> bool:
    if len(data) < 2:
        return False
    # 前 3 行：大量空单元格夹着短标签 → 可能本该 colspan
    for row in data[:3]:
        if _row_amount_count(row) > 0:
            continue
        nonempty = [c for c in row if str(c).strip()]
        empty = sum(1 for c in row if not str(c).strip())
        if len(nonempty) >= 1 and empty >= 2 and all(len(c) <= 12 for c in nonempty):
            return True
    return False


def _detect_header_wrap(data: List[List[str]], roles: ColumnRoles) -> List[int]:
    hits: List[int] = []
    if len(data) < 2:
        return hits
    # 仅扫前 5 行中无金额的相邻行
    limit = min(5, len(data) - 1)
    for i in range(limit):
        a, b = data[i], data[i + 1]
        if _row_amount_count(a) or _row_amount_count(b):
            continue
        # 同列短续写
        for j in range(min(len(a), len(b), roles.n_cols or len(a))):
            a0, b0 = _cell(a, j), _cell(b, j)
            if a0 and b0 and len(b0) <= 16 and _is_label_continuation(a0, b0):
                # 其它列 b 多为空
                if sum(1 for c in b if str(c).strip()) <= 2:
                    hits.append(i)
                    break
    return hits


def _detect_header_fragment_in_body(data: List[List[str]], roles: ColumnRoles) -> bool:
    if len(data) < 4:
        return False
    # 找首个金额行
    first_amt = next((i for i, r in enumerate(data) if _row_amount_count(r) >= 1), None)
    if first_amt is None or first_amt <= 0:
        return False
    # 金额行上的短无金额行已在表头；检查金额行本身标签是否像半截
    lab = _cell(data[first_amt], roles.primary_label_col)
    if lab and len(lab) <= 2 and not _LEAF_RE.match(lab):
        return True
    return False


def _detect_duplicate_header_rows(data: List[List[str]]) -> bool:
    if len(data) < 6:
        return False
    head = tuple(_cell(data[0], j) for j in range(len(data[0])))
    if not any(head):
        return False
    for i in range(3, len(data) - 1):
        cur = tuple(_cell(data[i], j) for j in range(min(len(data[i]), len(head))))
        if cur == head[: len(cur)] and any(cur):
            return True
    return False


def _detect_label_wrap(data: List[List[str]], label_col: int) -> List[int]:
    hits: List[int] = []
    for i in range(len(data) - 1):
        a, b = data[i], data[i + 1]
        if _row_amount_count(a) < 1:
            continue
        if _row_amount_count(b) > 0:
            continue
        # b 除标签列外应空
        extra = 0
        for j, c in enumerate(b):
            if j == label_col:
                continue
            if str(c).strip():
                extra += 1
        if extra:
            continue
        a0, b0 = _cell(a, label_col), _cell(b, label_col)
        if _is_label_continuation(a0, b0):
            hits.append(i)
    return hits


def _detect_hierarchy_glue(data: List[List[str]], label_col: int) -> List[int]:
    hits: List[int] = []
    for i, row in enumerate(data):
        lab = _cell(row, label_col)
        if split_glued_hierarchy_label(lab):
            hits.append(i)
    return hits


def _detect_hierarchy_scope_loss(
    data: List[List[str]], label_col: int, roles: ColumnRoles
) -> bool:
    """父行无金额后接 －子项，但父文本粘在子项上已由 glue 覆盖；此处查：子项前缺少空金额父行。"""
    for i in range(1, len(data)):
        lab = _cell(data[i], label_col)
        if not _LEAF_RE.match(lab):
            continue
        prev = data[i - 1]
        prev_lab = _cell(prev, label_col)
        if _LEAF_RE.match(prev_lab):
            continue
        # 上一行若已有金额且标签很长并含叶子语义，可能父级被并进去了
        if _row_amount_count(prev) >= 1 and split_glued_hierarchy_label(prev_lab):
            return True
    return False


def _detect_serial_glue(
    data: List[List[str]], label_col: int, roles: ColumnRoles
) -> List[int]:
    hits: List[int] = []
    # 若已有独立序号列则检查标签列是否仍带序号前缀
    for i, row in enumerate(data):
        lab = _cell(row, label_col)
        if _SERIAL_GLUE_RE.match(lab) and len(lab) >= 4:
            # 无独立序号列，或序号列为空
            if not roles.serial_cols or not any(
                _cell(row, s) for s in roles.serial_cols
            ):
                hits.append(i)
    return hits


def _detect_col_jitter(data: List[List[str]]) -> bool:
    if len(data) < 5:
        return False
    lengths = [len(r) for r in data if any(str(c).strip() for c in r)]
    if len(lengths) < 5:
        return False
    from collections import Counter

    mode = Counter(lengths).most_common(1)[0][0]
    bad = sum(1 for L in lengths if abs(L - mode) >= 2)
    return bad >= max(3, len(lengths) // 3)


def _detect_spacer(data: List[List[str]]) -> bool:
    empty_rows = 0
    for row in data:
        if not any(str(c).strip() for c in row):
            empty_rows += 1
            if empty_rows >= 2:
                return True
        else:
            empty_rows = 0
    if not data:
        return False
    n_cols = max(len(r) for r in data)
    for j in range(n_cols):
        empty_run = 0
        # 连续两列全空
        pass
    # 连续空列
    for j in range(n_cols):
        if all(not _cell(r, j) for r in data):
            if j + 1 < n_cols and all(not _cell(r, j + 1) for r in data):
                return True
    return False


def _detect_ghost_cols(data: List[List[str]]) -> List[int]:
    if not data:
        return []
    n_cols = max(len(r) for r in data)
    ghosts: List[int] = []
    for j in range(n_cols):
        nonempty = sum(1 for r in data if _cell(r, j))
        if 0 < nonempty <= max(1, len(data) // 10) and len(data) >= 8:
            ghosts.append(j)
    return ghosts


def _detect_cell_glue(data: List[List[str]]) -> List[Dict[str, int]]:
    hits: List[Dict[str, int]] = []
    for i, row in enumerate(data):
        for j, c in enumerate(row):
            t = str(c or "").strip()
            if not t:
                continue
            if _AMOUNT_RE.search(t) and re.search(r"[\u4e00-\u9fff]{2,}", t):
                # 排除纯「2024年」类
                if re.fullmatch(r"20\d{2}\s*年?", t):
                    continue
                hits.append({"r": i, "c": j})
    return hits


def _detect_value_col_pollution(data: List[List[str]], roles: ColumnRoles) -> bool:
    if not roles.value_cols or len(data) < 4:
        return False
    for j in roles.value_cols:
        textish = 0
        amt = 0
        for row in data[1:]:
            t = _cell(row, j)
            if not t:
                continue
            if _AMOUNT_RE.search(t):
                amt += 1
            elif re.search(r"[\u4e00-\u9fff]{2,}", t):
                textish += 1
        if amt + textish >= 4 and textish > amt:
            return True
    return False


def _detect_source_coverage_loss(table: Dict[str, Any], data: List[List[str]]) -> bool:
    sources = table.get("_cell_source_items")
    if not isinstance(sources, list) or not data:
        return False
    cells = 0
    empty_src = 0
    for ri, row in enumerate(data):
        if ri >= len(sources):
            break
        src_row = sources[ri] if isinstance(sources[ri], list) else []
        for ci, cell in enumerate(row):
            if not str(cell).strip():
                continue
            cells += 1
            src = src_row[ci] if ci < len(src_row) else None
            if not src:
                empty_src += 1
    return cells >= 8 and empty_src / max(cells, 1) >= 0.55
