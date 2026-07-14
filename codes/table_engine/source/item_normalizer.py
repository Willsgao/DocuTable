# -*- coding: utf-8 -*-
"""liteparse text_items 标准化（独立于 table_validator）。"""

from __future__ import annotations

import re
from typing import List

from codes.table_engine.models import BBox, SourceItem


def compute_item_index(page_num: int, text: str, x0: float, y0: float) -> str:
    """与旧版 _compute_item_index 同算法，存为 str 便于 JSON/日志。"""
    idx = hash((page_num, text, round(x0, 1), round(y0, 1)))
    return str(idx)


def _merge_split_decimals(items: List[dict]) -> List[dict]:
    """合并 PDF 拆开的小数后缀（如 239 + .85）。"""
    if len(items) < 2:
        return items

    decimal_suffix_pattern = re.compile(r"^\.\d+\D?$")
    decimal_suffixes: List[tuple] = []
    integer_candidates: List[tuple] = []

    for i, it in enumerate(items):
        text = it.get("text", "").strip()
        if not text:
            continue
        if decimal_suffix_pattern.match(text):
            decimal_suffixes.append((i, it))
        elif text[-1].isdigit():
            clean = text.replace(",", "")
            if re.match(r"^-?\d+$", clean):
                integer_candidates.append((i, it))

    if not decimal_suffixes:
        return items

    merged_indices = set()
    merged_items: List[dict] = []

    for ds_idx, ds_item in decimal_suffixes:
        ds_x0 = ds_item.get("x0", 0)
        ds_x1 = ds_item.get("x1", 0)
        ds_y_mid = ds_item.get("y_mid", 0)
        ds_y0 = ds_item.get("y0", 0)
        ds_y1 = ds_item.get("y1", 0)

        best_int_item = None
        best_int_idx = None
        best_x_gap = float("inf")

        for int_idx, int_item in integer_candidates:
            if int_idx == ds_idx or int_idx in merged_indices:
                continue
            int_x1 = int_item.get("x1", 0)
            int_y_mid = int_item.get("y_mid", 0)
            if abs(ds_y_mid - int_y_mid) > 3.0:
                continue
            if int_x1 > ds_x0 + 2.0:
                continue
            gap = max(ds_x0 - int_x1, 0)
            int_width = int_item.get("x1", 0) - int_item.get("x0", 0)
            if gap > max(int_width * 1.5, 15.0):
                continue
            if gap < best_x_gap:
                best_x_gap = gap
                best_int_idx = int_idx
                best_int_item = int_item

        if best_int_item is None:
            continue

        merged_item = {
            "text": best_int_item["text"] + ds_item["text"],
            "x0": best_int_item["x0"],
            "x1": ds_x1,
            "y0": min(best_int_item.get("y0", ds_y0), ds_y0),
            "y1": max(best_int_item.get("y1", ds_y1), ds_y1),
            "y_mid": (best_int_item.get("y_mid", ds_y_mid) + ds_y_mid) / 2,
            "item_index": best_int_item.get("item_index"),
            "_merged_from": list(best_int_item.get("_merged_from", [])),
        }
        if ds_item.get("item_index"):
            merged_item["_merged_from"].append(ds_item["item_index"])
        merged_items.append(merged_item)
        merged_indices.add(best_int_idx)
        merged_indices.add(ds_idx)

    if not merged_indices:
        return items

    result = [it for i, it in enumerate(items) if i not in merged_indices]
    result.extend(merged_items)
    result.sort(key=lambda it: (it["y_mid"], it["x0"]))
    return result


def _merge_chinese_chars(items: List[dict]) -> List[dict]:
    """合并同行连续单 CJK 字符。"""
    if len(items) < 2:
        return items

    sorted_items = sorted(items, key=lambda it: (round(it["y_mid"], 2), it["x0"]))
    merged: List[dict] = []
    i = 0
    n = len(sorted_items)

    def _is_single_cjk(text: str) -> bool:
        return (
            len(text) == 1
            and (
                "\u4e00" <= text <= "\u9fff"
                or "\u3000" <= text <= "\u303f"
                or "\uff00" <= text <= "\uffef"
            )
        )

    while i < n:
        item = sorted_items[i]
        text = item.get("text", "")
        if not _is_single_cjk(text):
            merged.append(item)
            i += 1
            continue

        group = [item]
        y_mid = item["y_mid"]
        prev_x1 = item["x1"]
        j = i + 1
        while j < n:
            nxt = sorted_items[j]
            if not _is_single_cjk(nxt.get("text", "")):
                break
            if abs(nxt["y_mid"] - y_mid) <= 2.0 and -1.0 <= nxt["x0"] - prev_x1 <= 5.0:
                group.append(nxt)
                prev_x1 = nxt["x1"]
                j += 1
            else:
                break

        if len(group) == 1:
            merged.append(item)
        else:
            merged_text = "".join(g["text"] for g in group)
            merged_item = dict(group[0])
            merged_item["text"] = merged_text
            merged_item["x1"] = group[-1]["x1"]
            merged_from = [
                g["item_index"] for g in group[1:] if g.get("item_index")
            ]
            if merged_from:
                merged_item["_merged_from"] = merged_from
            merged.append(merged_item)
        i = j

    merged.sort(key=lambda it: it["y_mid"])
    return merged


def _dict_to_source_item(page_num: int, it: dict) -> SourceItem:
    return SourceItem(
        text=it["text"],
        bbox=BBox(it["x0"], it["y0"], it["x1"], it["y1"]),
        page=page_num,
        item_index=str(it["item_index"]),
        y_mid=float(it["y_mid"]),
        font_size=float(it.get("font_size", 0.0)),
        font_name=str(it.get("font_name", "")),
        merged_from=[str(x) for x in it.get("_merged_from", [])],
    )


def normalize_page_items(
    text_items: List[dict],
    page_num: int,
) -> List[SourceItem]:
    """pages.json 原始 text_items → SourceItem 列表。"""
    raw: List[dict] = []
    for ti in text_items:
        if not isinstance(ti, dict):
            continue
        t = str(ti.get("text", "")).strip()
        y0 = float(ti.get("y0", 0))
        y1 = float(ti.get("y1", 0))
        x0 = float(ti.get("x0", 0))
        if not t or y1 <= y0:
            continue
        raw.append({
            "text": t,
            "x0": x0,
            "x1": float(ti.get("x1", 0)),
            "y0": y0,
            "y1": y1,
            "y_mid": (y0 + y1) / 2,
            "item_index": compute_item_index(page_num, t, x0, y0),
            "font_size": float(ti.get("font_size", 0.0)),
            "font_name": str(ti.get("font_name", "")),
        })

    raw = _merge_split_decimals(raw)
    raw = _merge_chinese_chars(raw)
    return [_dict_to_source_item(page_num, it) for it in raw]
