# -*- coding: utf-8 -*-
"""可选 LLM 裁判：跨页是否合并、粘连如何拆（禁止 OCR 纠字）。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import requests

from .models import Confidence, FormatTask, TaskType


SYSTEM_PROMPT = """你是表格【格式】纠错助手，不是 OCR 校对员。
硬性规则：
1. 不得改正、猜测或替换任何文字/数字的字形（OCR 错误留给人工）。
2. 不得丢失任何非空内容；相邻重复也不得建议删除（跨页重复表头除外且须说明依据）。
3. 不得重排原有行的业务顺序；只允许：合并续表、拆分粘连单元格、标记文表边界。
4. 只输出 JSON。
"""


def _load_llm_config(api_key=None, endpoint=None, model=None):
    from codes.pdf_extractor.utils import load_config

    cfg = load_config()
    return {
        "api_key": api_key or cfg.get("deepseek_api_key", ""),
        "endpoint": endpoint or cfg.get("deepseek_endpoint", "api.deepseek.com"),
        "model": model or cfg.get("deepseek_model", "deepseek-chat"),
    }


def call_llm_json(system: str, user: str, *, api_key=None, endpoint=None, model=None) -> Dict[str, Any]:
    conf = _load_llm_config(api_key, endpoint, model)
    if not conf["api_key"]:
        return {"error": "missing_api_key"}
    url = f"https://{conf['endpoint']}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {conf['api_key']}",
    }
    payload = {
        "model": conf["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_json(content)
    except Exception as e:
        return {"error": str(e)}


def _parse_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {"error": "json_parse_failed", "raw": content[:500]}


def refine_merge_task_with_llm(
    task: FormatTask,
    table_a: dict,
    table_b: dict,
    gap_preview: List[str],
) -> FormatTask:
    """让 LLM 对合并候选做 merge/keep_separate/uncertain 裁判。"""
    if task.task_type != TaskType.CROSS_PAGE_MERGE:
        return task

    def _preview(data, n=4):
        rows = []
        for row in (data or [])[:n]:
            rows.append([str(c or "")[:40] for c in (row or [])[:8]])
        return rows

    user = {
        "instruction": (
            "判断两张相邻表是否应合并为跨页/拆分续表。"
            "若中间有独立小节标题或新表说明，应 keep_separate。"
            "输出 JSON: {decision: merge|keep_separate|uncertain, confidence: high|medium|low, reason: str}"
        ),
        "gap_text_lines": gap_preview,
        "table_a_tail": _preview((table_a.get("data") or [])[-4:]),
        "table_b_head": _preview((table_b.get("data") or [])[:4]),
        "pages": [table_a.get("page"), table_b.get("page")],
        "cols": [
            max((len(r) for r in (table_a.get("data") or [])), default=0),
            max((len(r) for r in (table_b.get("data") or [])), default=0),
        ],
    }
    result = call_llm_json(SYSTEM_PROMPT, json.dumps(user, ensure_ascii=False))
    if result.get("error"):
        task.evidence["llm_error"] = result.get("error")
        return task

    decision = str(result.get("decision") or "uncertain").lower()
    conf = str(result.get("confidence") or "medium").lower()
    task.evidence["llm_decision"] = result
    if decision == "merge":
        task.proposal["auto_apply"] = conf == "high"
        task.proposal["action"] = "merge"
        task.confidence = Confidence.HIGH if conf == "high" else Confidence.MEDIUM
        task.reason = result.get("reason") or task.reason
    elif decision == "keep_separate":
        task.proposal["auto_apply"] = False
        task.proposal["action"] = "keep_separate"
        task.confidence = Confidence.HIGH if conf == "high" else Confidence.MEDIUM
        task.reason = result.get("reason") or "LLM 判定应保持分离"
    else:
        task.proposal["auto_apply"] = False
        task.confidence = Confidence.UNCERTAIN
        task.reason = result.get("reason") or "LLM 无法确定"
    return task


def refine_glue_with_llm(task: FormatTask, table: dict) -> FormatTask:
    """对 EMPTY_SPLIT：请 LLM 指出粘连格如何拆（parts 拼接须等于原文）。"""
    if task.task_type != TaskType.EMPTY_SPLIT:
        return task
    miss = task.evidence.get("liteparse_tokens_missing_in_table") or []
    if not miss:
        return task

    # 取若干长单元格作候选
    candidates = []
    for ri, row in enumerate((table.get("data") or [])[:40]):
        for ci, cell in enumerate(row or []):
            s = str(cell or "").strip()
            if len(s) >= 8:
                candidates.append({"row": ri, "col": ci, "text": s[:120]})
            if len(candidates) >= 20:
                break
        if len(candidates) >= 20:
            break

    user = {
        "instruction": (
            "以下单元格可能把相邻字段粘在一起。请给出拆分方案。"
            "每项: {row, col, parts: [..]}，要求 parts 拼接去空白后等于原文去空白。"
            "不要改正错字。输出 JSON: {splits: [...]}。"
        ),
        "missing_liteparse_tokens": miss[:20],
        "cells": candidates,
    }
    result = call_llm_json(SYSTEM_PROMPT, json.dumps(user, ensure_ascii=False))
    if result.get("error"):
        task.evidence["llm_error"] = result.get("error")
        return task

    splits = result.get("splits") or []
    patches = list((task.proposal or {}).get("patches") or [])
    for sp in splits:
        parts = sp.get("parts") or []
        if len(parts) < 2:
            continue
        original = None
        ri, ci = int(sp.get("row", -1)), int(sp.get("col", -1))
        data = table.get("data") or []
        if 0 <= ri < len(data) and 0 <= ci < len(data[ri]):
            original = str(data[ri][ci] or "")
        if not original:
            continue
        from .conservation import cell_key

        if cell_key("".join(parts)) != cell_key(original):
            continue
        patches.append(
            {
                "action": "split_cell_horizontal",
                "row": ri,
                "col": ci,
                "parts": parts,
                "original": original,
                "note": "LLM 语义拆分（已做还原校验）",
            }
        )
    if patches:
        task.proposal = task.proposal or {}
        task.proposal["patches"] = patches
        task.proposal["action"] = "patch"
        task.evidence["llm_splits"] = splits
    return task
