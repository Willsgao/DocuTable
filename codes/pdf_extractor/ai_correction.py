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
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from PyQt5.QtCore import QObject, pyqtSignal

from .utils import load_config


# ============================================================
# 数据容器
# ============================================================

@dataclass
class CorrectionResult:
    """单张表格的纠错结果"""
    table_index: int = -1
    status: str = "clean"               # clean | auto_fixed | llm_analyzed | needs_review | unresolvable
    confidence: str = "high"             # high | medium | low | unresolvable

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

    # == 修正数据 ==
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

class LLMCorrector:
    """LLM 深度分析器 — 一次 API 调用完成命名 + 层级 + 区域判断"""

    MAX_ROWS_PREVIEW = 8           # 每表最多预览行数
    MAX_CELL_LEN = 25              # 单格最大字符数
    MAX_OUTPUT_TOKENS = 8000       # 输出 token 上限
    TOKENS_PER_TABLE_EST = 300     # 每表 token 估算
    MAX_INPUT_TOKENS = 48000       # 输入 token 安全上限

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

        response_data = self._call_api(system_prompt, user_prompt)

        if progress_callback:
            progress_callback(70, "解析 LLM 响应...")

        return self._parse_response(response_data, tables)

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
        """构建三合一 Prompt（命名 + 层级 + 区域）"""
        total_pages = max((t.get("page", 1) for t in tables), default=1)

        system_prompt = """你是一个金融文档表格分析专家。分析从银行年报PDF中自动提取的表格数据。

任务（三项合一）：
1. **命名**：根据表格内容及上方描述文字生成规范名称和一句话摘要。
2. **层级**：识别合计(总计)/小计/层级嵌套关系，用row索引标记。
3. **区域**：判断表格是否完整、是否应与相邻表合并或拆分。

层级标记 type：
- "header": 表头行（列名）
- "category": 分类/层级标签行
- "subtotal": 小计行（total_of_rows 填写包含的行索引）
- "total": 总计行（total_of_rows 填写包含的行索引）
- "data": 普通数据行

置信度原则：
- 能确定的给出精确结果，confidence: "high"
- 不确定的标注 confidence: "medium" 或 "low" 并写明原因
- 无法判断的标注 "unresolvable"
- 只返回 JSON，不要其他任何文字"""

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

        user_prompt = f"""本文档共 {total_pages} 页，提取到 {len(tables)} 张表格。

{"".join(table_blocks)}

---

请返回 JSON（不要 markdown 代码块标记，直接返回纯 JSON 文本）：
{{"tables": [
  {{
    "table_index": 0,
    "name": {{"title": "合并资产负债表", "summary": "截至2024年末的资产负债情况"}},
    "hierarchy": [
      {{"row": 0, "type": "header", "level": 0, "label": "项目"}},
      {{"row": 1, "type": "category", "level": 1, "label": "流动资产"}},
      {{"row": 5, "type": "subtotal", "level": 1, "label": "流动资产合计", "total_of_rows": [1,2,3,4]}},
      {{"row": 10, "type": "total", "level": 0, "label": "资产总计", "total_of_rows": [5,9]}}
    ],
    "hierarchy_confidence": "high",
    "region": {{
      "is_complete": true,
      "merge_prev": null,
      "merge_next": null,
      "split_at_rows": null,
      "confidence": "high"
    }},
    "corrections": [
      {{"row": 2, "col": 3, "action": "change", "new_value": "12,345"}}
    ],
    "corrections_confidence": "high",
    "issues": []
  }}
]}}

备注：
- region.merge_prev: 填入应与之合并的前一表格 index，null 表示不合并
- region.merge_next: 填入应与之合并的后一表格 index，null 表示不合并
- region.split_at_rows: 如果一张表包含多个独立表格，填入分隔行索引（列表）
- corrections.action: change / merge_cells / split_cell / insert_col / delete_col
- issues 仅在 confidence 不是 high 时填写
  [{{"type": "...", "confidence": "medium/low", "description": "...", "suggested_action": "..."}}]"""

        return system_prompt, user_prompt

    def _enrich_context(self, table, context):
        """获取表格上方描述文字（优先使用现有 context_text）"""
        ctx = table.get("context_text", "")
        if ctx and len(ctx) > 50:
            return ctx[:500]  # 截断以防过长
        return (ctx or "")[:500]

    def _build_table_preview(self, table_data):
        """将表格数据压缩为文本预览"""
        if not table_data:
            return "（表格数据为空）"

        lines = []
        for row_idx, row in enumerate(table_data):
            if row_idx >= self.MAX_ROWS_PREVIEW:
                lines.append(f"... (共{len(table_data)}行，仅显示前{self.MAX_ROWS_PREVIEW}行)")
                break
            cells = []
            for cell in row:
                s = str(cell).strip()
                if len(s) > self.MAX_CELL_LEN:
                    s = s[:self.MAX_CELL_LEN - 3] + "..."
                cells.append(s)
            lines.append(" | ".join(cells))

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
        """调用 LLM API，含重试机制"""
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

                if 'choices' in result and len(result['choices']) > 0:
                    content = result['choices'][0]['message']['content']
                    return self._try_parse_json(content)
                else:
                    return {"error": "API 返回格式错误", "raw": str(result)}

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return {"error": "API 请求超时"}
            except requests.exceptions.HTTPError as e:
                return {"error": f"HTTP {e.response.status_code}: {e.response.reason}"}
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return {"error": str(e)}

        return {"error": "所有重试均失败"}

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

    def _parse_response(self, response_data, tables):
        """解析 LLM 响应为 CorrectionResult 列表"""
        results = []

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

    # ========== 分类 5: 元信息 ==========
    if not result.name_title and not result.hierarchy:
        tags.append({
            "key": "no_api_key", "label": "无API Key",
            "category": "meta",
            "detail": "仅规则检查"
        })

    if result.status == "unresolvable":
        tags.append({
            "key": "unresolvable", "label": "无法处理",
            "category": "needs_review",
            "detail": result.changes_summary or "需人工介入"
        })

    return tags


