# -*- coding: utf-8 -*-
"""把 `_grid_nucleus` 收成任务可读摘要（列表标签 + 详情段落）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union


def _as_gn(table_or_gn: Optional[Union[Dict[str, Any], Any]]) -> Dict[str, Any]:
    if not table_or_gn or not isinstance(table_or_gn, dict):
        return {}
    if "_grid_nucleus" in table_or_gn:
        gn = table_or_gn.get("_grid_nucleus")
        return gn if isinstance(gn, dict) else {}
    # 已是 _grid_nucleus 本体（含 ok/method）
    if "method" in table_or_gn or "ok" in table_or_gn:
        return table_or_gn
    return {}


def summarize_grid_nucleus(
    table_or_gn: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """生成紧凑摘要，供任务 evidence / UI 使用（不含整表 data）。"""
    gn = _as_gn(table_or_gn)
    if not gn:
        return {
            "present": False,
            "short_label": "凝核·未跑",
            "verdict": "missing",
            "ok": False,
            "method": "",
            "n_rows": 0,
            "n_cols": 0,
            "overwrote_data": False,
            "errors": [],
            "metrics": {},
            "lines": ["未写入 _grid_nucleus（本次扫描未跑凝结核，或非数据表已跳过）。"],
        }

    ok = bool(gn.get("ok"))
    method = str(gn.get("method") or "")
    metrics = gn.get("metrics") if isinstance(gn.get("metrics"), dict) else {}
    errors = [str(e) for e in (gn.get("errors") or [])][:8]
    overwrote = bool(metrics.get("overwrote_data"))
    n_rows = int(gn.get("n_rows") or 0)
    n_cols = int(gn.get("n_cols") or 0)
    cover = metrics.get("cover")
    skipped = metrics.get("skipped")

    if skipped:
        verdict = "skipped"
        short = f"凝核·跳过({skipped})"
    elif overwrote and ok:
        verdict = "overwrote"
        short = f"凝核·已写回 {n_rows}×{n_cols}"
    elif ok and not overwrote:
        verdict = "ok_kept"
        short = f"凝核·通过未写回 {n_rows}×{n_cols}"
    elif method in ("fallback_keep", "none") or not ok:
        verdict = "kept"
        # 把首因缩进列表标签，避免「全部保留」看不出原因
        tip = ""
        if errors:
            e0 = errors[0]
            if e0.startswith("no_source_words"):
                tip = "无字框"
            elif e0.startswith("conservation_block"):
                tip = "守恒拦"
            elif e0.startswith("cover_low"):
                tip = "覆盖低"
            elif e0.startswith("glue_residual"):
                tip = "粘连"
            else:
                tip = e0.split(":")[0][:10]
        short = f"凝核·保留原表" + (f"({tip})" if tip else "")
    else:
        verdict = "ran"
        short = f"凝核·{method or 'ran'}"

    lines: List[str] = [
        f"结果：{'成功' if ok else '未通过'} / method={method or '—'}",
        f"推断网格：{n_rows} 行 × {n_cols} 列",
        f"是否覆盖 data：{'是' if overwrote else '否'}",
    ]
    if cover is not None:
        try:
            lines.append(f"字框覆盖率：{float(cover):.1%}")
        except (TypeError, ValueError):
            lines.append(f"字框覆盖率：{cover}")
    if metrics.get("cross_ratio") is not None:
        lines.append(f"列交叉比：{metrics.get('cross_ratio')}")
    if skipped:
        lines.append(f"跳过原因：{skipped}")
    if errors:
        lines.append("错误/拦截：" + "；".join(errors[:5]))
    # 校验失败时的粘连专项审计
    ga = metrics.get("glue_audit") if isinstance(metrics, dict) else None
    if isinstance(ga, dict) and ga.get("triggered"):
        audit = ga.get("audit") if isinstance(ga.get("audit"), dict) else {}
        note = str(ga.get("note") or "")
        if note == "no_glue":
            lines.append("粘连专项：未发现可拆文本粘连")
        elif note == "glue_repaired":
            repair = ga.get("repair") if isinstance(ga.get("repair"), dict) else {}
            n_act = len(repair.get("actions") or [])
            kinds = ",".join(str(k) for k in (audit.get("kinds") or [])[:4])
            lines.append(
                f"粘连专项：已拆修 {n_act} 处"
                + (f"（{kinds}）" if kinds else "")
            )
        elif note == "glue_unrepaired":
            kinds = ",".join(str(k) for k in (audit.get("kinds") or [])[:4])
            hits = audit.get("hits") or []
            tip = hits[0].get("snippet") if hits else ""
            lines.append(
                "粘连专项：检出粘连但未能自动拆开"
                + (f" [{kinds}]" if kinds else "")
                + (f"：{tip}" if tip else "")
            )
        if metrics.get("glue_kinds"):
            gk = metrics.get("glue_kinds")
            if isinstance(gk, dict) and gk:
                lines.append(
                    "粘连类型："
                    + ",".join(f"{k}×{v}" for k, v in list(gk.items())[:6])
                )
    elif isinstance(metrics, dict) and metrics.get("glue_kinds"):
        gk = metrics.get("glue_kinds")
        if isinstance(gk, dict) and gk:
            lines.append(
                "粘连类型："
                + ",".join(f"{k}×{v}" for k, v in list(gk.items())[:6])
            )
    if verdict == "kept":
        lines.append(
            "说明：凝结核未改写本表（校验失败、守恒拦截或无字框），界面仍是原解析结果。"
        )
    elif verdict == "overwrote":
        lines.append(
            "说明：已按字框重切行列并写回 data；若仍有错，多为几何推断不足，需 LLM/人工。"
        )
    ha = metrics.get("header_align") if isinstance(metrics, dict) else None
    if isinstance(ha, dict) and (
        ha.get("header_align")
        or ha.get("merges")
        or ha.get("bottom_actions")
        or ha.get("dual_header_spills")
    ):
        parts = []
        if ha.get("merges"):
            parts.append("合并 " + ",".join(str(x) for x in ha.get("merges")[:6]))
        if ha.get("dual_header_spills"):
            parts.append(
                "双表头拆 "
                + ",".join(str(x) for x in ha.get("dual_header_spills")[:4])
            )
        if ha.get("bottom_actions"):
            parts.append("底层 " + ",".join(str(x) for x in ha.get("bottom_actions")[:4]))
        if ha.get("bottom_header_ok") is True:
            parts.append("底层表头✓一一对齐")
        elif ha.get("bottom_header_ok") is False:
            parts.append(
                "底层表头缺列:"
                + ",".join(str(x) for x in (ha.get("bottom_header_missing_amt_cols") or [])[:4])
            )
        if ha.get("cols_before") and ha.get("cols_after"):
            parts.append(f"{ha.get('cols_before')}→{ha.get('cols_after')}列")
        lines.append("表头对齐：" + "；".join(parts))

    return {
        "present": True,
        "short_label": short,
        "verdict": verdict,
        "ok": ok,
        "method": method,
        "n_rows": n_rows,
        "n_cols": n_cols,
        "overwrote_data": overwrote,
        "errors": errors,
        "metrics": {
            k: metrics[k]
            for k in (
                "cover",
                "cross_ratio",
                "overwrote_data",
                "skipped",
                "fallback_chain",
                "glue_audit",
                "glue_kinds",
            )
            if k in metrics
        },
        "lines": lines,
    }


def format_grid_nucleus_detail_block(summary: Optional[Dict[str, Any]]) -> str:
    """任务说明里的【凝结核】段落。"""
    s = summary or {}
    lines = ["【凝结核】"]
    for ln in s.get("lines") or ["（无摘要）"]:
        lines.append(f"  {ln}")
    return "\n".join(lines)


def grid_nucleus_list_tag(summary: Optional[Dict[str, Any]]) -> str:
    """列表短标签，如「凝核·已写回」。"""
    if not summary:
        return ""
    return str(summary.get("short_label") or "").strip()
