# -*- coding: utf-8 -*-
"""逻辑行组装：同格折行合并 + 科目缩进（给后续处理用）。

凝结核定列之后：
- 文本折行：以价值锚（金额或「-」占位）为主上下扩展；旁列空的续行并入；
  两锚争抢时优先邻行，远端仅明确碎片可并，否则 ambiguous（宁可错杀）
- 层次：按科目左缘写 indent_level，并以前导空格落入格子
- 与跨单元格无关：不看右缘拆列
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

_AMOUNT_RE = re.compile(r"[\d,]{3,}|\d+\.\d+%?|\(\s*[\d,]+\s*\)")
_PLACEHOLDER_RE = re.compile(r"^[-–—－]$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_SPAN_MARK_RE = re.compile(r"\s*⟦[^⟧]*⟧\s*")


def _strip_marks(s: str) -> str:
    return _SPAN_MARK_RE.sub(" ", str(s or "")).strip()


def _is_amount_cell(val: Any) -> bool:
    t = _strip_marks(str(val or ""))
    if not t or _PLACEHOLDER_RE.match(t):
        return False
    return bool(_AMOUNT_RE.search(t))


def _is_placeholder_cell(val: Any) -> bool:
    t = _strip_marks(str(val or ""))
    return bool(_PLACEHOLDER_RE.match(t))


def infer_label_col(data: Sequence[Sequence[Any]]) -> int:
    """科目列：中文多、金额少。"""
    if not data:
        return 0
    n_cols = max((len(r) for r in data if isinstance(r, (list, tuple))), default=0)
    if n_cols <= 0:
        return 0
    best_i, best_score = 0, -1e9
    for c in range(n_cols):
        cjk = amt = non_empty = 0
        for r in data:
            if not isinstance(r, (list, tuple)) or c >= len(r):
                continue
            t = _strip_marks(str(r[c] or ""))
            if not t:
                continue
            non_empty += 1
            if _CJK_RE.search(t):
                cjk += 1
            if _is_amount_cell(t):
                amt += 1
        score = cjk * 3 - amt * 5 + (1 if non_empty else 0)
        if score > best_score:
            best_score = score
            best_i = c
    return best_i


def _label_of(row: Sequence[Any], label_col: int) -> str:
    if label_col < 0 or label_col >= len(row):
        return ""
    return _strip_marks(str(row[label_col] or ""))


def _leading_indent_spaces(text: str) -> int:
    s = str(text or "")
    n = 0
    for ch in s:
        if ch == " ":
            n += 1
        else:
            break
    return n


def _row_side_status(row: Sequence[Any], label_col: int) -> str:
    """amount | placeholder | empty | other。"""
    has_amt = has_ph = has_other = False
    for j, c in enumerate(row):
        if j == label_col:
            continue
        t = _strip_marks(str(c or ""))
        if not t:
            continue
        if _is_amount_cell(t):
            has_amt = True
        elif _is_placeholder_cell(t):
            has_ph = True
        else:
            has_other = True
    if has_amt:
        return "amount"
    if has_ph:
        return "placeholder"
    if has_other:
        return "other"
    return "empty"


def _is_label_only_row(row: Sequence[Any], label_col: int) -> bool:
    return bool(_label_of(row, label_col)) and _row_side_status(row, label_col) == "empty"


def _is_value_anchor_status(status: str) -> bool:
    """金额或「-」占位均可作折行宿主（资本构成表大量旁列仅有 -）。"""
    return status in ("amount", "placeholder")


def _infer_serial_col(label_col: int) -> int:
    """序号通常在科目列左侧；科目在 0 时无独立序号列。"""
    return 0 if label_col > 0 else -1


def _row_serial_text(row: Sequence[Any], serial_col: int) -> str:
    if serial_col < 0 or serial_col >= len(row):
        return ""
    return _strip_marks(str(row[serial_col] or "")).strip()


def _row_has_serial(row: Sequence[Any], serial_col: int) -> bool:
    t = _row_serial_text(row, serial_col)
    if not t:
        return False
    # 14 / 47a / A1 等行号
    if re.fullmatch(r"\d{1,3}[a-zA-Z]?", t):
        return True
    if re.fullmatch(r"[A-Za-z]\d{0,2}", t):
        return True
    return False


def _is_clear_wrap_fragment(label: str) -> bool:
    """明显折行碎片：以粘连虚词收尾/起头，或括号未闭合。

    不以「：/:」判折行——表外「…如下：」会误并进首行金额。
    """
    t = str(label or "").strip()
    if not t:
        return False
    if t.endswith(("的", "及", "与", "和", "或", "等", "、")):
        return True
    if t.startswith(("的", "及", "与", "和", "或", "等", "）", ")")):
        return True
    if t.count("（") > t.count("）") or t.count("(") > t.count(")"):
        return True
    return False


def _looks_cjk_wrap_continuation(upper: str, lower: str) -> bool:
    """同列连续中文折行：上半截接到下半截（无空格断点）。"""
    u = str(upper or "").strip()
    l = str(lower or "").strip()
    if not u or not l:
        return False
    if not (_CJK_RE.search(u[-1:]) and _CJK_RE.search(l[:1])):
        return False
    if l.startswith(("的", "及", "与", "和", "或", "等", "非", "）", ")")):
        return True
    if _is_clear_wrap_fragment(u):
        return True
    return False


def _looks_serial_host_continuation(upper: str, lower: str) -> bool:
    """无序号续行 → 邻行有序号宿主：允许下半为短续文（如「债务工具投资」）。"""
    if _looks_cjk_wrap_continuation(upper, lower):
        return True
    u = str(upper or "").strip()
    l = str(lower or "").strip()
    if not u or not l:
        return False
    if not (_CJK_RE.search(u[-1:]) and _CJK_RE.search(l[:1])):
        return False
    # 下半短续文，且不像独立完整科目句
    if len(l) <= 24 and not l.endswith(("。", "；", "：", ":")):
        return True
    return False


def _looks_like_external_narrative(label: str) -> bool:
    """表外说明/引导句：禁止并入表内金额行（不得增删改表内数据）。"""
    t = str(label or "").strip()
    if not t:
        return False
    if any(
        m in t
        for m in (
            "如下",
            "下表",
            "本集团根据",
            "有关要求",
            "最新规定",
            "填报说明",
            "评估指标披露",
        )
    ):
        return True
    cn = len(_CJK_RE.findall(t))
    if t.endswith(("：", ":", "。", "；")) and cn >= 8:
        return True
    return False


def _prefer_host_when_contested(
    lab: str,
    *,
    row_i: int,
    up_amt: int,
    down_amt: int,
    working: Sequence[Sequence[Any]],
    label_col: int,
) -> Optional[int]:
    """两价值锚争抢时：优先邻行；仅明确折行碎片可并入远端，否则 ambiguous。"""
    if _looks_like_external_narrative(lab):
        return None
    d_up = row_i - up_amt
    d_down = down_amt - row_i
    lab_down = _label_of(working[down_amt], label_col)
    lab_up = _label_of(working[up_amt], label_col)

    # 邻行优先：括号未闭合 / 粘连虚词 / 连续中文续文 / 序号宿主短续文
    if d_down == 1:
        if (
            lab.endswith(("的", "及", "与", "和", "或", "等", "、"))
            or lab.count("（") > lab.count("）")
            or lab.count("(") > lab.count(")")
            or _looks_cjk_wrap_continuation(lab, lab_down)
        ):
            return down_amt
        sc = _infer_serial_col(label_col)
        if (
            not _row_has_serial(working[row_i], sc)
            and _row_has_serial(working[down_amt], sc)
            and _looks_serial_host_continuation(lab, lab_down)
        ):
            return down_amt
    if d_up == 1:
        if lab.startswith(("的", "及", "与", "和", "或", "等", "）", ")")):
            return up_amt
        if _looks_cjk_wrap_continuation(lab_up, lab):
            return up_amt
        sc = _infer_serial_col(label_col)
        if (
            not _row_has_serial(working[row_i], sc)
            and _row_has_serial(working[up_amt], sc)
            and _looks_serial_host_continuation(lab_up, lab)
        ):
            return up_amt

    # 远端：仍只允许最明确的碎片方向（且选更近一侧）
    if lab.endswith(("的", "及", "与", "和", "或", "等", "、")):
        return down_amt if d_down <= d_up else None
    if lab.startswith(("的", "及", "与", "和", "或", "等", "）", ")")):
        return up_amt if d_up <= d_down else None
    if lab.count("（") > lab.count("）") or lab.count("(") > lab.count(")"):
        # 未闭合括号不得跨多行吞进远端金额行（会吃掉中间行）
        if d_down <= 2:
            return down_amt
        return None
    if lab_down.startswith(("的", "及", "与", "非", "等")) and _is_clear_wrap_fragment(lab):
        return down_amt if d_down <= d_up else None
    return None


def _join_label_parts(a: str, b: str) -> str:
    """连续中文折行：不拆空格硬插；保留宿主前导缩进。"""
    lead = _leading_indent_spaces(a) if a.startswith(" ") else _leading_indent_spaces(b)
    aa = a.strip()
    bb = b.strip()
    if not aa:
        body = bb
    elif not bb:
        body = aa
    elif aa.endswith(("的", "及", "与", "和", "或", "等", "、")) or bb.startswith(
        ("的", "及", "与", "和", "或", "等", "）", ")")
    ):
        body = aa + bb
    else:
        # 同格连续：中文之间不加空格
        if _CJK_RE.search(aa[-1:]) and _CJK_RE.search(bb[:1]):
            body = aa + bb
        else:
            body = f"{aa} {bb}".strip()
    return (" " * lead) + body if lead else body


def assemble_wrapped_label_rows(
    data: List[List[Any]],
    *,
    label_col: Optional[int] = None,
) -> Tuple[List[List[Any]], Dict[str, Any]]:
    """价值锚（金额/占位）上下并旁列空续行；两锚争抢 → ambiguous（不合并，升级人工）。"""
    meta: Dict[str, Any] = {
        "merges": [],
        "ambiguous_rows": [],
        "label_col": 0,
    }
    if not data or len(data) < 2:
        return data, meta

    working = [list(r) if isinstance(r, list) else [] for r in data]
    lc = infer_label_col(working) if label_col is None else int(label_col)
    meta["label_col"] = lc
    sc = _infer_serial_col(lc)

    # 多轮：每次并掉一行后重扫
    guard = 0
    while guard < len(working) + 5:
        guard += 1
        statuses = [_row_side_status(r, lc) for r in working]
        labels_only = [
            i for i, r in enumerate(working)
            if _is_label_only_row(r, lc)
        ]
        if not labels_only:
            break

        merged_any = False
        for i in labels_only:
            # 找上下最近价值锚（金额或「-」占位），避免跳过占位行吞进远端金额行
            up_amt = next(
                (j for j in range(i - 1, -1, -1) if _is_value_anchor_status(statuses[j])),
                None,
            )
            down_amt = next(
                (j for j in range(i + 1, len(working)) if _is_value_anchor_status(statuses[j])),
                None,
            )

            lab_i = _label_of(working[i], lc)
            if not lab_i:
                continue
            # 表外叙述不得并入表内（宁可不并，不可改表内数据）
            if _looks_like_external_narrative(lab_i):
                meta["ambiguous_rows"].append(
                    {
                        "row": i,
                        "label": lab_i[:40],
                        "reason": "external_narrative",
                    }
                )
                continue

            host: Optional[int] = None
            # 两价值锚之间：邻行优先；仅明确折行碎片可并远端；否则 ambiguous
            if up_amt is not None and down_amt is not None:
                host = _prefer_host_when_contested(
                    lab_i,
                    row_i=i,
                    up_amt=up_amt,
                    down_amt=down_amt,
                    working=working,
                    label_col=lc,
                )
                if host is None:
                    meta["ambiguous_rows"].append(
                        {
                            "row": i,
                            "label": lab_i[:40],
                            "reason": "between_two_amount_anchors",
                            "up_amt": up_amt,
                            "down_amt": down_amt,
                        }
                    )
                    continue
            elif down_amt is not None and up_amt is None:
                host = down_amt
            elif up_amt is not None and down_amt is None:
                host = up_amt
            else:
                continue

            # 门槛：明确折行碎片，或邻行「无序号续文 → 有序号宿主」连续中文。
            # 页眉/完整科目行不得因「旁列空」被吸进远端金额行。
            lab_h = _label_of(working[host], lc)
            adj = abs(host - i) == 1
            serial_wrap = (
                adj
                and host > i
                and not _row_has_serial(working[i], sc)
                and _row_has_serial(working[host], sc)
                and _looks_serial_host_continuation(lab_i, lab_h)
            )
            if not (
                _is_clear_wrap_fragment(lab_i)
                or (
                    host > i
                    and lab_h.startswith(("的", "及", "与", "和", "或", "等", "非", "）", ")"))
                )
                or (
                    host < i
                    and lab_h.endswith(("的", "及", "与", "和", "或", "等", "、"))
                )
                or serial_wrap
                or (
                    adj
                    and host > i
                    and _looks_cjk_wrap_continuation(lab_i, lab_h)
                    and _is_clear_wrap_fragment(lab_i)
                )
            ):
                continue

            if host < i:
                # 续行在下：接到宿主后
                new_lab = _join_label_parts(lab_h, lab_i)
                while len(working[host]) <= lc:
                    working[host].append("")
                working[host][lc] = new_lab
                del working[i]
                meta["merges"].append(f"wrap {i}->host{host} (below)")
            else:
                # 续行在上：接到宿主前
                new_lab = _join_label_parts(lab_i, lab_h)
                while len(working[host]) <= lc:
                    working[host].append("")
                working[host][lc] = new_lab
                del working[i]
                # host index shifts down by 1
                meta["merges"].append(f"wrap {i}->host{host} (above)")
            merged_any = True
            break  # restart scan after structural change

        if not merged_any:
            break

    # 去重 ambiguous（按 label）
    seen = set()
    uniq = []
    for a in meta["ambiguous_rows"]:
        key = (a.get("label"), a.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(a)
    meta["ambiguous_rows"] = uniq
    return working, meta


def apply_label_indent_to_data(
    data: List[List[Any]],
    rows_nuclei_x0: Optional[Sequence[Optional[float]]] = None,
    *,
    label_col: Optional[int] = None,
    indent_step_pt: float = 12.0,
    indent_threshold_pt: float = 5.0,
    spaces_per_level: int = 2,
    max_level: int = 4,
) -> Tuple[List[List[Any]], Dict[str, Any]]:
    """按行科目左缘写前导空格；无几何时保留已有前导空格。"""
    meta: Dict[str, Any] = {"indent_levels": [], "label_col": 0}
    if not data:
        return data, meta
    working = [list(r) if isinstance(r, list) else [] for r in data]
    lc = infer_label_col(working) if label_col is None else int(label_col)
    meta["label_col"] = lc

    baseline = None
    if rows_nuclei_x0:
        xs = [float(x) for x in rows_nuclei_x0 if x is not None]
        if xs:
            baseline = min(xs)

    levels: List[int] = []
    for ri, row in enumerate(working):
        while len(row) <= lc:
            row.append("")
        raw = str(row[lc] or "")
        body = raw.lstrip(" ")
        level = 0
        if rows_nuclei_x0 and ri < len(rows_nuclei_x0) and rows_nuclei_x0[ri] is not None and baseline is not None:
            delta = max(0.0, float(rows_nuclei_x0[ri]) - baseline)
            if delta >= indent_threshold_pt:
                level = min(max_level, max(1, int(round(delta / indent_step_pt))))
        else:
            level = min(max_level, _leading_indent_spaces(raw) // max(spaces_per_level, 1))
        if level > 0:
            row[lc] = (" " * (spaces_per_level * level)) + body
        else:
            row[lc] = body
        levels.append(level)
    meta["indent_levels"] = levels
    return working, meta


def postprocess_grid_logical_rows(
    data: List[List[Any]],
    *,
    label_x0_per_row: Optional[Sequence[Optional[float]]] = None,
) -> Tuple[List[List[Any]], Dict[str, Any]]:
    """网格后处理：先缩进，再折行组装（宿主行保留缩进）。"""
    indented, ind_meta = apply_label_indent_to_data(
        data, label_x0_per_row
    )
    assembled, wrap_meta = assemble_wrapped_label_rows(
        indented, label_col=ind_meta.get("label_col")
    )
    # 合并后再根据仍在的前导空格重算 level 列表长度
    lc = int(wrap_meta.get("label_col") or 0)
    levels = []
    for row in assembled:
        lab = str(row[lc] if lc < len(row) else "") if row else ""
        levels.append(min(4, _leading_indent_spaces(lab) // 2))
    out_meta = {
        **wrap_meta,
        "indent_levels": levels,
        "indent_applied": True,
        "needs_review": bool(wrap_meta.get("ambiguous_rows")),
    }
    return assembled, out_meta
