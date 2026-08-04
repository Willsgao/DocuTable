# -*- coding: utf-8 -*-
"""数据主体锁定：粘连先拆 → 同列同型 → 数据区范围（表头服从主体）。"""

from __future__ import annotations

from typing import Any, Dict, List


def prepare_data_body(table: Dict[str, Any]) -> Dict[str, Any]:
    """在 checklist 之前执行。写入 table['_data_body']，返回 notes 摘要。"""
    notes: List[str] = []
    meta: Dict[str, Any] = {
        "glue_fixed": False,
        "zone": {},
        "roles": {},
        "homogeneous_ok": True,
        "homogeneous_msg": "",
    }

    # 1) 粘连必拆（金额+文本同格）
    try:
        from codes.v2_steps.table_glue_repair import repair_table_numeric_text_glue

        gnotes = repair_table_numeric_text_glue(table)
        if gnotes:
            meta["glue_fixed"] = True
            notes.extend(gnotes)
    except Exception as exc:
        notes.append(f"glue_skip:{exc}")

    data = table.get("data") or []

    # 2) 列角色 + 数据区
    try:
        from codes.table_repair.column_roles import infer_column_roles
        from codes.table_repair.invariants import (
            check_column_homogeneity,
            locate_data_zone,
        )

        roles = infer_column_roles(data)
        zone = locate_data_zone(data, roles)
        ok_h, msg_h = check_column_homogeneity(data, zone)
        meta["roles"] = {
            "n_cols": roles.n_cols,
            "label_col": int(roles.primary_label_col or 0),
            "value_cols": list(roles.value_cols or []),
            "serial_col": getattr(roles, "serial_col", None),
        }
        meta["zone"] = zone.to_dict() if hasattr(zone, "to_dict") else {
            "start_row": zone.start_row,
            "end_row": zone.end_row,
            "n_cols": zone.n_cols,
            "value_cols": list(zone.value_cols),
            "label_col": zone.label_col,
        }
        meta["homogeneous_ok"] = bool(ok_h)
        meta["homogeneous_msg"] = str(msg_h)
        if not ok_h:
            notes.append(f"col_type_break:{msg_h}")
        else:
            notes.append(
                f"data_body rows={zone.start_row}:{zone.end_row} "
                f"value_cols={list(zone.value_cols)}"
            )
    except Exception as exc:
        notes.append(f"data_body_skip:{exc}")

    table["_data_body"] = meta
    return {"notes": notes, "meta": meta}
