# -*- coding: utf-8 -*-
"""
AI 纠错引擎 — 四层递增式纠错架构

Layer 1: RuleChecker     — 10项规则预检（零API成本）
Layer 2: RuleAutoFixer   — 确定性规则修复（零API成本）
Layer 3: LLMCorrector    — LLM深度分析 + 数值交叉验证
Layer 4: CorrectionEngine — 总协调器，串联 L1→L2→L3

设计原则：
- 不修改原始提取逻辑，作为独立后处理管线
- 生成新数据(corrected_data)，不破坏原始 data
- 确定性问题自动修复并标记 high 置信度
- 不确定问题标注问题类型，等人工确认
- 不可解决问题明确告知原因

多模态扩展接口：
- PROMPT_MODE = "text_only" 当前纯文本模式
- 未来可改为 "multimodal" 启用图片分析
- CorrectionResult.layout_analysis 预留字段
"""

import json as _json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from PyQt5.QtCore import QObject, pyqtSignal

from .utils import load_config, get_project_root, get_pdf_cache_dir


# ============================================================
# 数据容器
# ============================================================

@dataclass
class CorrectionResult:
    """单张表格的纠错结果"""
    table_index: int = -1
    status: str = "clean"               # clean | verified | auto_fixed | auto_fixed_code | llm_analyzed | needs_review | unresolvable
    confidence: str = "high"             # high | medium | low | unresolvable
    diff_source: str = "none"            # "none" | "deterministic" | "llm" — 修正来源追踪
    diff_report: Optional[dict] = None   # Phase 4 确定性差异分析报告

    # == 命名（合并原 TableContextLLM 的功能）==
    name_title: str = ""
    name_summary: str = ""

    # == 层级关系 ==
    hierarchy: list = field(default_factory=list)
    # [{"row": 0, "type": "header", "level": 0, "label": "资产"},
    #  {"row": 5, "type": "subtotal", "level": 1, "label": "流动资产合计", "total_of_rows": [1,2,3,4]}]
    hierarchy_verified: bool = False    # 数值交叉验证是否通过

    # == 区域判断 ==
    region_is_complete: bool = True
    region_merge_prev: Optional[int] = None    # 应与前表合并的 table_index
    region_merge_next: Optional[int] = None    # 应与后表合并的 table_index
    region_split_rows: list = field(default_factory=list)  # 应在此行索引处拆分
    region_issues: list = field(default_factory=list)       # ["表格边界疑似包含非表格文本"]

    # == LLM 重构数据（核心新增）==
    reconstructed_data: Optional[list] = None     # LLM 重构的完整二维数组（直接替换原表数据）
    merge_source_indices: list = field(default_factory=list)  # 已合并的源表索引列表
    changes_log: list = field(default_factory=list)
    # [{"type": "cell_corrected", "row": 2, "col": 3, "pdf2docx_value": "12,345",
    #   "corrected_value": "12,346", "source": "liteparse"}, ...]

    # == 修正数据（兼容旧字段）==
    corrected_data: Optional[list] = None    # 仅在 data 有实质变化时非空
    applied_rules: list = field(default_factory=list)       # ["remove_empty_rows", ...]
    applied_corrections: list = field(default_factory=list)
    # [{"row": 2, "col": 3, "action": "change", "new_value": "12,345"}, ...]

    # == 无法自动处理 ==
    unresolved_issues: list = field(default_factory=list)
    # [{"type": "column_misalign", "confidence": "medium",
    #   "description": "第3-5行疑似左偏1列", "suggested_action": "建议对比PDF原图确认列对齐"}]

    # == 变更摘要 ==
    changes_summary: str = ""

    # == 多标签分类（供 UI 标签化展示）==
    check_result: dict = field(default_factory=dict)   # Layer 1 原始预检结果
    error_tags: list = field(default_factory=list)     # 统一标签列表
    # [{"key": "empty_rows", "label": "空行", "category": "auto_fixed", "detail": "行2,行5"}, ...]

    # == 预留：多模态分析结果 ==
    layout_analysis: Optional[dict] = None
    # {"table_bbox": [x0,y0,x1,y1], "merged_cells": [...], ...}

    # == Token 消耗统计 ==
    usage: Optional[dict] = None
    # {"prompt_tokens": 1234, "completion_tokens": 567, "total_tokens": 1801,
    #  "cost_estimate": 0.001, "model": "deepseek-chat"}


# ============================================================
# 工具函数
# ============================================================

def _deep_copy_table(data):
    """深拷贝表格数据"""
    return [[cell for cell in row] for row in data]