# ============================================================
# Layer 4: CorrectionEngine 协调器
# ============================================================

class CorrectionEngine(QObject):
    """AI 纠错总协调器 — 串联 4 层管线"""

    # 信号
    progress = pyqtSignal(int, str)           # 进度: (百分比, 消息)
    precheck_done = pyqtSignal(list)          # Layer 1 完成: [CorrectionResult]
    autofix_done = pyqtSignal(list)          # Layer 2 完成: [CorrectionResult]
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

    def run(self, custom_system_prompt=None, custom_user_prompt=None):
        """主流程：L1 → L2 → L3"""
        results = []

        # ============== Layer 1: 规则预检 ==============
        self.progress.emit(5, "规则预检中...")
        checker = RuleChecker()
        check_results = {}
        for i, t in enumerate(self.tables):
            if "__index__" not in t:
                t["__index__"] = i  # 临时索引标记（不覆盖已预置的）
            check_results[i] = checker.check(t)

        # ============== Layer 2: 规则自动修复 ==============
        self.progress.emit(15, "自动修复中...")
        fixer = RuleAutoFixer()
        for i, t in enumerate(self.tables):
            r = fixer.fix(t, check_results.get(i, {}))
            results.append(r)

        self.autofix_done.emit(results)

        # 筛选需要 LLM 的表
        need_llm = [r for r in results
                     if r.status not in ("clean",)]
        need_llm_indices = set(r.table_index for r in need_llm)

        if need_llm_indices:
            llm_tables = [t for t in self.tables if t.get("__index__") in need_llm_indices]

            # ============== Layer 3: LLM 深度分析 ==============
            self.progress.emit(30, f"LLM 分析中（{len(llm_tables)} 张表）...")

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

            # 合并 LLM 结果到 results
            llm_map = {r.table_index: r for r in llm_results}
            for r in results:
                if r.table_index in llm_map:
                    lr = llm_map[r.table_index]
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
                    if lr.corrected_data:
                        r.corrected_data = lr.corrected_data
                    elif r.corrected_data is None and lr.corrected_data is None:
                        pass  # 两边都没修改数据
                    elif r.corrected_data is not None and lr.corrected_data is not None:
                        r.corrected_data = lr.corrected_data  # LLM 的修正覆盖 L2 的

            # ============ 数值交叉验证 ============
            self.progress.emit(95, "数值交叉验证中...")
            for r in results:
                if r.hierarchy:
                    data_to_verify = r.corrected_data or self.tables[r.table_index].get("data", [])
                    verified, discrepancies = _verify_hierarchy_numeric(data_to_verify, r.hierarchy)
                    r.hierarchy_verified = verified
                    if not verified:
                        r.confidence = min_confidence(r.confidence, "medium")
                        for d in discrepancies:
                            r.unresolved_issues.append(d)

        self.progress.emit(100, "分析完成")
        # 汇总所有标签
        for r in results:
            r.error_tags = _build_error_tags(r)
        self.all_done.emit(results)
        return results