def _is_numeric(val):
    """判断值是否为数值类型（含千分位逗号、百分号、负号）"""
    if val is None:
        return False
    s = str(val).strip().rstrip('%').replace(',', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _count_numeric_in_row(row):
    """统计一行中数值单元格的数量"""
    return sum(1 for c in row if _is_numeric(c))


def _detect_irregular_number_format(data):
    """检测数值格式不一致（同一列有的有逗号有的没有）"""
    if not data:
        return False
    cols = max(len(r) for r in data)
    for c in range(cols):
        has_comma = False
        no_comma = False
        for r in range(len(data)):
            if c >= len(data[r]):
                continue
            v = str(data[r][c]).strip()
            if not _is_numeric(v):
                continue
            if ',' in v:
                has_comma = True
            else:
                no_comma = True
        if has_comma and no_comma:
            return True
    return False


def _detect_split_header(data):
    """检测表头是否被垂直拆分：前2行含短文本、无或极少数字，
    后面行以数字为主"""
    if len(data) < 3:
        return False
    rows = min(3, len(data))
    header_rows = data[:rows]
    body_rows = data[rows:rows + 5]

    header_numeric = sum(_count_numeric_in_row(r) for r in header_rows)
    header_total_cells = sum(len(r) for r in header_rows)
    if header_total_cells == 0:
        return False
    header_numeric_ratio = header_numeric / header_total_cells if header_total_cells else 0

    body_numeric = sum(_count_numeric_in_row(r) for r in body_rows)
    body_total_cells = sum(len(r) for r in body_rows)
    if body_total_cells == 0:
        return False
    body_numeric_ratio = body_numeric / body_total_cells

    # 表头数字少，正文数字多 → 可能拆分表头
    return body_numeric_ratio > 0.3 and header_numeric_ratio < 0.3


def _remove_rows(data, indices, applied, rule_name):
    """移除指定索引的行"""
    indices = sorted(set(indices), reverse=True)
    for idx in indices:
        if 0 <= idx < len(data):
            data.pop(idx)
    if indices:
        applied.append(rule_name)
    return data, applied


def _trim_all_cells(data, applied):
    """修剪所有单元格的前后空格"""
    changed = False
    for row in data:
        for c in range(len(row)):
            old = str(row[c])
            new = old.strip()
            if old != new:
                row[c] = new
                changed = True
    if changed:
        applied.append("trim_cells")
    return data, applied


def _trim_edge_empty_cells(data):
    """仅删除每行左右两端的空单元格，保留内部空单元格。
    用于表格提取后的自动清洗流程。

    策略：
    - 找到该行第一个非空单元格的索引（左边界）
    - 找到该行最后一个非空单元格的索引（右边界）
    - 删除左边界之前和右边界之后的所有单元格
    - 保持内部结构不变（包括中间的空白列）

    Returns:
        (new_data, removed_count)
    """
    if not data:
        return data, 0

    result = []
    total_removed = 0

    for row in data:
        # 找第一个和最后一个非空单元格索引
        first_non_empty = -1
        last_non_empty = -1
        for i, cell in enumerate(row):
            if cell is not None and str(cell).strip():
                if first_non_empty < 0:
                    first_non_empty = i
                last_non_empty = i

        if first_non_empty < 0:
            # 全空行，保留原样
            result.append(list(row))
        else:
            # 只保留 [first_non_empty .. last_non_empty] 范围内的单元格
            trimmed_row = row[first_non_empty:last_non_empty + 1]
            removed = len(row) - len(trimmed_row)
            total_removed += removed
            result.append(list(trimmed_row))

    return result, total_removed


def _normalize_numbers(data, applied):
    """统一数值格式：去掉不必要的逗号分隔"""
    changed = False
    for row in data:
        for c in range(len(row)):
            v = str(row[c]).strip()
            if _is_numeric(v) and ',' in v:
                row[c] = v.replace(',', '')
                changed = True
    if changed:
        applied.append("normalize_number_format")
    return data, applied


def min_confidence(a, b):
    """取较低的置信度等级"""
    order = {"high": 0, "medium": 1, "low": 2, "unresolvable": 3}
    return a if order.get(a, 0) >= order.get(b, 0) else b


# ============================================================
# Layer 1: 规则预检
# ============================================================

class RuleChecker:
    """规则预检器 — 对每张表执行10项检查，只检测不修复"""

    @staticmethod
    def check(table: dict) -> dict:
        """
        Args:
            table: {"data": [[...], ...], "__index__": int, ...}
        Returns:
            { issue_key: detail, ... }
        """
        data = table.get("data", [])
        if not data:
            return {"empty_table": True}

        results = {}
        rows = len(data)
        cols = max((len(r) for r in data), default=0)
        if cols == 0:
            return {"empty_table": True}

        # 1. 空行
        empty_row_indices = [
            i for i, r in enumerate(data)
            if all(not str(c).strip() for c in r)
        ]
        if empty_row_indices:
            results["empty_rows"] = empty_row_indices

        # 2. 列数不一致
        inconsistent_rows = [i for i, r in enumerate(data) if len(r) != cols]
        if inconsistent_rows:
            results["inconsistent_cols"] = inconsistent_rows

        # 3. 单行表
        if rows == 1:
            results["single_row"] = True

        # 4. 无数值
        if not any(_is_numeric(c) for r in data for c in r):
            results["no_numeric"] = True

        # 5-6. 空格
        if any(str(c).startswith(" ") for r in data for c in r):
            results["leading_whitespace"] = True
        if any(str(c).endswith(" ") for r in data for c in r):
            results["trailing_whitespace"] = True

        # 7. 数值格式不一致
        if _detect_irregular_number_format(data):
            results["irregular_number"] = True

        # 8. 表头拆分
        if _detect_split_header(data):
            results["split_header"] = True

        # 9. 空列表头
        null_header_cols = [
            c for c in range(cols)
            if all(not str(data[r][c]).strip()
                   for r in range(min(2, rows)) if c < len(data[r]))
        ]
        if null_header_cols:
            results["null_header_col"] = null_header_cols

        # 10. 首行纯数字（可能是跨页续行）
        first_row = data[0] if data else []
        if first_row and len(first_row) > 0:
            if all(_is_numeric(c) for c in first_row if str(c).strip()):
                results["orphan_numeric_row"] = True

        return results

    @staticmethod
    def has_issues(check_result: dict) -> bool:
        """检查是否有任何问题标记"""
        if not check_result:
            return False
        ignored = {"empty_table", "no_numeric", "single_row"}  # 这些不一定是"需要修复的错"
        return any(k not in ignored for k in check_result)


# ============================================================
# Layer 2: 规则自动修复
# ============================================================

class RuleAutoFixer:
    """规则自动修复器 — 只修确定性能确认的问题，confidence 固定为 high"""

    # 可自动修复的问题及其处理函数
    AUTO_FIXABLE = {
        "empty_rows",
        "leading_whitespace",
        "trailing_whitespace",
        "irregular_number",
    }

    @staticmethod
    def fix(table: dict, check_result: dict) -> CorrectionResult:
        """
        Args:
            table: 含 data 和 __index__ 的表格字典
            check_result: RuleChecker.check() 的输出
        Returns:
            CorrectionResult
        """
        data = _deep_copy_table(table.get("data", []))
        applied = []

        if not data:
            return CorrectionResult(
                table_index=table.get("__index__", -1),
                status="clean",
                confidence="high"
            )

        if "empty_rows" in check_result:
            data, applied = _remove_rows(data, check_result["empty_rows"], applied, "remove_empty_rows")

        if "leading_whitespace" in check_result or "trailing_whitespace" in check_result:
            data, applied = _trim_all_cells(data, applied)

        if "irregular_number" in check_result:
            data, applied = _normalize_numbers(data, applied)

        fixable_issues = set(k for k in check_result if k in RuleAutoFixer.AUTO_FIXABLE)
        unfixable_issues = set(k for k in check_result
                               if k not in RuleAutoFixer.AUTO_FIXABLE
                               and k not in ("empty_table",))

        result = CorrectionResult(
            table_index=table.get("__index__", -1),
            status="auto_fixed" if applied else "clean",
            confidence="high",
            applied_rules=applied,
            changes_summary="; ".join(applied) if applied else "无需修复"
        )

        if applied:
            result.corrected_data = data

        # 保存原始规则预检结果（供 UI 标签化展示）
        result.check_result = check_result

        # 还有无法规则修复的问题 → 标记为需要 LLM
        if unfixable_issues:
            result.status = "needs_review"
            result.confidence = "medium"

        return result


# ============================================================
# Layer 3: LLM 深度分析
# ============================================================

# ============================================================
# Prompt 模板管理器
# ============================================================

class PromptTemplateManager:
    """Prompt 模板管理器 — 加载、保存、切换模板"""

    TEMPLATES_FILE = "prompts/templates.json"
    DEFAULT_SYSTEM_FILE = "prompts/default_system.txt"
    DEFAULT_USER_FILE = "prompts/default_user.txt"

    def __init__(self):
        self._root = get_project_root()
        self._templates_data = None

    @property
    def templates_file(self):
        return self._root / self.TEMPLATES_FILE

    @property
    def default_system_file(self):
        return self._root / self.DEFAULT_SYSTEM_FILE

    @property
    def default_user_file(self):
        return self._root / self.DEFAULT_USER_FILE

    def _load_templates(self):
        """加载模板配置文件"""
        if self._templates_data is not None:
            return self._templates_data
        try:
            if self.templates_file.exists():
                with open(self.templates_file, 'r', encoding='utf-8') as f:
                    self._templates_data = _json.load(f)
            else:
                self._templates_data = {"version": 1, "default_template": "", "templates": {}}
        except Exception:
            self._templates_data = {"version": 1, "default_template": "", "templates": {}}
        return self._templates_data

    def get_template_list(self):
        """获取模板名称列表，返回 [(template_id, display_name), ...]"""
        data = self._load_templates()
        templates = data.get("templates", {})
        return [(tid, t.get("name", tid)) for tid in templates]

    def get_template(self, template_id):
        """获取指定模板的 (system_prompt, user_template)"""
        data = self._load_templates()
        t = data.get("templates", {}).get(template_id, {})
        return t.get("system", ""), t.get("user_template", "")

    def get_default_template_id(self):
        """获取默认模板 ID"""
        data = self._load_templates()
        return data.get("default_template", "")

    def get_default_system_prompt(self):
        """从默认文件读取 system prompt"""
        try:
            if self.default_system_file.exists():
                with open(self.default_system_file, 'r', encoding='utf-8') as f:
                    return f.read()
        except Exception:
            pass
        return ""

    def get_default_user_prompt_template(self):
        """从默认文件读取 user prompt 模板"""
        try:
            if self.default_user_file.exists():
                with open(self.default_user_file, 'r', encoding='utf-8') as f:
                    return f.read()
        except Exception:
            pass
        return ""


class LLMCorrector:
    """LLM 深度分析器 — 对比审核 + 表格重构 + 命名 + 层级 + 区域判断"""

    MAX_ROWS_PREVIEW = 50          # 每表最多预览行数（含 liteparse 时增大到 50）
    MAX_CELL_LEN = 25              # 单格最大字符数
    MAX_LITEPARSE_CHARS = 2500     # 每表 liteparse 区域文本最大字符数
    MAX_OUTPUT_TOKENS = 16000      # 输出 token 上限（重构数据需要更多输出）
    TOKENS_PER_TABLE_EST = 800     # 每表 token 估算（含 liteparse 时 ~800）
    MAX_INPUT_TOKENS = 48000       # 输入 token 安全上限
    BATCH_SIZE_WITH_LITEPARSE = 35  # 有 liteparse 时每批次表数
    MAX_JSON_RETRIES = 1           # JSON 解析失败时的最大重试次数

    # 常见模型的成本单价（元/1M tokens）
    PRICING = {
        "deepseek-chat":      {"input": 1.0, "output": 2.0},
        "deepseek-reasoner":  {"input": 4.0, "output": 16.0},
        "doubao-pro-32k":     {"input": 0.8, "output": 2.0},
        "doubao-pro-128k":    {"input": 5.0, "output": 9.0},
        "qwen-plus":          {"input": 2.0, "output": 6.0},
        "gpt-3.5-turbo":      {"input": 3.5, "output": 10.5},
        "gpt-4o":             {"input": 18.3, "output": 73.1},
    }

    PROMPT_MODE = "text_only"      # 当前纯文本模式，未来可改为 "multimodal"

    def __init__(self, api_key=None, endpoint=None, model=None):
        config = load_config()
        self.api_key = api_key or config.get("deepseek_api_key", "")
        self.endpoint = endpoint or config.get("deepseek_endpoint", "api.deepseek.com")
        self.model = model or config.get("deepseek_model", "deepseek-chat")
        self.api_url = f"https://{self.endpoint}/v1/chat/completions"
        # 存储最近一次实际发送的 prompt（供 UI 事后查看）
        self.last_system_prompt = ""
        self.last_user_prompt = ""
        # Token 消耗统计（跨批次的累计值）
        self.total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_estimate": 0.0,
            "model": self.model,
            "api_calls": 0,
        }
        # Prompt 模板管理器
        self._template_mgr = PromptTemplateManager()

    def analyze_all(self, tables, check_results, context=None, progress_callback=None,
                    custom_system_prompt=None, custom_user_prompt=None):
        """
        一次性分析所有待处理表格。

        Args:
            tables: 完整的 tables 列表
            check_results: {table_index: check_result_dict}
            context: PDFContext 实例（可选，用于扩大 context_text 提取）
            progress_callback: callable(percent, message)
            custom_system_prompt: 自定义 system prompt（None 则用默认）
            custom_user_prompt: 自定义 user prompt（None 则用默认）

        Returns:
            [CorrectionResult, ...]
        """
        if not self.api_key:
            return self._fallback_results(tables)

        # 检查 liteparse 是否可用，调整批次大小
        has_liteparse = self._load_liteparse_data(context) is not None
        if has_liteparse:
            batch_size = min(self.BATCH_SIZE_WITH_LITEPARSE,
                             self.MAX_INPUT_TOKENS // self.TOKENS_PER_TABLE_EST)
        else:
            batch_size = min(150, self.MAX_INPUT_TOKENS // self.TOKENS_PER_TABLE_EST)

        if len(tables) <= batch_size:
            return self._single_call(tables, check_results, context, progress_callback,
                                     custom_system_prompt, custom_user_prompt)
        else:
            return self._batched_call(tables, check_results, batch_size, context, progress_callback)

    def _single_call(self, tables, check_results, context, progress_callback,
                     custom_system_prompt=None, custom_user_prompt=None):
        """单批次 API 调用"""
        if progress_callback:
            progress_callback(10, "构建 Prompt...")

        if custom_system_prompt is not None and custom_user_prompt is not None:
            # 使用自定义 prompt
            system_prompt, user_prompt = custom_system_prompt, custom_user_prompt
        else:
            system_prompt, user_prompt = self._build_prompt(tables, check_results, context)

        # 存储到实例，供事后查看
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt

        if progress_callback:
            progress_callback(30, "调用 LLM API...")

        response_data, batch_usage = self._call_api(system_prompt, user_prompt)

        # 累计 token 消耗
        self._accumulate_usage(batch_usage)

        if progress_callback:
            progress_callback(70, "解析 LLM 响应...")

        return self._parse_response(response_data, tables, batch_usage)

    def _batched_call(self, tables, check_results, batch_size, context, progress_callback):
        """分批次 API 调用"""
        all_results = []
        for batch_start in range(0, len(tables), batch_size):
            batch_end = min(batch_start + batch_size, len(tables))
            batch_tables = tables[batch_start:batch_end]
            batch_checks = {
                i: check_results.get(i, {})
                for i in range(batch_start, batch_end)
            }

            batch_progress_start = int(90 * batch_start / len(tables))
            batch_progress_end = int(90 * batch_end / len(tables))

            def batch_cb(pct, msg):
                if progress_callback:
                    progress_callback(
                        batch_progress_start + int(pct / 100 * (batch_progress_end - batch_progress_start)),
                        msg
                    )

            batch_results = self._single_call(batch_tables, batch_checks, context, batch_cb)
            all_results.extend(batch_results)

            if batch_end < len(tables):
                time.sleep(1)  # 限速

        return all_results

    def _build_prompt(self, tables, check_results, context):
        """构建对比审核 Prompt（liteparse权威原文 + pdf2docx数据 → 重构表格）。"""
        total_pages = max((t.get("page", 1) for t in tables), default=1)

        # 使用模板管理器获取默认 system prompt
        system_prompt = self._template_mgr.get_default_system_prompt()
        if not system_prompt:
            system_prompt = (
                "你是一个 PDF 表格对比审核与重构专家。"
                "以 liteparse 区域文本（PDF 原始底片）为权威来源，审核 pdf2docx 提取表格，"
                "输出重构后的完整二维数组 reconstructed_data。"
            )

        # 加载 liteparse 数据
        liteparse_data = self._load_liteparse_data(context)

        # 为每张表预匹配 liteparse 区域文本
        table_liteparse_map = self._match_tables_to_liteparse(tables, liteparse_data)

        # 构建表格块（每表含 liteparse 原文 + 完整 pdf2docx 数据）
        table_blocks = self._build_table_blocks_v2(tables, check_results, context, table_liteparse_map)

        # 构建跨页提示（保留跨页合并建议）
        cross_page_hints = self._build_cross_page_hints(tables, liteparse_data)

        hints_block = f"\n## ⚠️ 跨页相邻提示\n{cross_page_hints}\n" if cross_page_hints else ""

        # 使用模板管理器获取默认 user prompt 模板
        user_template = self._template_mgr.get_default_user_prompt_template()
        if user_template:
            user_prompt = user_template.replace("{total_pages}", str(total_pages))
            user_prompt = user_prompt.replace("{table_count}", str(len(tables)))
            user_prompt = user_prompt.replace("{table_blocks}", "".join(table_blocks))
            # 如果有跨页提示，插入到 table_blocks 之前
            if hints_block:
                user_prompt = user_prompt.replace("{table_blocks}", hints_block + "".join(table_blocks))
            # 模板文件中用 {{ 和 }} 转义 JSON 大括号，现在还原为单个 {
            user_prompt = user_prompt.replace("{{", "{").replace("}}", "}")
        else:
            user_prompt = f"""本文档共 {total_pages} 页，提取到 {len(tables)} 张表格。
{hints_block}
{"".join(table_blocks)}

---
请返回 JSON（不要 markdown 代码块标记，直接返回纯 JSON 文本）：..."""

        return system_prompt, user_prompt

    # ==================== 表格 ↔ liteparse 匹配（新增核心方法） ====================

    def _match_tables_to_liteparse(self, tables, liteparse_data):
        """为每张 pdf2docx 表匹配 liteparse 区域文本。

        匹配策略：
        1. 该页仅 1 个 region → 直接匹配
        2. 多个 region → 用 match_tables_to_regions + 内容交叉验证
        3. 无 liteparse 数据 → 返回空字符串

        Returns:
            dict: {table_index: region_text_str}
        """
        result = {}
        if not liteparse_data:
            return result

        # 按页分组
        page_tables: dict = {}
        for t in tables:
            page_tables.setdefault(t.get("page", 0), []).append(t)

        for page_num, page_ts in page_tables.items():
            lp_page = self._get_liteparse_page_dict(liteparse_data, page_num)
            if not lp_page:
                continue

            regions = lp_page.get("table_regions", [])
            if not regions:
                continue

            if len(regions) == 1:
                # 单区域 → 整页所有表都关联同一个 region_text
                rt = regions[0].get("region_text", "")
                ctx = regions[0].get("context_text", "")
                for t in page_ts:
                    idx = t.get("__index__", -1)
                    # 优先用 context_text 里更有意义的描述
                    if ctx.strip():
                        result[idx] = f"[上方标题] {ctx.strip()[:300]}\n{rt[:self.MAX_LITEPARSE_CHARS]}"
                    else:
                        result[idx] = rt[:self.MAX_LITEPARSE_CHARS]
            else:
                # 多区域 → 精准匹配
                try:
                    from codes.table_validator.table_boundary import match_tables_to_regions
                    matching = match_tables_to_regions(page_ts, regions)
                    for local_idx, t in enumerate(page_ts):
                        idx = t.get("__index__", -1)
                        region_idx = matching.get(local_idx)
                        if region_idx is not None and region_idx < len(regions):
                            rt = regions[region_idx].get("region_text", "")
                            ctx = regions[region_idx].get("context_text", "")
                            if ctx.strip():
                                result[idx] = f"[上方标题] {ctx.strip()[:300]}\n{rt[:self.MAX_LITEPARSE_CHARS]}"
                            else:
                                result[idx] = rt[:self.MAX_LITEPARSE_CHARS]
                except Exception:
                    # 兜底：按 Y 坐标从上到下分配
                    sorted_regions = sorted(regions, key=lambda r: r.get("y0", 0))
                    for local_idx, t in enumerate(page_ts):
                        idx = t.get("__index__", -1)
                        if local_idx < len(sorted_regions):
                            rt = sorted_regions[local_idx].get("region_text", "")
                            result[idx] = rt[:self.MAX_LITEPARSE_CHARS]

        return result

    def _build_table_preview_full(self, table_data):
        """将表格数据压缩为预览文本（尽量完整，供 LLM 对比审核）。

        策略：
        - ≤ 30 行：全部显示
        - 31-60 行：全部显示
        - > 60 行：显示前 40 + 后 10 行
        """
        if not table_data:
            return "（表格数据为空）"

        total_rows = len(table_data)
        lines = []

        if total_rows <= 60:
            show_rows = table_data
        else:
            show_rows = table_data[:40] + [["...", f"（中间省略 {total_rows - 50} 行）", "..."]] + table_data[-10:]

        for row_idx, row in enumerate(show_rows):
            if row_idx >= 40 and total_rows > 60 and row_idx < len(show_rows) - 10:
                # 省略行
                lines.append(" | ".join(str(c) for c in row))
                continue
            cells = []
            for cell in row:
                s = str(cell).strip()
                if len(s) > self.MAX_CELL_LEN:
                    s = s[:self.MAX_CELL_LEN - 3] + "..."
                cells.append(s)
            actual_idx = row_idx if row_idx < 40 else total_rows - len(show_rows) + row_idx
            lines.append(f"行{actual_idx}: " + " | ".join(cells))

        if total_rows > 60:
            lines.append(f"（共 {total_rows} 行）")

        return "\n".join(lines)

    def _build_table_blocks_v2(self, tables, check_results, context, table_liteparse_map):
        """构建对比审核格式的表格块：每表附带 liteparse 原文 + 完整 pdf2docx 数据。"""
        total_pages = max((t.get("page", 1) for t in tables), default=1)
        table_blocks = []
        for t in tables:
            idx = t.get("__index__", 0)
            page = t.get("page", 1)
            ext = t.get("extractor", "unknown")
            ctx = self._enrich_context(t, context)
            preview = self._build_table_preview_full(t.get("data", []))
            ck = check_results.get(idx, {})
            lp_text = table_liteparse_map.get(idx, "")

            # 有 liteparse 时用"对比审核"格式
            if lp_text:
                block = f"""
---
### 表格 #{idx}（第 {page} 页 / 共 {total_pages} 页，{ext}）

#### 📋 liteparse 区域文本（PDF 文本层直接提取，是权威原文，优先信任）：
{lp_text}

#### 📝 上下文描述：
{ctx if ctx else "（无）"}

#### 📊 pdf2docx 提取数据（自动解析，待审核对比）：
{preview}

#### ⚠️ 规则预检：
{self._format_check_result(ck)}"""
            else:
                # 无 liteparse → 降级为旧格式（仅 pdf2docx 数据）
                block = f"""
---
### 表格 #{idx}（第 {page} 页 / 共 {total_pages} 页，{ext}）

#### ⚠️ 注意：本表无 liteparse 文本参考，仅能基于 pdf2docx 数据判断。

#### 📝 上下文描述：
{ctx if ctx else "（无）"}

#### 📊 pdf2docx 提取数据：
{preview}

#### ⚠️ 规则预检：
{self._format_check_result(ck)}"""
            table_blocks.append(block)
        return table_blocks

    def _build_cross_page_hints(self, tables, liteparse_data):
        """构建跨页相邻表格的合并提示（精简版）。"""
        if not liteparse_data:
            return ""

        # 按页分组
        page_tables: dict = {}
        for t in tables:
            page_tables.setdefault(t.get("page", 0), []).append(t)

        sorted_pages = sorted(page_tables.keys())
        hints = []

        for idx in range(len(sorted_pages) - 1):
            p_a = sorted_pages[idx]
            p_b = sorted_pages[idx + 1]
            if p_b != p_a + 1:
                continue  # 页码不连续

            tables_a = page_tables[p_a]
            tables_b = page_tables[p_b]
            if not tables_a or not tables_b:
                continue

            last_a = tables_a[-1]
            first_b = tables_b[0]
            cols_a = max((len(r) for r in last_a.get("data", [])), default=0)
            cols_b = max((len(r) for r in first_b.get("data", [])), default=0)
            if cols_a == 0 or cols_b == 0:
                continue
            if abs(cols_a - cols_b) > 2:
                continue

            idx_a = last_a.get("__index__", "?")
            idx_b = first_b.get("__index__", "?")
            hints.append(
                f"⚠️ 表格#{idx_a}（P{p_a}）和表格#{idx_b}（P{p_b}）"
                f"列数相近（{cols_a} vs {cols_b}），可能属于同一张跨页表格。请判断是否合并。"
            )

            # 附带 liteparse 区域尾部/头部各 5 行
            lp_a = self._get_liteparse_page_dict(liteparse_data, p_a)
            if lp_a:
                regions_a = lp_a.get("table_regions", [])
                if regions_a:
                    rt = regions_a[-1].get("region_text", "")
                    if rt:
                        lines = rt.split("\n") if "\n" in rt else [rt]
                        hints.append(f"   P{p_a} 区域尾部: {' | '.join(lines[-5:])[:300]}")

            lp_b = self._get_liteparse_page_dict(liteparse_data, p_b)
            if lp_b:
                regions_b = lp_b.get("table_regions", [])
                if regions_b:
                    rt = regions_b[0].get("region_text", "")
                    if rt:
                        lines = rt.split("\n") if "\n" in rt else [rt]
                        hints.append(f"   P{p_b} 区域头部: {' | '.join(lines[:5])[:300]}")

        return "\n".join(hints) if hints else ""

    def _enrich_context(self, table, context):
        """获取表格上方描述文字（优先使用现有 context_text）"""
        ctx = table.get("context_text", "")
        if ctx and len(ctx) > 50:
            return ctx[:500]  # 截断以防过长
        return (ctx or "")[:500]

    def _build_table_preview(self, table_data):
        """将表格数据压缩为文本预览（保留旧接口，内部调用 _build_table_preview_full）。"""
        return self._build_table_preview_full(table_data)

    def _build_liteparse_sections(self, tables, context):
        """构建 liteparse 版面参考块，覆盖同页多表 + 跨页相邻表。

        输出场景：
        - 同页 ≥2 张 pdf2docx 表（可能拆分）
        - 相邻跨页对（前页最后一张 + 后页第一张），列数特征匹配

        Returns:
            str: Markdown 格式的版面参考文本，或空字符串
        """
        # 栅门1: 加载 liteparse 缓存
        liteparse_data = self._load_liteparse_data(context)
        if not liteparse_data:
            return ""

        # 栅门2: 按页分组
        page_tables: dict = {}
        for t in tables:
            page_tables.setdefault(t.get("page", 0), []).append(t)

        sections = []

        # ---- 场景 A：同页多表 ----
        for page_num, page_ts in sorted(page_tables.items()):
            if len(page_ts) < 2:
                continue
            lp_page = self._get_liteparse_page_dict(liteparse_data, page_num)
            if not lp_page:
                continue
            regions = lp_page.get("table_regions", [])
            if not regions:
                continue
            section = self._format_page_section(page_num, page_ts, regions, lp_page)
            if section:
                sections.append(section)

        # ---- 场景 B：跨页相邻对 ----
        # 找出页码连续且都有表格的相邻页对
        # 跳过已在场景 A 中详细输出的页
        covered_pages = {p for p, ts in page_tables.items() if len(ts) >= 2}

        sorted_pages = sorted(page_tables.keys())
        for idx in range(len(sorted_pages) - 1):
            p_a = sorted_pages[idx]
            p_b = sorted_pages[idx + 1]
            if p_b != p_a + 1:
                continue  # 页码不连续
            if p_a in covered_pages:
                continue  # 已在场景 A 详细覆盖

            tables_a = page_tables[p_a]
            tables_b = page_tables[p_b]

            # 只对"前一页最后1表 + 后一页第1表"这种潜在的跨页拆分做提示
            if len(tables_a) == 0 or len(tables_b) == 0:
                continue

            last_a = tables_a[-1]
            first_b = tables_b[0]

            # 列数特征快速检查
            cols_a = max((len(r) for r in last_a.get("data", [])), default=0)
            cols_b = max((len(r) for r in first_b.get("data", [])), default=0)
            if cols_a == 0 or cols_b == 0:
                continue
            if abs(cols_a - cols_b) > 2:
                continue  # 列数差异太大，不太可能是一个表

            cross_section = self._format_cross_page_section(
                p_a, p_b, last_a, first_b, liteparse_data
            )
            if cross_section:
                sections.append(cross_section)

        return "\n\n".join(sections) if sections else ""

    def _format_cross_page_section(self, page_a, page_b, table_a, table_b, liteparse_data):
        """格式化跨页相邻表的版面参考块。"""
        idx_a = table_a.get("__index__", "?")
        idx_b = table_b.get("__index__", "?")

        lines = []
        lines.append(f"## 跨页相邻检查：第{page_a}页 → 第{page_b}页")
        lines.append(f"⚠️ 表格#{idx_a}（P{page_a}最后一张）和表格#{idx_b}（P{page_b}第一张）可能属于同一张跨页表格。")
        lines.append("请判断是否应合并。")
        lines.append("")

        # 前页最后一张表的尾部
        lp_a = self._get_liteparse_page_dict(liteparse_data, page_a)
        if lp_a:
            regions_a = lp_a.get("table_regions", [])
            if regions_a:
                last_region = regions_a[-1]
                rt = last_region.get("region_text", "")
                if rt:
                    lines.append(f"**第{page_a}页 最后一个表格区域尾部**:")
                    tail_lines = rt.split("\n")
                    lines.append("\n".join(tail_lines[-8:]))  # 最后 8 行
                    lines.append("")

        # 后页第一个区域的头部
        lp_b = self._get_liteparse_page_dict(liteparse_data, page_b)
        if lp_b:
            regions_b = lp_b.get("table_regions", [])
            if regions_b:
                first_region = regions_b[0]
                rt = first_region.get("region_text", "")
                ctx = first_region.get("context_text", "")
                if ctx.strip():
                    lines.append(f"**第{page_b}页 第一个表格区域上方文字**: {ctx.strip()[:200]}")
                    lines.append("")
                if rt:
                    lines.append(f"**第{page_b}页 第一个表格区域头部**:")
                    head_lines = rt.split("\n")
                    lines.append("\n".join(head_lines[:8]))  # 前 8 行
                    lines.append("")

        lines.append("---")
        return "\n".join(lines)

    def _load_liteparse_data(self, context):
        """加载 liteparse 缓存数据。"""
        if not context:
            return None
        try:
            from codes.liteparse_extractor.cache_manager import load_parse_result
            pdf_path = getattr(context, "pdf_path", None)
            if not pdf_path:
                return None
            result = load_parse_result(pdf_path)
            return result.to_dict() if result else None
        except Exception:
            return None

    @staticmethod
    def _get_liteparse_page_dict(liteparse_data, page_num):
        """从 liteparse dict 中获取指定页。"""
        pages = liteparse_data.get("pages", [])
        for p in pages:
            if p.get("page_number") == page_num:
                return p
        return None

    def _format_page_section(self, page_num, page_ts, regions, lp_page):
        """格式化单页的 liteparse 版面参考块。

        包含：
        - 区域数量概览
        - 每个区域的 bbox + 上方描述文字 + 区域文本（截断）
        - 与 pdf2docx 表的对照提示
        """
        full_text = lp_page.get("full_text", "")
        text_items = lp_page.get("text_items", [])

        lines = []
        lines.append(f"## 第{page_num}页 版面参考（liteparse 文本层分析，请优先参考）")
        lines.append(f"该页检测到 {len(regions)} 个表格区域。")

        # 列出同页的 pdf2docx 表
        table_indices = [t.get("__index__", "?") for t in page_ts]
        lines.append(f"pdf2docx 产出了 {len(page_ts)} 张表（表号 {table_indices}）。")

        # 区域数 vs 表数 不匹配时重点提示
        if len(regions) != len(page_ts):
            if len(regions) == 1:
                lines.append(f"⚠️ 本页 liteparse 只有 1 个表格区域，但 pdf2docx 拆成了 {len(page_ts)} 张表")
                lines.append("→ 请重点判断这些表是否应合并为一张完整表格！")
            else:
                lines.append(f"⚠️ liteparse {len(regions)} 个区域 ≠ pdf2docx {len(page_ts)} 张表，请仔细对账")

        lines.append("")

        # 逐区域输出
        for idx, region in enumerate(regions):
            y0 = region.get("y0", 0)
            y1 = region.get("y1", 0)
            x0 = region.get("x0", 0)
            x1 = region.get("x1", 0)
            context_text = region.get("context_text", "")
            region_text = region.get("region_text", "")

            lines.append(f"### 区域{idx + 1}（Y: {y0:.0f}→{y1:.0f}pt, X: {x0:.0f}→{x1:.0f}pt）")
            lines.append("")

            if context_text.strip():
                lines.append("**上方描述文字**（可能是表格标题）:")
                ctx_preview = context_text.strip()[:300]
                lines.append(ctx_preview)
                lines.append("")

            if region_text.strip():
                lines.append("**区域文本**（保留原始列对齐格式，用于判断列边界和层级缩进）:")
                # 截断到合理长度
                region_lines = region_text.split("\n")
                if len(region_lines) > 30:
                    region_lines = region_lines[:30]
                    region_lines.append(f"... （共 {len(region_text.split(chr(10)))} 行，仅显示前 30 行）")
                lines.append("\n".join(region_lines))
                lines.append("")

            lines.append("---")

        return "\n".join(lines)

    def _format_check_result(self, check_result):
        """格式化规则预检结果为人类可读文本"""
        if not check_result:
            return "（无异常）"
        lines = []
        for k, v in check_result.items():
            if k == "empty_rows":
                lines.append(f"- 存在 {len(v)} 个空行（行 {v[:5]}...）" if len(v) > 5
                             else f"- 存在 {len(v)} 个空行（行 {v}）")
            elif k == "inconsistent_cols":
                lines.append(f"- 行 {v[:5]} 列数不一致")
            elif isinstance(v, list):
                lines.append(f"- {k}: {v}")
            elif isinstance(v, bool) and v:
                lines.append(f"- {k}")
            elif isinstance(v, bool) and not v:
                pass
            else:
                lines.append(f"- {k}: {v}")
        return "\n".join(lines) if lines else "（无异常）"

    def _call_api(self, system_prompt, user_prompt, max_retries=2):
        """调用 LLM API，含重试机制 + JSON 解析失败时自动重试

        Returns:
            (parsed_data, usage_dict) 或 ({"error": ...}, {})
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": self.MAX_OUTPUT_TOKENS,
            "temperature": 0.1
        }

        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(self.api_url, headers=headers, json=data, timeout=120)
                resp.raise_for_status()
                result = resp.json()

                # 提取 usage 信息
                usage = self._extract_usage(result)

                if 'choices' in result and len(result['choices']) > 0:
                    content = result['choices'][0]['message']['content']
                    parsed = self._try_parse_json(content)

                    # JSON 解析成功
                    if "error" not in parsed:
                        return parsed, usage

                    # JSON 解析失败 → 尝试重试（将错误反馈给 LLM）
                    if self.MAX_JSON_RETRIES > 0 and attempt < max_retries:
                        json_error = parsed.get("error", "未知解析错误")
                        print(f"[LLM] JSON 解析失败 (尝试 {attempt + 1}): {json_error}，正在重试...")

                        # 构建修复请求
                        data["messages"].append(
                            {"role": "assistant", "content": content}
                        )
                        data["messages"].append({
                            "role": "user",
                            "content": (
                                f"你上次的回复无法解析为有效 JSON。错误信息: {json_error}\n\n"
                                "请严格按照 JSON 格式重新输出，确保：\n"
                                "1. 所有字符串使用双引号\n"
                                "2. 没有尾随逗号\n"
                                "3. 括号完整闭合\n"
                                "4. 不要包含 markdown 代码块标记\n"
                                "5. 只返回纯 JSON"
                            )
                        })
                        # 增加 temperature 避免重复同样错误
                        data["temperature"] = min(0.3, data["temperature"] + 0.1)
                        continue

                    return parsed, usage
                else:
                    return {"error": "API 返回格式错误", "raw": str(result)}, usage

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return {"error": "API 请求超时"}, {}
            except requests.exceptions.HTTPError as e:
                return {"error": f"HTTP {e.response.status_code}: {e.response.reason}"}, {}
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return {"error": str(e)}, {}

        return {"error": "所有重试均失败"}, {}

    def _try_parse_json(self, content):
        """尝试多种方式解析 JSON"""
        # 方式 1：直接解析
        try:
            return _json.loads(content)
        except _json.JSONDecodeError:
            pass

        # 方式 2：提取 ```json ... ``` 代码块
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if match:
            try:
                return _json.loads(match.group(1).strip())
            except _json.JSONDecodeError:
                pass

        # 方式 3：提取最外层 {...} 或 [...]
        for pattern in [r'\{[\s\S]*\}', r'\[[\s\S]*\]']:
            match = re.search(pattern, content)
            if match:
                try:
                    return _json.loads(match.group())
                except _json.JSONDecodeError:
                    pass

        # 方式 4：去除前后空白后再次尝试
        try:
            return _json.loads(content.strip().lstrip('`').rstrip('`'))
        except _json.JSONDecodeError:
            pass

        return {"error": "JSON 解析失败", "raw": content[:500]}

    def _extract_usage(self, api_response):
        """从 API 响应中提取 token 使用量并估算成本"""
        usage_raw = api_response.get("usage", {})
        if not usage_raw:
            return {}

        prompt_tokens = usage_raw.get("prompt_tokens", 0)
        completion_tokens = usage_raw.get("completion_tokens", 0)
        total_tokens = usage_raw.get("total_tokens", prompt_tokens + completion_tokens)

        # 估算成本
        pricing = self.PRICING.get(self.model, {"input": 0, "output": 0})
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        cost = input_cost + output_cost

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_estimate": round(cost, 6),
            "model": self.model,
        }

    def _accumulate_usage(self, usage):
        """累计 token 使用量到 total_usage"""
        if not usage:
            return
        self.total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        self.total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
        self.total_usage["total_tokens"] += usage.get("total_tokens", 0)
        self.total_usage["cost_estimate"] += usage.get("cost_estimate", 0)
        self.total_usage["cost_estimate"] = round(self.total_usage["cost_estimate"], 6)
        self.total_usage["api_calls"] += 1

    def _parse_response(self, response_data, tables, batch_usage=None):
        """解析 LLM 响应为 CorrectionResult 列表

        Args:
            response_data: LLM 返回的 JSON 数据
            tables: 原始表格列表
            batch_usage: 本批次的 token 消耗统计（可选）
        """
        results = []

        # 容错：LLM 可能直接返回 JSON 数组 [{...}, {...}] 而非 {"tables": [...]}
        if isinstance(response_data, list):
            llm_tables = response_data
        elif isinstance(response_data, dict):
            if "error" in response_data:
                # LLM 调用失败 → 所有表降级为 needs_review
                for t in tables:
                    results.append(CorrectionResult(
                        table_index=t.get("__index__", 0),
                        status="needs_review",
                        confidence="medium",
                        changes_summary=f"LLM 分析失败: {response_data.get('error', '未知错误')}"
                    ))
                return results
            llm_tables = response_data.get("tables", [])
        else:
            # 既非 list 也非 dict → 降级处理
            for t in tables:
                results.append(CorrectionResult(
                    table_index=t.get("__index__", 0),
                    status="needs_review",
                    confidence="medium",
                    changes_summary="LLM 返回格式异常，无法解析"
                ))
            return results
        if not isinstance(llm_tables, list):
            for t in tables:
                results.append(CorrectionResult(
                    table_index=t.get("__index__", 0),
                    status="needs_review",
                    confidence="medium",
                    changes_summary="LLM 返回格式异常，无法解析"
                ))
            return results

        # 创建 table_index → CorrectionResult 的快速查找
        llm_map = {
            lt.get("table_index", -1): lt
            for lt in llm_tables
        }

        for t in tables:
            idx = t.get("__index__", 0)
            lt = llm_map.get(idx, {})

            result = CorrectionResult(table_index=idx)

            # 命名
            name = lt.get("name", {})
            result.name_title = name.get("title", "") if isinstance(name, dict) else ""
            result.name_summary = name.get("summary", "") if isinstance(name, dict) else ""

            # == LLM 重构数据（核心新增）==
            reconstructed = lt.get("reconstructed_data")
            if isinstance(reconstructed, list) and reconstructed:
                result.reconstructed_data = reconstructed
                result.corrected_data = reconstructed  # 同时填充 corrected_data 兼容旧逻辑

            merge_sources = lt.get("merge_source_indices")
            if isinstance(merge_sources, list) and merge_sources:
                result.merge_source_indices = merge_sources

            changes_log = lt.get("changes_log")
            if isinstance(changes_log, list) and changes_log:
                result.changes_log = changes_log

            # 层级
            hierarchy = lt.get("hierarchy", [])
            if isinstance(hierarchy, list) and hierarchy:
                result.hierarchy = hierarchy

            # 区域
            region = lt.get("region", {})
            if isinstance(region, dict):
                result.region_is_complete = region.get("is_complete", True)
                result.region_merge_prev = region.get("merge_prev")
                result.region_merge_next = region.get("merge_next")
                split_rows = region.get("split_at_rows")
                if isinstance(split_rows, list):
                    result.region_split_rows = split_rows

            # 修正
            corrections = lt.get("corrections", [])
            if isinstance(corrections, list) and corrections:
                result.applied_corrections = corrections
                # 应用 LLM 修正到数据
                result.corrected_data = self._apply_corrections(
                    t.get("data", []), corrections
                )

            # 问题列表
            issues = lt.get("issues", [])
            if isinstance(issues, list) and issues:
                result.unresolved_issues = issues

            # 置信度
            h_conf = lt.get("hierarchy_confidence", "high")
            c_conf = lt.get("corrections_confidence", "high")
            r_conf = region.get("confidence", "high") if isinstance(region, dict) else "high"
            result.confidence = min_confidence(min_confidence(h_conf, c_conf), r_conf)

            # 状态
            if result.confidence == "high" and not result.unresolved_issues:
                if result.applied_corrections or result.corrected_data:
                    result.status = "llm_analyzed"
                else:
                    result.status = "clean" if result.name_title else "auto_fixed"
            elif result.confidence == "unresolvable":
                result.status = "unresolvable"
            else:
                result.status = "needs_review"

            # 变更摘要
            parts = []
            if result.name_title:
                parts.append(f"命名: {result.name_title}")
            if result.reconstructed_data:
                parts.append(f"重构: {len(result.reconstructed_data)}行×{max((len(r) for r in result.reconstructed_data), default=0)}列")
            if result.merge_source_indices:
                parts.append(f"已合并: 表{result.merge_source_indices}")
            if result.changes_log:
                stats = {}
                for entry in result.changes_log:
                    t = entry.get("type", "unknown")
                    stats[t] = stats.get(t, 0) + 1
                parts.append(f"变更: {', '.join(f'{v}处{k}' for k, v in stats.items())}")
            if result.hierarchy:
                total_rows = sum(1 for h in result.hierarchy
                                 if h.get("type") in ("subtotal", "total"))
                parts.append(f"层级: {len(result.hierarchy)}行标记（含{total_rows}个合计）")
            if result.applied_corrections:
                parts.append(f"修正: {len(result.applied_corrections)}处")
            if result.region_merge_prev is not None:
                parts.append(f"应向前合并到表#{result.region_merge_prev}")
            if result.region_merge_next is not None:
                parts.append(f"应与后表#{result.region_merge_next}合并")
            if result.unresolved_issues:
                parts.append(f"待确认: {len(result.unresolved_issues)}项问题")

            result.changes_summary = "; ".join(parts) if parts else "无需更改"

            # 多模态预留
            result.layout_analysis = None

            # Token 消耗统计（每批次只第一个表携带 usage，避免重复累加）
            first_batch_idx = tables[0].get("__index__", -1) if tables else -1
            if idx == first_batch_idx and batch_usage:
                result.usage = batch_usage

            results.append(result)

        return results

    def _apply_corrections(self, data, corrections):
        """应用 LLM 返回的修正到数据副本"""
        if not data or not corrections:
            return None

        corrected = _deep_copy_table(data)

        # 按操作类型排序：delete_col 需要从后往前处理
        delete_col_indices = sorted(
            [c["col"] for c in corrections if c.get("action") == "delete_col"],
            reverse=True
        )
        insert_col_info = [c for c in corrections if c.get("action") == "insert_col"]

        # 先处理 delete_col
        for col_idx in delete_col_indices:
            for row in corrected:
                if col_idx < len(row):
                    row.pop(col_idx)

        # 处理其他修正（change, merge_cells, split_cell）
        for corr in corrections:
            action = corr.get("action", "change")
            r = corr.get("row", -1)
            c = corr.get("col", -1)

            if action == "change":
                new_val = corr.get("new_value", "")
                if 0 <= r < len(corrected) and 0 <= c < len(corrected[r]):
                    corrected[r][c] = str(new_val) if new_val is not None else ""

            elif action == "merge_cells":
                # 合并多个单元格（如垂直拆分的表头）
                cells = corr.get("cells", [])
                merged_text = corr.get("merged_text", "")
                if cells and merged_text:
                    target_row, target_col = cells[0]["row"], cells[0]["col"]
                    if 0 <= target_row < len(corrected) and 0 <= target_col < len(corrected[target_row]):
                        corrected[target_row][target_col] = merged_text
                    for cell_info in cells[1:]:
                        cr, cc = cell_info["row"], cell_info["col"]
                        if 0 <= cr < len(corrected) and 0 <= cc < len(corrected[cr]):
                            corrected[cr][cc] = ""

        return corrected

    def _fallback_results(self, tables):
        """无 API Key 时的降级结果"""
        results = []
        for t in tables:
            results.append(CorrectionResult(
                table_index=t.get("__index__", 0),
                status="needs_review",
                confidence="low",
                changes_summary="未配置 API Key，跳过 LLM 分析"
            ))
        return results

    # ==================== 模板管理接口 ====================

    def get_template_list(self):
        """获取可用模板列表 [(id, name), ...]"""
        return self._template_mgr.get_template_list()

    def get_default_template_id(self):
        """获取默认模板 ID"""
        return self._template_mgr.get_default_template_id()

    def apply_template(self, template_id, tables, check_results, context=None):
        """应用指定模板构建 prompt

        Args:
            template_id: 模板 ID
            tables: 表格列表
            check_results: 预检结果
            context: PDFContext 实例（可选）

        Returns:
            (system_prompt, user_prompt) 或 (None, None) 如果模板不存在
        """
        sys_tpl, usr_tpl = self._template_mgr.get_template(template_id)
        if not sys_tpl and not usr_tpl:
            return None, None

        # 如果模板没有 user_template，使用默认构建
        if not usr_tpl:
            _, usr_tpl = self._build_prompt(tables, check_results, context)
            return sys_tpl, usr_tpl

        # 用模板的 user_template 构建（替换变量）
        total_pages = max((t.get("page", 1) for t in tables), default=1)
        table_blocks = self._build_table_blocks(tables, check_results, context)
        liteparse_sections = self._build_liteparse_sections(tables, context)
        user_prompt = usr_tpl.replace("{total_pages}", str(total_pages))
        user_prompt = user_prompt.replace("{table_count}", str(len(tables)))
        user_prompt = user_prompt.replace("{table_blocks}", "".join(table_blocks))
        user_prompt = user_prompt.replace("{liteparse_sections}", liteparse_sections)

        return sys_tpl, user_prompt

    def _build_table_blocks(self, tables, check_results, context):
        """构建表格块文本（提取自 _build_prompt，供模板化使用）"""
        total_pages = max((t.get("page", 1) for t in tables), default=1)
        table_blocks = []
        for t in tables:
            idx = t.get("__index__", 0)
            page = t.get("page", 1)
            ext = t.get("extractor", "unknown")
            ctx = self._enrich_context(t, context)
            preview = self._build_table_preview(t.get("data", []))
            ck = check_results.get(idx, {})

            block = f"""---
### 表格 #{idx}（PDF 第 {page} 页 / 共 {total_pages} 页，提取方式 {ext}）

#### 规则预检：
{self._format_check_result(ck)}

#### 表格上方文字：
{ctx if ctx else "（无）"}

#### 表格数据：
{preview}"""
            table_blocks.append(block)
        return table_blocks

    def get_total_usage(self):
        """获取累计 token 消耗统计"""
        return dict(self.total_usage)

    # ==================== 多模态扩展接口（预留） ====================

    def build_prompt_for_preview(self, tables, check_results, context=None):
        """
        公开方法：为预览编辑构建 prompt，不调用 API。
        供 UI PromptEditDialog 调用。

        Args:
            tables: 表格列表
            check_results: {table_index: check_result_dict}
            context: PDFContext 实例（可选）

        Returns:
            (system_prompt, user_prompt) 两个字符串
        """
        return self._build_prompt(tables, check_results, context)

    def _build_multimodal_prompt(self, tables, check_results, context):
        """
        如果未来启用多模态，在此方法中构建包含图片的 prompt。
        只需修改此方法 + 切换 PROMPT_MODE = "multimodal" 即可。
        """
        raise NotImplementedError("多模态模式待实现")


# ============================================================
# 数值交叉验证
# ============================================================

def _verify_hierarchy_numeric(data, hierarchy):
    """验证 LLM 标记的合计关系是否与数据一致

    Args:
        data: 表格数据 [[cell, ...], ...]
        hierarchy: LLM 返回的层级列表 [{"row": ..., "type": "subtotal", "total_of_rows": [...]}, ...]

    Returns:
        (all_verified: bool, discrepancies: [dict])
    """
    if not data or not hierarchy:
        return True, []

    discrepancies = []

    for entry in hierarchy:
        total_of_rows = entry.get("total_of_rows")
        if not total_of_rows:
            continue

        total_row = entry.get("row", -1)
        if total_row < 0 or total_row >= len(data):
            continue

        # 找第一个数值列（跳过标签列，通常在第 2+ 列）
        numeric_col = None
        for c in range(len(data[total_row]) if total_row < len(data) else 0):
            if _is_numeric(data[total_row][c]):
                numeric_col = c
                break

        if numeric_col is None:
            # 无法确定数值列，跳过验证
            continue

        # 计算子项和
        child_sum = 0.0
        valid_children = 0
        for child_row in total_of_rows:
            if 0 <= child_row < len(data) and numeric_col < len(data[child_row]):
                child_val = str(data[child_row][numeric_col]).strip().rstrip('%').replace(',', '')
                if child_val.startswith('(') and child_val.endswith(')'):
                    child_val = '-' + child_val[1:-1]
                try:
                    child_sum += float(child_val)
                    valid_children += 1
                except (ValueError, TypeError):
                    pass

        if valid_children == 0:
            continue

        # 获取合计值
        total_val_str = str(data[total_row][numeric_col]).strip().rstrip('%').replace(',', '')
        if total_val_str.startswith('(') and total_val_str.endswith(')'):
            total_val_str = '-' + total_val_str[1:-1]
        try:
            total_val = float(total_val_str)
        except (ValueError, TypeError):
            continue

        # 比较（允许 1% 误差）
        if abs(total_val) > 0.01:
            rel_error = abs(child_sum - total_val) / abs(total_val)
        else:
            rel_error = abs(child_sum - total_val)

        if rel_error > 0.01:
            discrepancies.append({
                "type": "hierarchy_verification_failed",
                "confidence": "medium",
                "description": (
                    f"合计验证失败：行{total_row}（{entry.get('label', '合计')}）"
                    f"值={total_val}，子项({total_of_rows})和={child_sum}，"
                    f"差异={abs(child_sum - total_val):.2f}"
                ),
                "suggested_action": "请人工核对层级关系和数值"
            })

    return len(discrepancies) == 0, discrepancies


# ============================================================
# 多标签分类汇总
# ============================================================

def _build_error_tags(result):
    """汇总所有层的结果为统一标签列表，供 UI 标签化展示。

    Args:
        result: CorrectionResult 实例

    Returns:
        [{"key": "empty_rows", "label": "空行", "category": "auto_fixed", "detail": "共2行"}, ...]
    """
    tags = []
    ck = getattr(result, 'check_result', {}) or {}

    # ========== 分类 1: 已自动修复（绿色） ==========
    AUTO_FIXED_MAP = {
        "empty_rows":         ("空行", lambda v: f"共{len(v) if isinstance(v, list) else '?'}行"),
        "leading_whitespace": ("前导空格", lambda v: "已Trim"),
        "trailing_whitespace":("尾部空格", lambda v: "已Trim"),
        "irregular_number":   ("数值格式", lambda v: "已统一"),
    }
    for key, (label, detail_fn) in AUTO_FIXED_MAP.items():
        if key in ck:
            detail = detail_fn(ck[key])
            tags.append({"key": key, "label": label, "category": "auto_fixed", "detail": detail})

    # ========== 分类 2: 需确认（黄色） ==========
    NEEDS_REVIEW_MAP = {
        "inconsistent_cols":  ("列不对齐", lambda v: f"行{v[:3]}" if isinstance(v, list) else str(v)),
        "split_header":       ("疑似拆表头", lambda v: "需确认"),
        "null_header_col":    ("空列表头", lambda v: f"列{v[:3]}" if isinstance(v, list) else str(v)),
        "single_row":         ("单行表", lambda v: "可能非表格"),
        "orphan_numeric_row": ("首行纯数字", lambda v: "疑似跨页续行"),
        "no_numeric":         ("无数值", lambda v: "可能非数据表"),
    }
    for key, (label, detail_fn) in NEEDS_REVIEW_MAP.items():
        if key in ck:
            detail = detail_fn(ck[key])
            tags.append({"key": key, "label": label, "category": "needs_review", "detail": detail})

    # ========== 分类 3: LLM 深度分析（蓝色） ==========
    if result.name_title:
        tags.append({
            "key": "llm_naming", "label": "AI命名",
            "category": "llm_analyzed",
            "detail": result.name_title
        })
    if result.reconstructed_data:
        rows = len(result.reconstructed_data)
        cols = max((len(r) for r in result.reconstructed_data), default=0)
        tags.append({
            "key": "llm_reconstructed", "label": "表格重构",
            "category": "llm_analyzed",
            "detail": f"{rows}行×{cols}列"
        })
    if result.merge_source_indices:
        tags.append({
            "key": "llm_merged", "label": "已合并表",
            "category": "llm_analyzed",
            "detail": f"来源: {result.merge_source_indices}"
        })
    if result.changes_log:
        tags.append({
            "key": "llm_changes_log", "label": "变更日志",
            "category": "llm_analyzed",
            "detail": f"{len(result.changes_log)}条记录"
        })
    if result.hierarchy:
        tags.append({
            "key": "llm_hierarchy", "label": "层级识别",
            "category": "llm_analyzed",
            "detail": f"{len(result.hierarchy)}行标记"
        })
    if result.applied_corrections:
        tags.append({
            "key": "llm_corrections", "label": "单元格修正",
            "category": "llm_analyzed",
            "detail": f"{len(result.applied_corrections)}处"
        })
    if result.region_merge_prev is not None:
        tags.append({
            "key": "llm_region_merge", "label": "区域合并",
            "category": "llm_analyzed",
            "detail": f"←表#{result.region_merge_prev}"
        })
    if result.region_merge_next is not None:
        tags.append({
            "key": "llm_region_merge", "label": "区域合并",
            "category": "llm_analyzed",
            "detail": f"→表#{result.region_merge_next}"
        })
    if result.region_split_rows:
        tags.append({
            "key": "llm_region_split", "label": "区域拆分",
            "category": "llm_analyzed",
            "detail": f"行{result.region_split_rows}"
        })

    # ========== 分类 4: 验证结果 ==========
    if result.hierarchy_verified:
        tags.append({
            "key": "hierarchy_verified", "label": "合计验证通过",
            "category": "verified",
            "detail": "✅"
        })
    elif result.hierarchy:
        tags.append({
            "key": "hierarchy_mismatch", "label": "合计不符",
            "category": "needs_review",
            "detail": f"{len(result.unresolved_issues)}项差异"
        })

    # ========== 分类 5: 确定性验证/修复（绿色） ==========
    if result.status == "verified":
        tags.append({
            "key": "liteparse_verified", "label": "✅ 逐行验证通过",
            "category": "verified",
            "detail": "与 liteparse 原文完全一致"
        })
    if result.status == "auto_fixed_code":
        tags.append({
            "key": "code_fixed", "label": "🔧 代码自动修复",
            "category": "auto_fixed",
            "detail": result.changes_summary or "基于 liteparse 确定性修复"
        })

    # ========== 分类 6: 置信度阶梯标记 ==========
    if result.confidence == "high" and result.status == "verified":
        tags.append({
            "key": "confidence_high", "label": "置信度: 高",
            "category": "verified",
            "detail": "liteparse ↔ data 逐行匹配"
        })
    elif result.confidence == "high" and result.status in ("auto_fixed_code", "auto_fixed"):
        tags.append({
            "key": "confidence_high", "label": "置信度: 高",
            "category": "auto_fixed",
            "detail": "确定性规则修复"
        })
    elif result.confidence == "medium":
        tags.append({
            "key": "confidence_medium", "label": "置信度: 中",
            "category": "needs_review",
            "detail": "需人工核对"
        })
    elif result.confidence == "low":
        tags.append({
            "key": "confidence_low", "label": "置信度: 低",
            "category": "needs_review",
            "detail": "LLM 不确定"
        })

    # ========== 分类 7: 元信息 ==========
    if result.status == "no_liteparse":
        tags.append({
            "key": "no_liteparse_ref", "label": "无 liteparse",
            "category": "needs_review",
            "detail": "缺少权威原文参照"
        })
    if not result.name_title and not result.hierarchy and result.diff_source not in ("llm", "needs_llm"):
        tags.append({
            "key": "rule_only", "label": "仅规则检查",
            "category": "meta",
            "detail": "未触发 LLM 分析"
        })

    if result.status == "unresolvable":
        tags.append({
            "key": "unresolvable", "label": "无法处理",
            "category": "needs_review",
            "detail": result.changes_summary or "需人工介入"
        })

    return tags


# ============================================================
# Phase 4: 确定性差异分析（零 API 成本，LLM 之前执行）
# ============================================================

def _get_scoped_liteparse_items_for_table(table, all_tables_on_page, liteparse_data):
    """获取单表的 liteparse scoped text_items。

    优先使用已有 _liteparse_items，否则尝试匹配 region。
    """
    # 1. 已有预计算
    items = table.get("_liteparse_items")
    if items:
        return items

    # 2. 从 liteparse_data 获取
    if not liteparse_data:
        return None

    page_num = table.get("page", 0)
    lp_page = None
    for p in liteparse_data.get("pages", []):
        if p.get("page_number") == page_num:
            lp_page = p
            break

    if not lp_page:
        return None

    # 3. 尝试 scoped（同页多表限定区域）
    if all_tables_on_page and len(all_tables_on_page) > 1:
        try:
            from codes.table_validator.table_boundary import get_scoped_items_for_table
            scoped = get_scoped_items_for_table(table, all_tables_on_page, lp_page)
            if scoped:
                return scoped
        except Exception:
            pass

    # 4. 降级：全页 text_items
    return lp_page.get("text_items", []) or None


def _deterministic_diff_analysis(table, liteparse_data):
    """对单张表做代码级 liteparse 差异分析。

    Returns:
        (diff_level, diff_data):
        - ("clean", None)            — liteparse 逐行匹配，无需修改
        - ("auto_fixable", report)   — 代码可确定性修复
        - ("needs_llm", report)      — 需要 LLM 判断
        - ("no_liteparse", None)     — 无 liteparse 参考
    """
    from codes.table_validator.cell_differ import (
        diff_table_with_liteparse,
        classify_rows_with_liteparse,
    )

    data = table.get("data", [])
    if not data:
        return ("clean", None)

    # 获取 liteparse text_items
    page_num = table.get("page", 0)
    all_on_page = table.get("_all_tables_on_page")
    items = _get_scoped_liteparse_items_for_table(table, all_on_page, liteparse_data)

    if not items:
        return ("no_liteparse", None)

    # 行级分类
    row_classify = classify_rows_with_liteparse(data, items)

    # 逐单元格对比
    cell_diff = diff_table_with_liteparse(data, items)

    # 汇总差异信号
    has_phantom = any(
        s.get("status") == "phantom"
        for s in row_classify.get("row_status", {}).values()
    )
    has_extra_rows = any(
        s.get("status") == "extra"
        for s in row_classify.get("row_status", {}).values()
    )
    has_missing = len(row_classify.get("missing_rows", [])) > 0
    has_cell_diffs = len(cell_diff.get("cell_diffs", {})) > 0
    has_extra_cols = len(cell_diff.get("extra_cols", {})) > 0
    has_missing_cols = len(cell_diff.get("missing_cols", {})) > 0
    has_unmatched_items = len(cell_diff.get("unmatched_items", [])) > 0

    # 统计 real 行（正常的）
    real_count = sum(
        1 for s in row_classify.get("row_status", {}).values()
        if s.get("status") == "real"
    )

    # 收集全部差异信号
    diff_signals = {
        "phantom": has_phantom,
        "extra_rows": has_extra_rows,
        "missing_rows": has_missing,
        "cell_diffs": has_cell_diffs,
        "extra_cols": has_extra_cols,
        "missing_cols": has_missing_cols,
        "unmatched_items": has_unmatched_items,
        "real_count": real_count,
    }

    # 判断差异等级
    total_rows = len(data)
    any_issue = (has_phantom or has_extra_rows or has_missing or
                 has_cell_diffs or has_extra_cols or has_missing_cols)

    if not any_issue:
        return ("clean", None)

    # 仅幽灵行 (phantom)：确定性可修复
    if has_phantom and not has_cell_diffs and not has_extra_cols and not has_missing_cols:
        report = {
            "row_classify": row_classify,
            "cell_diff": cell_diff,
            "diff_signals": diff_signals,
        }
        return ("auto_fixable", report)

    # 仅空行差异（已由 L2 处理，这里作为补充）
    # 有单元格差异 + 差异较少 → 代码可以先修复确定性的部分
    cell_diff_count = len(cell_diff.get("cell_diffs", {}))
    if cell_diff_count <= 3 and not has_missing_cols and not has_unmatched_items:
        report = {
            "row_classify": row_classify,
            "cell_diff": cell_diff,
            "diff_signals": diff_signals,
        }
        return ("auto_fixable", report)

    # 复杂差异 → 需要 LLM
    report = {
        "row_classify": row_classify,
        "cell_diff": cell_diff,
        "diff_signals": diff_signals,
    }
    return ("needs_llm", report)


def _apply_deterministic_fix(table, diff_report):
    """基于确定性差异分析报告，代码层面修复表格数据。

    Returns:
        (corrected_data dict, changes_summary str)
    """
    import copy
    corrected = copy.deepcopy(table.get("data", []))
    changes = []

    if not diff_report or not corrected:
        return corrected, changes

    row_classify = diff_report.get("row_classify", {})
    cell_diff = diff_report.get("cell_diff", {})
    row_status = row_classify.get("row_status", {})

    original_len = len(corrected)

    # 1. 移除幽灵行 (phantom) — 从后往前删
    phantom_indices = sorted(
        [int(k) for k, v in row_status.items() if v.get("status") == "phantom"],
        reverse=True
    )

    # 🆕 构建旧索引 → 新索引映射（供后续 cell_diff 等引用重映射）
    old_to_new = {i: i for i in range(original_len)}

    for idx in phantom_indices:
        if 0 <= idx < len(corrected):
            corrected.pop(idx)
            changes.append(f"删除幽灵行{idx}")

    # 🆕 重映射：删除幽灵行后，重建 old_to_new 映射
    if phantom_indices:
        new_idx = 0
        old_to_new = {}
        for old_i in range(original_len):
            if old_i not in phantom_indices:
                old_to_new[old_i] = new_idx
                new_idx += 1
            else:
                old_to_new[old_i] = -1  # 已删除

    # 2. 删除多余列 (extra_cols) — 使用重映射后的行号
    for row_idx_str, cols in cell_diff.get("extra_cols", {}).items():
        row_idx = int(row_idx_str)
        # 🆕 重映射行号
        if phantom_indices:
            row_idx = old_to_new.get(row_idx, -1)
        if row_idx < 0 or row_idx >= len(corrected):
            continue
        for col in sorted(cols, reverse=True):
            if col < len(corrected[row_idx]):
                corrected[row_idx].pop(col)
                changes.append(f"行{row_idx}删除多余列{col}")

    # 3. 修正单元格差异（以 liteparse 为准）— 使用重映射后的行号
    for key, diff_info in cell_diff.get("cell_diffs", {}).items():
        if diff_info.get("status") != "suspicious":
            continue
        parts = key.split(",")
        if len(parts) != 2:
            continue
        r, c = int(parts[0]), int(parts[1])
        # 🆕 重映射行号
        if phantom_indices:
            r = old_to_new.get(r, -1)
        if r < 0 or r >= len(corrected):
            continue
        lp_val = diff_info.get("liteparse_value", "")
        pdf_val = diff_info.get("cell_value", "")
        if lp_val and c < len(corrected[r]):
            corrected[r][c] = lp_val
            changes.append(f"单元格({r},{c}): '{pdf_val}' → '{lp_val}'")

    return corrected, changes


# ============================================================
# 纠错日志记录器
# ============================================================

class _CorrectionLogger:
    """AI 纠错流程日志记录器 — 将每张表的各环节中间变化写入日志文件。

    日志文件保存到 get_pdf_cache_dir(pdf_path) / "ai_correction_log.txt"。
    每次运行覆盖上一次日志。
    """

    def __init__(self, pdf_path=None):
        self._lines = []
        self._log_path = None
        if pdf_path:
            try:
                cache_dir = get_pdf_cache_dir(pdf_path)
                cache_dir.mkdir(parents=True, exist_ok=True)
                self._log_path = cache_dir / "ai_correction_log.txt"
            except Exception:
                pass
        self._section_depth = 0

    # ---- 基础写入 ----

    def _add(self, text=""):
        self._lines.append(text)

    def header(self, title):
        self._add()
        self._add("=" * 72)
        self._add(f"  {title}")
        self._add("=" * 72)

    def section(self, title):
        self._add()
        self._add(f"{'─' * 3} {title} {'─' * (65 - len(title))}")

    def subsection(self, title):
        self._add(f"  ▸ {title}")

    def info(self, msg):
        self._add(f"    {msg}")

    def kv(self, key, value):
        """输出 key = value 行，value 过长时截断"""
        v_str = str(value)
        if len(v_str) > 120:
            v_str = v_str[:117] + "..."
        self._add(f"    {key} = {v_str}")

    def data_snapshot(self, label, data, max_rows=10, max_cols=8):
        """输出表格数据的简快照"""
        if not data:
            self._add(f"    {label}: （空）")
            return
        rows = len(data)
        cols = max((len(r) for r in data), default=0)
        self._add(f"    {label}: {rows}行 × {cols}列")
        show_rows = data[:max_rows]
        for ri, row in enumerate(show_rows):
            cells = []
            for ci, c in enumerate(row[:max_cols]):
                s = str(c).strip() if c is not None else ""
                if len(s) > 18:
                    s = s[:15] + "..."
                cells.append(s)
            suffix = " ..." if len(row) > max_cols else ""
            self._add(f"      行{ri}: {' | '.join(cells)}{suffix}")
        if rows > max_rows:
            self._add(f"      ... (省略 {rows - max_rows} 行)")

    def diff_report(self, diff_data):
        """输出确定性差异分析报告的要点"""
        if not diff_data:
            self._add("    差异报告: 无")
            return
        rc = diff_data.get("row_classify", {})
        cd = diff_data.get("cell_diff", {})
        ds = diff_data.get("diff_signals", {})

        # 行分类
        row_status = rc.get("row_status", {})
        phantom_rows = [k for k, v in row_status.items() if v.get("status") == "phantom"]
        extra_rows = [k for k, v in row_status.items() if v.get("status") == "extra"]
        real_rows = [k for k, v in row_status.items() if v.get("status") == "real"]
        missing = rc.get("missing_rows", [])

        if real_rows:
            self._add(f"    正常行: {len(real_rows)} 行 {real_rows[:10]}")
        if phantom_rows:
            self._add(f"    幽灵行: {len(phantom_rows)} 行 → 删除 {phantom_rows}")
        if extra_rows:
            self._add(f"    多余行: {len(extra_rows)} 行 {extra_rows}")
        if missing:
            self._add(f"    缺失行: {len(missing)} 行 {missing[:5]}")

        # 单元格差异
        cell_diffs = cd.get("cell_diffs", {})
        extra_cols = cd.get("extra_cols", {})
        missing_cols = cd.get("missing_cols", {})
        unmatched = cd.get("unmatched_items", [])

        if cell_diffs:
            self._add(f"    单元格差异: {len(cell_diffs)} 处")
            for key, info in list(cell_diffs.items())[:8]:
                pdf_v = info.get("cell_value", "")
                lp_v = info.get("liteparse_value", "")
                status = info.get("status", "")
                self._add(f"      [{key}] '{pdf_v}' vs LP='{lp_v}' ({status})")
            if len(cell_diffs) > 8:
                self._add(f"      ... (省略 {len(cell_diffs) - 8} 处)")

        if extra_cols:
            self._add(f"    多余列: {len(extra_cols)} 行有 → 删除")
            for rk, cols in list(extra_cols.items())[:5]:
                self._add(f"      行{rk}: 列{cols}")
        if missing_cols:
            self._add(f"    缺失列: {len(missing_cols)} 行有")
        if unmatched:
            self._add(f"    未匹配 liteparse 项: {len(unmatched)} 个")

        # 信号汇总
        signals_on = [k for k, v in ds.items() if v and k != "real_count"]
        if signals_on:
            self._add(f"    差异信号: {', '.join(signals_on)}")

    def changes_detail(self, changes):
        """输出代码修复的变更明细"""
        if not changes:
            self._add("    变更: 无")
            return
        self._add(f"    变更明细 ({len(changes)} 处):")
        for c in changes[:20]:
            self._add(f"      - {c}")
        if len(changes) > 20:
            self._add(f"      ... (省略 {len(changes) - 20} 处)")

    def llm_result(self, lr):
        """输出 LLM 返回的 CorrectionResult 要点"""
        self.kv("状态", lr.status)
        self.kv("置信度", lr.confidence)
        if lr.name_title:
            self.kv("命名", lr.name_title)
        if lr.reconstructed_data:
            rows = len(lr.reconstructed_data)
            cols = max((len(r) for r in lr.reconstructed_data), default=0)
            self.kv("重构数据", f"{rows}行 × {cols}列")
        if lr.merge_source_indices:
            self.kv("合并来源表", lr.merge_source_indices)
        if lr.changes_log:
            self._add(f"    变更日志 ({len(lr.changes_log)} 条):")
            for entry in lr.changes_log[:10]:
                self._add(f"      {entry}")
            if len(lr.changes_log) > 10:
                self._add(f"      ... (省略 {len(lr.changes_log) - 10} 条)")
        if lr.hierarchy:
            self._add(f"    层级: {len(lr.hierarchy)} 行标记")
            for h in lr.hierarchy[:6]:
                self._add(f"      行{h.get('row', '?')}: {h.get('type', '?')} [{h.get('label', '')}]")
        if lr.applied_corrections:
            self._add(f"    修正: {len(lr.applied_corrections)} 处")
            for corr in lr.applied_corrections[:6]:
                self._add(f"      {corr}")
        if lr.unresolved_issues:
            self._add(f"    待确认问题: {len(lr.unresolved_issues)} 项")
            for iss in lr.unresolved_issues[:4]:
                self._add(f"      {iss.get('description', iss)}")
        if lr.region_merge_prev is not None:
            self.kv("区域合并←", f"表#{lr.region_merge_prev}")
        if lr.region_merge_next is not None:
            self.kv("区域合并→", f"表#{lr.region_merge_next}")
        self.kv("摘要", lr.changes_summary)

    def prompt_block(self, label, text, max_len=2000):
        """输出 prompt 片段（截断）"""
        self._add(f"    {label}:")
        if len(text) > max_len:
            self._add(text[:max_len])
            self._add(f"    ... (共 {len(text)} 字符，截断)")
        else:
            self._add(text)

    def save(self):
        """将日志写入文件"""
        if not self._log_path:
            return
        try:
            with open(self._log_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self._lines))
        except Exception as e:
            print(f"[CorrectionLogger] 保存日志失败: {e}")


# ============================================================
# Layer 4: CorrectionEngine 协调器
# ============================================================

class CorrectionEngine(QObject):
    """AI 纠错总协调器 — 串联 5 层管线：L1 规则预检 → L2 规则修复 → P4 确定性差异分析 → L3 LLM"""

    # 信号
    progress = pyqtSignal(int, str)           # 进度: (百分比, 消息)
    precheck_done = pyqtSignal(list)          # Layer 1 完成: [CorrectionResult]
    autofix_done = pyqtSignal(list)          # Layer 2 完成: [CorrectionResult]
    diff_analysis_done = pyqtSignal(list)    # Phase 4 完成: [CorrectionResult]
    llm_done = pyqtSignal(list)              # Layer 3 完成: [CorrectionResult]
    all_done = pyqtSignal(list)              # 全部完成: [CorrectionResult]

    def __init__(self, tables, pdf_context=None):
        """
        Args:
            tables: processed_results['tables'] 列表
            pdf_context: PDFContext 实例（可选）
        """
        super().__init__()
        self.tables = tables
        self.pdf_context = pdf_context
        self.last_prompts = None  # 存储最近一次发送的 (system_prompt, user_prompt)
        self._corrector = None    # 保留 LLMCorrector 引用以便外部读取
        self.last_total_usage = None  # 存储最近一次 LLM 调用的 token 消耗统计
        self._liteparse_data = None  # liteparse 缓存
        # 日志记录器
        pdf_path = getattr(pdf_context, "pdf_path", None) if pdf_context else None
        self._logger = _CorrectionLogger(pdf_path)

    def _load_liteparse(self):
        """加载 liteparse 缓存数据。"""
        if self._liteparse_data is not None:
            return self._liteparse_data
        if not self.pdf_context:
            return None
        try:
            from codes.liteparse_extractor.cache_manager import load_parse_result
            pdf_path = getattr(self.pdf_context, "pdf_path", None)
            if not pdf_path:
                return None
            result = load_parse_result(pdf_path)
            self._liteparse_data = result.to_dict() if result else None
        except Exception:
            self._liteparse_data = None
        return self._liteparse_data

    def _build_page_table_map(self):
        """构建 page_num -> [table_index, ...] 映射，供 P4 使用。"""
        page_map = {}
        for i, t in enumerate(self.tables):
            page = t.get("page", 0)
            if page not in page_map:
                page_map[page] = []
            page_map[page].append(i)
        return page_map

    def run(self, custom_system_prompt=None, custom_user_prompt=None):
        """主流程：L1 → L2 → P4（确定性差异分析）→ L3（LLM 仅处理疑难）"""
        results = []
        log = self._logger
        log.header(f"AI 纠错流程日志 — {time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"共 {len(self.tables)} 张表格待处理")
        pdf_path = getattr(self.pdf_context, "pdf_path", None) if self.pdf_context else None
        if pdf_path:
            log.info(f"PDF: {pdf_path}")

        # ============== Layer 1: 规则预检 ==============
        self.progress.emit(5, "规则预检中...")
        log.section("Layer 1: 规则预检")
        checker = RuleChecker()
        check_results = {}
        for i, t in enumerate(self.tables):
            if "__index__" not in t:
                t["__index__"] = i  # 临时索引标记（不覆盖已预置的）
            check_results[i] = checker.check(t)
            ck = check_results[i]
            if ck:
                page = t.get("page", "?")
                log.subsection(f"表#{i} (P{page})")
                for k, v in ck.items():
                    log.kv(k, v)

        # ============== Layer 2: 规则自动修复 ==============
        self.progress.emit(15, "自动修复中...")
        log.section("Layer 2: 规则自动修复")
        fixer = RuleAutoFixer()
        for i, t in enumerate(self.tables):
            r = fixer.fix(t, check_results.get(i, {}))
            results.append(r)
            if r.applied_rules:
                page = t.get("page", "?")
                log.subsection(f"表#{i} (P{page})")
                log.kv("应用规则", r.applied_rules)
                if r.corrected_data:
                    log.data_snapshot("修复后数据", r.corrected_data)
                log.kv("状态", r.status)

        self.autofix_done.emit(results)

        # ============== Phase 4: 确定性差异分析 ==============
        self.progress.emit(25, "确定性差异分析中...")
        log.section("Phase 4: 确定性差异分析（liteparse 对比）")
        liteparse_data = self._load_liteparse()
        has_liteparse = liteparse_data is not None
        log.info(f"liteparse 数据: {'可用' if has_liteparse else '不可用'}")
        page_map = self._build_page_table_map()

        # 为每个表注入 _all_tables_on_page 引用
        for page_num, indices in page_map.items():
            tables_on_page = [self.tables[i] for i in indices]
            for i in indices:
                self.tables[i]["_all_tables_on_page"] = tables_on_page

        verified_count = 0
        auto_fixed_count = 0
        no_lp_count = 0

        for r in results:
            idx = r.table_index
            if idx >= len(self.tables):
                continue
            table = self.tables[idx]

            # 已 L2 标记 clean 的表跳过
            if r.status == "clean" and not check_results.get(idx, {}):
                continue

            diff_level, diff_data = _deterministic_diff_analysis(table, liteparse_data)

            page = table.get("page", "?")
            log.subsection(f"表#{idx} (P{page})")
            log.kv("差异等级", diff_level)

            if diff_level == "clean":
                # 代码验证通过 → 标记 verified
                r.status = "verified"
                r.confidence = "high"
                r.diff_source = "deterministic"
                r.changes_summary = "✅ liteparse 逐行验证通过"
                log.info("→ 逐行验证通过，无需修改")
                verified_count += 1

            elif diff_level == "no_liteparse":
                # 无 liteparse 参考 → 标记 needs_review（不送 LLM）
                if r.status == "clean" or r.status == "auto_fixed":
                    r.status = "needs_review"
                    r.confidence = "medium"
                r.diff_source = "none"
                r.changes_summary = (r.changes_summary or "") + " | ⚠️ 无 liteparse 参考"
                log.info("→ 无 liteparse 参考，标记 needs_review")
                no_lp_count += 1

            elif diff_level == "auto_fixable":
                # 代码可修复 → 应用确定性修改
                log.diff_report(diff_data)
                corrected_data, changes = _apply_deterministic_fix(table, diff_data)
                log.changes_detail(changes)
                if corrected_data and changes:
                    r.corrected_data = corrected_data
                    r.status = "auto_fixed_code"
                    r.confidence = "high"
                    r.diff_source = "deterministic"
                    r.diff_report = diff_data
                    r.changes_summary = f"代码修复: {'; '.join(changes[:5])}"
                    if len(changes) > 5:
                        r.changes_summary += f"等{len(changes)}处"
                    log.data_snapshot("修复后数据", corrected_data)
                    auto_fixed_count += 1
                else:
                    r.status = "verified"
                    r.confidence = "high"
                    r.diff_source = "deterministic"
                    r.changes_summary = "✅ liteparse 验证通过（差异可忽略）"
                    log.info("→ 差异可忽略，验证通过")
                    verified_count += 1

            else:
                # diff_level == "needs_llm" → 保持 needs_review 状态进入 L3
                r.diff_source = "needs_llm"
                r.diff_report = diff_data
                # 如果 L2 已经标记为 clean，升级为 needs_review
                if r.status == "clean":
                    r.status = "needs_review"
                log.info("→ 需 LLM 分析，差异报告:")
                log.diff_report(diff_data)

        self.diff_analysis_done.emit(results)
        log.info(f"P4 汇总: 验证通过 {verified_count} / 代码修复 {auto_fixed_count} / 无 liteparse {no_lp_count}")

        # 筛选真正需要 LLM 的表（排除已代码验证/修复的）
        need_llm = [r for r in results
                     if r.diff_source == "needs_llm"
                     and r.status not in ("clean", "verified", "auto_fixed_code")]

        if need_llm:
            need_llm_indices = set(r.table_index for r in need_llm)
            llm_tables = [t for t in self.tables if t.get("__index__") in need_llm_indices]

            # ============== Layer 3: LLM 深度分析（仅疑难表）==============
            self.progress.emit(30, f"LLM 分析中（{len(llm_tables)} 张疑难表 / 共 {len(results)} 张）...")
            log.section(f"Layer 3: LLM 深度分析（{len(llm_tables)} 张疑难表）")
            log.info(f"送入 LLM 的表: {sorted(need_llm_indices)}")

            self._corrector = LLMCorrector()

            def llm_progress(pct, msg):
                self.progress.emit(30 + int(pct * 0.65), msg)

            llm_results = self._corrector.analyze_all(
                llm_tables,
                check_results,
                context=self.pdf_context,
                progress_callback=llm_progress,
                custom_system_prompt=custom_system_prompt,
                custom_user_prompt=custom_user_prompt
            )

            # 保存实际发送的 prompt
            self.last_prompts = (
                self._corrector.last_system_prompt,
                self._corrector.last_user_prompt
            )

            # 记录 prompt 到日志
            log.subsection("发送的 Prompt")
            log.prompt_block("System Prompt", self._corrector.last_system_prompt, max_len=1500)
            log.prompt_block("User Prompt", self._corrector.last_user_prompt, max_len=3000)

            # 保存 token 消耗统计
            self.last_total_usage = self._corrector.get_total_usage()
            if self.last_total_usage:
                log.subsection("Token 消耗")
                for k, v in self.last_total_usage.items():
                    log.kv(k, v)

            # 合并 LLM 结果到 results
            llm_map = {r.table_index: r for r in llm_results}
            log.subsection("LLM 返回结果")
            for r in results:
                if r.table_index in llm_map:
                    lr = llm_map[r.table_index]
                    log.info(f"  表#{r.table_index}:")
                    log.llm_result(lr)
                    r.status = lr.status
                    r.confidence = lr.confidence
                    r.name_title = lr.name_title
                    r.name_summary = lr.name_summary
                    r.hierarchy = lr.hierarchy
                    r.region_is_complete = lr.region_is_complete
                    r.region_merge_prev = lr.region_merge_prev
                    r.region_merge_next = lr.region_merge_next
                    r.region_split_rows = lr.region_split_rows
                    r.region_issues = lr.region_issues
                    r.applied_corrections = lr.applied_corrections
                    r.unresolved_issues = lr.unresolved_issues
                    r.changes_summary = lr.changes_summary
                    r.layout_analysis = lr.layout_analysis
                    r.reconstructed_data = lr.reconstructed_data
                    r.merge_source_indices = lr.merge_source_indices
                    r.changes_log = lr.changes_log
                    r.diff_source = "llm"  # 最终来源标记为 LLM
                    if lr.corrected_data:
                        r.corrected_data = lr.corrected_data
                    elif r.corrected_data is None and lr.corrected_data is None:
                        pass
                    elif r.corrected_data is not None and lr.corrected_data is not None:
                        r.corrected_data = lr.corrected_data

            # ============ 数值交叉验证 ============
            self.progress.emit(95, "数值交叉验证中...")
            log.subsection("数值交叉验证")
            for r in results:
                if r.hierarchy and 0 <= r.table_index < len(self.tables):
                    data_to_verify = r.corrected_data or self.tables[r.table_index].get("data", [])
                    verified, discrepancies = _verify_hierarchy_numeric(data_to_verify, r.hierarchy)
                    r.hierarchy_verified = verified
                    if verified:
                        log.info(f"  表#{r.table_index}: 层级验证通过")
                    else:
                        log.info(f"  表#{r.table_index}: 层级验证失败")
                        for d in discrepancies:
                            log.info(f"    {d.get('description', d)}")
                    if not verified:
                        r.confidence = min_confidence(r.confidence, "medium")
                        for d in discrepancies:
                            r.unresolved_issues.append(d)
        else:
            self.progress.emit(80, f"确定性分析完成（已验证{verified_count}张 / 代码修复{auto_fixed_count}张 / 跳过{no_lp_count}张），无需 LLM")

        self.progress.emit(100, "分析完成")
        # 汇总所有标签
        for r in results:
            r.error_tags = _build_error_tags(r)

        # 最终汇总
        log.section("最终汇总")
        status_counts = {}
        for r in results:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1
        for st, cnt in sorted(status_counts.items()):
            log.kv(st, cnt)
        log.info(f"日志文件: {self._logger._log_path}")
        log.save()

        self.all_done.emit(results)
        return results
