# -*- coding: utf-8 -*-
"""
LLM 表格边界检测器 — 用 LLM 识别整页 liteparse 文本中的逻辑表格边界

架构位置：
  Layer 2（新增）：在 pdf2docx 提取表格碎片之后，
  _auto_merge_split_tables 之前，用 LLM 做边界识别，
  替代原有的启发式合并规则。

核心流程：
  1. 将连续表格页分组（连续页码）
  2. 每组页的 liteparse full_text 一起提交给 LLM
  3. LLM 逐页分析，返回每页所有逻辑表格的起止行、列数、表头位置
  4. 代码收尾：用 LLM 返回的边界信息合并 pdf2docx 碎片 + 跨页拼接

设计原则：
  - LLM 只做"需要语义理解"的事：判断哪几行是同一个表格
  - 代码做"不需要理解"的事：跨页拼接、文本解析、坐标聚类
  - 零 API 成本备选：LLM 不可用或失败时降级为启发式合并
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .utils import load_config, get_pdf_cache_dir


# ============================================================
# 1. 连续页分组
# ============================================================

def group_consecutive_table_pages(
    liteparse_data: dict,
    max_group_size: int = 5,
) -> List[List[int]]:
    """将 liteparse 检测到的表格页按连续页码分组。

    连续页码（如 8,9,10）归为同一组 → 组内 LLM 可感知跨页续表。
    不连续的页码（如 3, 8-10, 15-17）各自成组。

    Args:
        liteparse_data: ParseResult.to_dict()，含 pages 列表
        max_group_size: 每组最多多少页（防止 token 溢出）

    Returns:
        [[3], [8, 9, 10], [15, 16, 17], ...]
    """
    pages = liteparse_data.get("pages", [])
    if not pages:
        return []

    # 收集所有表格页的页码
    table_pages = sorted(
        p.get("page_number", 0)
        for p in pages
        if p.get("is_table_page") and p.get("full_text", "").strip()
    )

    if not table_pages:
        return []

    # 按连续性分组
    groups = []
    current_group = [table_pages[0]]

    for i in range(1, len(table_pages)):
        if table_pages[i] == table_pages[i - 1] + 1:
            current_group.append(table_pages[i])
        else:
            groups.append(current_group)
            current_group = [table_pages[i]]

    groups.append(current_group)

    # 拆分超大组（> max_group_size）
    result = []
    for g in groups:
        for i in range(0, len(g), max_group_size):
            result.append(g[i : i + max_group_size])

    return result


def _get_liteparse_page(liteparse_data: dict, page_num: int) -> Optional[dict]:
    """从 liteparse_data 获取指定页。"""
    pages = liteparse_data.get("pages", [])
    for p in pages:
        if p.get("page_number") == page_num:
            return p
    return None


# ============================================================
# 2. Prompt 构建
# ============================================================

_BOUNDARY_SYSTEM_PROMPT = """你是一个 PDF 表格结构分析器。你的任务是分析从 PDF 连续页面提取的版式保留文本，逐页识别出所有逻辑表格的精确边界。

liteparse 文本格式说明：
- 文本保留了原始排版，多个连续空格表示列之间的空白间隔
- 换行符表示原文换行
- 数字型 PDF（非扫描件）的文本准确度非常高

判断表格的标准：
1. **表格特征**：连续多行有规律的空格列对齐，通常包含数值列
2. **表头特征**：包含中文标签/列名，列数与下方数据行一致
3. **表格结束标志**：遇到连续空行（≥2行）、下一章节标题、大段说明文字
4. **跨页续表** (is_continuation=true)：表格直接从数据行开始，没有中文表头

注意：
- start_line/end_line 是 0-based 行号（空行也要计入行号）
- 一页可能有 0 个或 N 个表格
- 表格标题行（如"财务摘要"）应计入 start_line 之前，不计入表格内容"""


def build_boundary_user_prompt(
    page_texts: Dict[int, str],
    group_pages: List[int],
) -> str:
    """构建 LLM 边界检测的 user prompt。

    Args:
        page_texts: {page_num: liteparse_full_text}
        group_pages: 该组包含的页码列表

    Returns:
        str: user prompt
    """
    blocks = []
    for page_num in group_pages:
        text = page_texts.get(page_num, "")
        if not text:
            blocks.append(
                f"=== 第 {page_num} 页 ===\n"
                f"（此页无文本内容）\n"
            )
        else:
            # 限制每页最多 3000 字符（防止 token 溢出）
            truncated = text[:3000]
            if len(text) > 3000:
                truncated += f"\n...（已截断，原文本共 {len(text)} 字符）"
            blocks.append(
                f"=== 第 {page_num} 页 ===\n"
                f"{truncated}\n"
            )

    pages_str = "、".join(f"第{p}页" for p in group_pages)
    return (
        f"以下是 PDF 中连续 {len(group_pages)} 页（{pages_str}）的版式保留文本。\n"
        f"请逐页分析，识别每页的所有逻辑表格边界。\n\n"
        + "\n".join(blocks)
        + "\n\n"
        + "请返回 JSON（只返回纯 JSON，不要 markdown 代码块标记）：\n"
        + """{
  "pages": [
    {
      "page": 8,
      "tables": [
        {
          "table_id": 0,
          "start_line": 4,
          "end_line": 20,
          "header_start": 4,
          "header_end": 5,
          "column_count": 7,
          "is_continuation": false,
          "continues_from_page": null,
          "caption": "财务摘要",
          "notes": ""
        }
      ]
    },
    {
      "page": 9,
      "tables": [
        {
          "table_id": 0,
          "start_line": 1,
          "end_line": 15,
          "header_start": null,
          "header_end": null,
          "column_count": 7,
          "is_continuation": true,
          "continues_from_page": 8,
          "caption": "",
          "notes": "延续第8页的财务摘要表"
        }
      ]
    }
  ]
}

字段说明：
- table_id: 页内表格序号，从上到下，0-based
- start_line / end_line: 表格在页内文本中的起止行号，0-based，空行计入
- header_start / header_end: 表头行范围，无表头时填 null
- column_count: 推测的数据列数
- is_continuation: 是否延续上一页的表格
- continues_from_page: 延续自哪一页（仅 is_continuation=true 时填写）
- caption: 表格标题（如有）
- notes: 补充说明（如有）
- 如果某页没有任何表格，tables 字段为空数组 []"""
    )


# ============================================================
# 3. LLM 调用
# ============================================================

def _extract_usage(result: dict) -> dict:
    """从 API 响应中提取 token 用量。"""
    usage = result.get("usage", {})
    if not usage:
        return {}
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }


def _try_parse_json(content: str) -> dict:
    """尝试从 LLM 回复中解析 JSON，兼容多种格式。"""
    # 去除 markdown 代码块标记
    text = content.strip()

    # 尝试提取 ```json ... ``` 或 ``` ... ```
    m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if m:
        text = m.group(1).strip()

    # 尝试找到第一个 { 到最后一个 }
    try:
        start = text.index('{')
        end = text.rindex('}') + 1
        text = text[start:end]
    except ValueError:
        pass

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失败: {str(e)}", "raw": content[:500]}


def call_llm_boundary_detection(
    page_texts: Dict[int, str],
    group_pages: List[int],
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    model: Optional[str] = None,
    max_retries: int = 2,
    log_callback=None,
) -> Tuple[Optional[dict], dict]:
    """调用 LLM 检测一组连续页面的表格边界。

    Args:
        page_texts: {page_num: liteparse full_text}
        group_pages: 该组包含的页码列表（已排序）
        api_key: 覆写 API key（None 则用配置文件）
        endpoint: 覆写 API endpoint
        model: 覆写 model
        max_retries: 最大重试次数
        log_callback: callable(message) 用于记录日志

    Returns:
        (parsed_boundaries_json, usage_dict)
        - 成功: ({"pages": [...]}, {...})
        - 失败: (None, {})

        usage_dict: {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
    """
    config = load_config()
    api_key = api_key or config.get("deepseek_api_key", "")
    endpoint = endpoint or config.get("deepseek_endpoint", "api.deepseek.com")
    model = model or config.get("deepseek_model", "deepseek-chat")

    if not api_key:
        print("  [LLM边界] 未配置 API Key，跳过 LLM 调用")
        return None, {}

    api_url = f"https://{endpoint}/v1/chat/completions"

    user_prompt = build_boundary_user_prompt(page_texts, group_pages)

    # 记录 prompt 到日志
    if log_callback:
        pages_tag = "→".join(str(p) for p in group_pages)
        log_callback(f"  LLM 请求 P{pages_tag}:")
        log_callback(f"    System Prompt: {_BOUNDARY_SYSTEM_PROMPT[:200]}...")
        log_callback(f"    User Prompt: {user_prompt[:500]}...")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _BOUNDARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 4096,
        "temperature": 0.1,
    }

    pages_tag = "→".join(str(p) for p in group_pages)
    print(f"  [LLM边界] 调用 LLM 分析 P{pages_tag} ({len(group_pages)}页) ...")

    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            result = resp.json()

            usage = _extract_usage(result)

            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                parsed = _try_parse_json(content)

                if "error" not in parsed:
                    # 验证基本结构
                    if "pages" not in parsed:
                        print(f"  [LLM边界] 响应缺少 'pages' 字段，重试...")
                        if log_callback:
                            log_callback(f"    WARNING: 响应缺少 'pages' 字段，重试")
                        if attempt < max_retries:
                            continue
                        return None, usage

                    print(f"  [LLM边界] P{pages_tag} 分析完成 "
                          f"({usage.get('total_tokens', 0)} tokens)")

                    # 记录 LLM 原始响应到日志
                    if log_callback:
                        log_callback(f"  LLM 响应 P{pages_tag}:")
                        # 记录解析后的结构化结果
                        for page_entry in parsed.get("pages", []):
                            pn = page_entry.get("page", "?")
                            tbls = page_entry.get("tables", [])
                            log_callback(f"    第{pn}页: {len(tbls)}个逻辑表")
                            for tb in tbls:
                                tid = tb.get("table_id", "?")
                                sl = tb.get("start_line", "?")
                                el = tb.get("end_line", "?")
                                cc = tb.get("column_count", "?")
                                cont = tb.get("is_continuation", False)
                                cap = tb.get("caption", "")
                                log_callback(f"      表{tid}: 行{sl}~{el}, {cc}列"
                                             f"{' [续表]' if cont else ''}"
                                             f'{f" 标题:{cap}" if cap else ""}')
                        # 记录原始响应（截断）
                        raw_preview = content[:800] if len(content) > 800 else content
                        log_callback(f"    原始响应（截断）:")
                        for line in raw_preview.split("\n")[:20]:
                            log_callback(f"      {line}")

                    return parsed, usage

                # JSON 解析失败 → 重试
                if attempt < max_retries:
                    json_error = parsed.get("error", "未知错误")
                    print(f"  [LLM边界] JSON 解析失败 (尝试 {attempt + 1}): {json_error}")
                    if log_callback:
                        log_callback(f"    WARNING: JSON 解析失败 (尝试 {attempt + 1}): {json_error}")

                    # 添加修复请求
                    payload["messages"].append(
                        {"role": "assistant", "content": content}
                    )
                    payload["messages"].append({
                        "role": "user",
                        "content": (
                            f"上次回复无法解析为有效 JSON。错误: {json_error}\n"
                            "请严格按照 JSON 格式重新输出，只返回纯 JSON。"
                        )
                    })
                    payload["temperature"] = min(0.3, payload["temperature"] + 0.1)
                    continue

                return None, usage
            else:
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return None, {}

        except requests.exceptions.Timeout:
            if attempt < max_retries:
                print(f"  [LLM边界] 超时，重试 {attempt + 2}/{max_retries + 1} ...")
                time.sleep(2)
                continue
            print(f"  [LLM边界] API 超时")
            return None, {}
        except requests.exceptions.HTTPError as e:
            print(f"  [LLM边界] HTTP {e.response.status_code}: {e.response.reason}")
            if log_callback:
                log_callback(f"    ERROR: HTTP {e.response.status_code}: {e.response.reason}")
            return None, {}
        except Exception as e:
            if attempt < max_retries:
                print(f"  [LLM边界] 异常: {e}，重试...")
                time.sleep(2)
                continue
            print(f"  [LLM边界] 异常: {e}")
            if log_callback:
                log_callback(f"    ERROR: {e}")
            return None, {}

    return None, {}


# ============================================================
# 4. 边界应用：合并 pdf2docx 碎片 + 跨页拼接
# ============================================================

def _is_page_header_fragment(table: dict) -> bool:
    """判断一个 pdf2docx 碎片是否为页面页眉/标题行（非表格数据）。
    
    这类碎片是 pdf2docx 将页面标题行误识别为表格的结果。
    合并时应该跳过它们，不能拼入真正的表格数据。
    
    判定策略（按优先级）：
    1. 内容包含公司名/年报关键词 → 直接判定为页眉
    2. 纯中文短文本 + 行数少 → 页眉
    3. 纯数字页码 → 页眉
    4. 兜底：≤3行 + 数值占比 < 10% → 页眉
    """
    data = table.get("data", [])
    if not data:
        return False
    
    n_rows = len(data)
    if n_rows > 3:
        return False
    
    # 收集文本特征
    import re
    total_cells = 0
    numeric_cells = 0
    all_text = ""
    for row in data:
        for cell in row:
            t = str(cell).strip()
            if t:
                total_cells += 1
                all_text += t
                if t.replace(",", "").replace("%", "").replace("(", "").replace(")", "").replace("-", "").replace(".", "").isdigit():
                    numeric_cells += 1
    
    # ---- 优先级1：内容模式检查（含关键词→必为页眉）----
    has_report_keyword = any(kw in all_text for kw in [
        "年度报告", "年报", "股份有限公司", "有限公司", "目录",
    ])
    if has_report_keyword:
        return True
    
    # ---- 优先级2：纯中文短文本（如章节标题）----
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', all_text))
    if chinese_chars > 10 and n_rows <= 2:
        return True
    
    # ---- 优先级3：纯数字页码（如单格 "22"）----
    if re.match(r'^\d{1,3}$', all_text):
        return True
    
    # ---- 兜底：数值占比检查（大量数值 → 是数据表）----
    if total_cells > 0 and numeric_cells / total_cells >= 0.1:
        return False
    
    return True


def apply_llm_boundaries_to_merge(
    tables: List[dict],
    boundaries: dict,
    liteparse_data: dict,
) -> Tuple[List[dict], List[str]]:
    """用 LLM 返回的边界信息合并 pdf2docx 表格碎片。

    核心逻辑：
    1. 用 LLM 判断的同页多表边界 → 决定同页碎片是否分开
    2. 用 LLM 标记的 is_continuation → 跨页拼接

    Args:
        tables: 当前全部表格列表（已按页码排序）
        boundaries: LLM 返回的 {"pages": [{"page": N, "tables": [...]}, ...]}
        liteparse_data: liteparse ParseResult.to_dict()

    Returns:
        (merged_tables, merge_logs)
    """
    if not boundaries or not tables:
        return tables, []

    # 构建快捷查找: page → llm_tables_info
    llm_page_info: Dict[int, list] = {}
    has_continuation = False
    continuation_map: Dict[int, int] = {}  # page → continues_from_page

    for page_entry in boundaries.get("pages", []):
        page_num = page_entry.get("page", 0)
        page_tables = page_entry.get("tables", [])
        llm_page_info[page_num] = page_tables

        for t in page_tables:
            if t.get("is_continuation") and t.get("continues_from_page"):
                has_continuation = True
                continuation_map[page_num] = t["continues_from_page"]

    logs = []

    if not llm_page_info and not has_continuation:
        return tables, logs

    # ---- Step 1: 同页碎片重组 ----
    # LLM 告诉我们每页有几个逻辑表
    # - LLM 1个表 + pdf2docx N个碎片 → 合并所有碎片（排除页眉碎片）
    # - LLM N个表 + pdf2docx ≤N 个碎片 → 不合并（可能是不同表）
    # - LLM 无数据 → 不做判断

    # 按页分组 pdf2docx tables
    page_tables_map: Dict[int, List[int]] = {}  # page → [table_indices]
    for idx, t in enumerate(tables):
        if not t.get("data"):
            continue
        page_tables_map.setdefault(t.get("page", 0), []).append(idx)

    # 记录将被合并的碎片索引（同页）
    same_page_merge_pairs = []  # [(keeper_idx, removed_idx), ...]
    # 🔧 记录被识别为页眉应该删除的碎片索引
    header_fragment_indices = set()

    for page_num, indices in page_tables_map.items():
        llm_tables = llm_page_info.get(page_num, [])
        llm_table_count = len(llm_tables)

        # 🔧 分离出页眉碎片（非表格数据，不应合并）
        page_header_indices = []
        valid_indices = []
        for idx in indices:
            if _is_page_header_fragment(tables[idx]):
                page_header_indices.append(idx)
                header_fragment_indices.add(idx)
            else:
                valid_indices.append(idx)
        
        if page_header_indices:
            hdr_pages = ", ".join(f"表#{i}" for i in page_header_indices)
            logs.append(f"P{page_num}: 检测到页面页眉碎片 ({hdr_pages})，排除不合并")

        # 用有效碎片重新判断
        indices = valid_indices if valid_indices else indices  # 如果全部是页眉，保守保留

        if llm_table_count == 1 and len(indices) > 1:
            # LLM 说此页只有1个逻辑表，但 pdf2docx 拆成了多个碎片
            # 🔧 使用 LLM 的 column_count 判断哪个碎片是主表
            llm_col_count = (llm_tables[0].get("column_count") 
                             if llm_tables and isinstance(llm_tables[0], dict) else None)
            
            # 找出列数最接近 LLM 判定的碎片作为 keeper
            if llm_col_count and len(indices) >= 2:
                best_idx = min(
                    indices,
                    key=lambda i: abs(
                        max((len(r) for r in tables[i].get("data", [])), default=0) - llm_col_count
                    )
                )
                # 把 best_idx 移到 indices[0] 位置
                indices.remove(best_idx)
                indices.insert(0, best_idx)
            
            keeper = indices[0]
            for removed in indices[1:]:
                same_page_merge_pairs.append((keeper, removed))
            logs.append(f"P{page_num}: LLM检测到1个逻辑表，"
                       f"pdf2docx有{len(indices)}个碎片，合并为1个")
            
            # NOTE: _llm_column_count 仅用于本函数内选择 keeper 的辅助判断，
            # 不应在下游（如 liteparse_cell_filler）中用于截断/修改 pdf2docx 列数
            if llm_col_count:
                tables[keeper]["_llm_column_count"] = llm_col_count

        elif llm_table_count >= 2 and len(indices) <= llm_table_count:
            # LLM 说此页有多个逻辑表，pdf2docx 碎片不多于 LLM 表数
            # → 不合并同页碎片，它们可能是不同表
            logs.append(f"P{page_num}: LLM检测到{llm_table_count}个逻辑表，"
                       f"pdf2docx有{len(indices)}个碎片，不合并")

        elif llm_table_count >= 2 and len(indices) > llm_table_count:
            # LLM 说 N 个表，但 pdf2docx 有更多碎片
            # → 尝试将多余的碎片合并到最近的已有表（保守策略：合并到第一个）
            excess = len(indices) - llm_table_count
            logs.append(f"P{page_num}: LLM检测到{llm_table_count}个逻辑表，"
                       f"pdf2docx有{len(indices)}个碎片（多{excess}个），"
                       f"多余碎片合并到首表")

            # pdf2docx 碎片数 > LLM 表数 → 保留前 llm_table_count 个碎片，
            # 多余的合并到这些保留碎片的最后一个
            # 保守策略：全部合并到第一个碎片
            if llm_table_count >= 1:
                keeper = indices[0]
                for removed in indices[llm_table_count:]:
                    same_page_merge_pairs.append((keeper, removed))
                # 中间碎片如果 LLM 说有多表也不合并：indices[1:llm_table_count] 保持独立

    # 执行同页合并（从后往前，避免索引变化）
    if same_page_merge_pairs:
        # 按 removed_idx 降序排列避免索引错乱
        same_page_merge_pairs.sort(key=lambda x: x[1], reverse=True)
        for keeper, removed in same_page_merge_pairs:
            if keeper < len(tables) and removed < len(tables):
                _do_merge_tables(tables, keeper, removed)
        to_remove = sorted(set(j for _, j in same_page_merge_pairs), reverse=True)
        for j in to_remove:
            if j < len(tables):
                tables.pop(j)

    # 🔧 删除页面页眉碎片（非表格数据）
    if header_fragment_indices:
        # 先排除已被合并删除的（索引已变化，需要重新映射）
        remaining_headers = []
        for h in sorted(header_fragment_indices, reverse=True):
            if h < len(tables):
                remaining_headers.append(h)
        for h in remaining_headers:
            tables.pop(h)
        if remaining_headers:
            logs.append(f"共移除 {len(remaining_headers)} 个页面页眉碎片")



    # ---- Step 2: 跨页续表拼接 ----
    # LLM 标记了 is_continuation: true 的页 → 将该页的第一个表拼接到 continues_from_page 页的最后一个表

    if has_continuation:
        # 重建页→索引映射（同页合并后索引已变化）
        tables.sort(key=lambda x: x.get("page", 0))

        page_to_indices: Dict[int, List[int]] = {}
        for idx, t in enumerate(tables):
            if t.get("data"):
                page_to_indices.setdefault(t.get("page", 0), []).append(idx)

        merged_pairs = []  # [(keep_idx, remove_idx), ...]

        for page_num, continues_from in sorted(continuation_map.items()):
            from_indices = page_to_indices.get(continues_from, [])
            to_indices = page_to_indices.get(page_num, [])

            if not from_indices or not to_indices:
                logs.append(f"P{continues_from}→P{page_num}: "
                           f"LLM 标记跨页续表，但找不到对应表格碎片，跳过")
                continue

            # 前页最后一个表 + 后页第一个表
            prev_last_idx = from_indices[-1]
            next_first_idx = to_indices[0]

            # 检查列数兼容性
            cols_a = max((len(r) for r in tables[prev_last_idx].get("data", [])), default=0)
            cols_b = max((len(r) for r in tables[next_first_idx].get("data", [])), default=0)

            if cols_a == 0 or cols_b == 0:
                continue

            if abs(cols_a - cols_b) > 2:
                logs.append(f"P{continues_from}→P{page_num}: "
                           f"LLM 标记跨页续表，但列数差异过大 ({cols_a} vs {cols_b})，跳过")
                continue

            # 执行跨页拼接（启用续表去重）
            _do_merge_tables(tables, prev_last_idx, next_first_idx, is_continuation=True)
            merged_pairs.append((prev_last_idx, next_first_idx))
            logs.append(f"P{continues_from}+P{page_num}: LLM标记跨页续表，自动拼接")

        # 移除已被合并的表
        if merged_pairs:
            to_remove = sorted(set(j for _, j in merged_pairs), reverse=True)
            for j in to_remove:
                if j < len(tables):
                    tables.pop(j)

    # ---- Step 3: 给每张表打上 LLM 边界标记 ----
    # 存入 table dict 供后续 liteparse cell filler 使用
    for t in tables:
        page = t.get("page", 0)
        llm_tables = llm_page_info.get(page, [])
        if llm_tables:
            t["_llm_boundary"] = llm_tables

    return tables, logs


def _is_header_like_row(row: List[str]) -> bool:
    """判断一行是否像表头（含年份、变化等列标题特征）。

    跨页续表场景：后页顶部常重复列标题行，如 "2024年 2023年 变化+/(-) ..."
    此类行应删除。"""
    import re
    if not row:
        return False
    # 年份模式
    year_count = sum(1 for c in row if re.search(r'\b(19|20)\d{2}年?\b', str(c)))
    if year_count >= 1:
        return True
    # 变化/增减模式
    delta_count = sum(1 for c in row if re.search(r'(变化|增减|变动|±)', str(c)))
    if delta_count >= 1:
        return True
    # 全是短词且无长文本标签（典型表头特征）
    non_empty = [str(c).strip() for c in row if str(c).strip()]
    if non_empty and all(len(c) <= 6 for c in non_empty):
        # 全部是短词，不太可能是数据行（数据行通常有数值或长标签）
        has_numeric = any(re.search(r'\d', c) for c in non_empty)
        if has_numeric:
            # 含数字的短词可能是年份列名
            return True
    return False


def _row_similarity(row_a: List[str], row_b: List[str]) -> float:
    """计算两行的相似度（0~1），基于标签匹配 + 数值重叠。

    用于检测跨页续表中的重复数据行。
    """
    if not row_a or not row_b:
        return 0.0

    # 1. 标签匹配（第一个非空单元格）
    label_a = ""
    label_b = ""
    for c in row_a:
        if str(c).strip():
            label_a = _normalize_for_dedup(str(c).strip())
            break
    for c in row_b:
        if str(c).strip():
            label_b = _normalize_for_dedup(str(c).strip())
            break

    if not label_a or not label_b:
        return 0.0

    # 标签完全相同 → 高基础分
    label_match = 1.0 if label_a == label_b else (
        0.8 if (label_a in label_b or label_b in label_a) else 0.0
    )
    if label_match == 0.0:
        return 0.0

    # 2. 数值重叠度
    vals_a = set()
    vals_b = set()
    for c in row_a[1:]:
        v = _normalize_for_dedup(str(c).strip())
        if v:
            vals_a.add(v)
    for c in row_b[1:]:
        v = _normalize_for_dedup(str(c).strip())
        if v:
            vals_b.add(v)

    if not vals_a or not vals_b:
        return label_match  # 只有标签能比，靠标签分

    # Jaccard 相似度
    intersection = vals_a & vals_b
    union = vals_a | vals_b
    value_overlap = len(intersection) / len(union) if union else 0.0

    # 综合分：标签匹配 40% + 数值重叠 60%
    return 0.4 * label_match + 0.6 * value_overlap


def _normalize_for_dedup(text: str) -> str:
    """归一化文本用于去重比较。"""
    import re
    s = text.strip().replace(",", "").replace(" ", "").replace("\u3000", "")
    # 括号负号转负号
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    # 去掉上标标记（如 ¹ ² ³）
    s = re.sub(r'[\u00b9\u00b2\u00b3\u2070-\u2079]', '', s)
    return s


def _dedup_continuation_rows(data_b: List[List[str]], data_a: List[List[str]] = None) -> List[List[str]]:
    """对跨页合并后的数据做去重：检测并移除因跨页续表导致的重复行。

    策略：
    1. 先移除表头样式的行（含年份/变化列标题）
    2. 与 data_a（前页数据）交叉对比，移除与 data_a 末尾重叠的行
    3. 检测 data_b 内部的标签重复行，移除数值重叠度高的行

    Args:
        data_b: 续页数据（需要去重的部分）
        data_a: 前页数据（用于交叉对比，可选）

    Returns:
        去重后的数据
    """
    if not data_b:
        return data_b

    # Step 1: 移除表头行
    cleaned = []
    for row in data_b:
        if not _is_header_like_row(row):
            cleaned.append(row)
        else:
            print(f"  [合并去重] 移除跨页表头行: {row[0] if row else '(空行)'}")

    if len(cleaned) <= 1:
        return cleaned

    # Step 2: 与前页数据交叉去重
    rows_to_remove = set()

    if data_a:
        # 在前页末尾查找与续页开头重叠的行
        # 只检查续页前 ~60% 的行（避免把新数据也删了）
        check_count = max(1, int(len(cleaned) * 0.6))
        for b_idx in range(check_count):
            row_b = cleaned[b_idx]
            label_b = ""
            for c in row_b:
                if str(c).strip():
                    label_b = _normalize_for_dedup(str(c).strip())
                    break
            if not label_b:
                continue

            # 在前页末尾 ~40% 范围内查找
            a_start = max(0, len(data_a) - max(1, int(len(data_a) * 0.4)))
            for a_idx in range(a_start, len(data_a)):
                row_a = data_a[a_idx]
                sim = _row_similarity(row_a, row_b)
                if sim >= 0.65:
                    rows_to_remove.add(b_idx)
                    break

        if rows_to_remove:
            print(f"  [合并去重] 交叉对比移除 {len(rows_to_remove)} 行与前页重叠的行")

    # Step 3: data_b 内部去重（相同标签 + 高重叠度）
    # 先过滤掉交叉去重标记的行
    remaining = [(idx, row) for idx, row in enumerate(cleaned) if idx not in rows_to_remove]

    label_map = {}
    for orig_idx, row in remaining:
        label = ""
        for c in row:
            if str(c).strip():
                label = _normalize_for_dedup(str(c).strip())
                break
        if label:
            label_map.setdefault(label, []).append((orig_idx, row))

    for label, entries in label_map.items():
        if len(entries) <= 1:
            continue

        entries.sort(key=lambda x: x[0])

        for k in range(len(entries) - 1):
            idx_a, row_a = entries[k]
            idx_b, row_b = entries[k + 1]

            sim = _row_similarity(row_a, row_b)
            if sim >= 0.65:
                na = sum(1 for c in row_a if str(c).strip())
                nb = sum(1 for c in row_b if str(c).strip())

                if na >= nb:
                    rows_to_remove.add(idx_b)
                else:
                    rows_to_remove.add(idx_a)
                    entries[k] = (idx_a, row_b)

    # Step 4: 执行删除
    if rows_to_remove:
        result = [row for idx, row in enumerate(cleaned) if idx not in rows_to_remove]
        total_removed = len(rows_to_remove)
        if total_removed > 0:
            print(f"  [合并去重] 共移除 {total_removed} 行跨页重复行")
        return result

    return cleaned


def _do_merge_tables(tables, i, j, is_continuation=False):
    """将 tables[j] 合并到 tables[i]。

    Args:
        tables: 表格列表
        i: 保留的表索引
        j: 被合并的表索引
        is_continuation: 是否为跨页续表拼接（True 时启用去重逻辑）
    """
    table_a = tables[i]
    table_b = tables[j]
    cols_a = max((len(r) for r in table_a.get("data", [])), default=0)
    cols_b = max((len(r) for r in table_b.get("data", [])), default=0)

    # 列对齐：以较宽的为准
    target_cols = max(cols_a, cols_b)

    # 对齐 table_b 的列宽
    aligned_b_data = []
    for row in table_b.get("data", []):
        while len(row) < target_cols:
            row.append("")
        aligned_b_data.append(row[:target_cols])

    # 对齐 table_a 的列宽（如果 table_a 更窄）
    for row in table_a.get("data", []):
        while len(row) < target_cols:
            row.append("")

    if is_continuation:
        # 续表去重：将 table_b 数据与 table_a 数据交叉对比后拼接
        table_a["data"].extend(_dedup_continuation_rows(aligned_b_data, table_a.get("data", [])))
    else:
        # 同页合并：直接拼接
        table_a["data"].extend(aligned_b_data)

    table_a["rows"] = len(table_a["data"])

    if "original_data" in table_b:
        if "original_data" not in table_a:
            table_a["original_data"] = []
        for row in table_b["original_data"]:
            while len(row) < target_cols:
                row.append("")
        table_a["original_data"].extend([row[:target_cols] for row in table_b["original_data"]])

    # 标记跨页拼接信息
    if is_continuation:
        table_a["_cross_page_merged"] = True
    table_a["_merged_from_pages"] = list(set(
        table_a.get("_merged_from_pages", [table_a.get("page", 0)]) +
        [table_b.get("page", 0)]
    ))


# ============================================================
# 5. 完整边界检测流程（对外接口）
# ============================================================

def detect_and_merge_with_llm(
    tables: List[dict],
    liteparse_data: dict,
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    model: Optional[str] = None,
    max_group_size: int = 5,
    progress_callback=None,
    log_callback=None,
) -> Tuple[List[dict], List[str], dict]:
    """完整的 LLM 边界检测 + 合并流程。

    1. 将 liteparse 表格页按连续页分组
    2. 对每组调用 LLM 检测表格边界
    3. 用 LLM 边界合并 pdf2docx 碎片

    Args:
        tables: pdf2docx 提取的表格列表
        liteparse_data: liteparse ParseResult.to_dict()
        api_key: API key 覆写
        endpoint: API endpoint 覆写
        model: model 覆写
        max_group_size: 每组最多页数
        progress_callback: callable(percent, message)
        log_callback: callable(message) 用于记录日志

    Returns:
        (merged_tables, merge_logs, total_usage)
    """
    if not liteparse_data:
        print("  [LLM边界] 无 liteparse 数据，跳过 LLM 边界检测")
        return tables, [], {}

    # 1. 分组
    page_groups = group_consecutive_table_pages(liteparse_data, max_group_size)
    if not page_groups:
        print("  [LLM边界] 无表格页，跳过")
        return tables, [], {}

    pages_tag = ", ".join(
        f"P{g[0]}" if len(g) == 1 else f"P{g[0]}~{g[-1]}"
        for g in page_groups
    )
    print(f"  [LLM边界] 连续页分组 ({len(page_groups)}组): {pages_tag}")

    # 2. 构建 page_texts 缓存
    page_texts: Dict[int, str] = {}
    for group in page_groups:
        for page_num in group:
            if page_num not in page_texts:
                lp_page = _get_liteparse_page(liteparse_data, page_num)
                if lp_page:
                    page_texts[page_num] = lp_page.get("full_text", "")

    # 3. 逐组调用 LLM
    all_boundaries = {"pages": []}
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "api_calls": 0,
        "model": model or load_config().get("deepseek_model", "deepseek-chat"),
    }

    total_groups = len(page_groups)

    for idx, group in enumerate(page_groups):
        if progress_callback:
            pct = int((idx / max(total_groups, 1)) * 80 + 5)
            pages_tag_group = (
                f"P{group[0]}" if len(group) == 1 else f"P{group[0]}~{group[-1]}"
            )
            progress_callback(pct, f"LLM 分析表格边界 ({pages_tag_group})...")

        result, usage = call_llm_boundary_detection(
            page_texts, group,
            api_key=api_key, endpoint=endpoint, model=model,
            log_callback=log_callback,
        )

        if usage:
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                total_usage[k] = total_usage.get(k, 0) + usage.get(k, 0)
            total_usage["api_calls"] += 1

        if result and "pages" in result:
            all_boundaries["pages"].extend(result["pages"])

        # 组间间隔（限速）
        if idx < total_groups - 1:
            time.sleep(0.5)

    if progress_callback:
        progress_callback(90, "应用 LLM 边界合并表格...")

    # 4. 应用边界合并
    merged_tables, merge_logs = apply_llm_boundaries_to_merge(
        tables, all_boundaries, liteparse_data
    )

    # 打印汇总
    if all_boundaries["pages"]:
        total_llm_tables = sum(
            len(p.get("tables", [])) for p in all_boundaries["pages"]
        )
        print(f"  [LLM边界] 完成: {total_groups}组页面 → "
              f"{len(all_boundaries['pages'])}页 → "
              f"识别{total_llm_tables}个逻辑表格 "
              f"({total_usage.get('total_tokens', 0)} tokens)")

    if merge_logs:
        for log in merge_logs:
            print(f"  [LLM边界] {log}")

    return merged_tables, merge_logs, total_usage


# ============================================================
# 6. 手动入口：一键 LLM 增强（供 UI 手动触发）
# ============================================================

def run_llm_table_enhancement(
    tables: List[dict],
    liteparse_data: Optional[dict] = None,
    progress_callback=None,
    pdf_path: Optional[str] = None,
) -> dict:
    """手动触发 LLM 表格增强 — 边界检测 + liteparse 文本填充。

    这是一个独立的手动入口函数，不在自动提取管线中运行。
    调用方式：
        from codes.pdf_extractor.table_boundary_llm import run_llm_table_enhancement
        result = run_llm_table_enhancement(tables, liteparse_data)

    处理流程：
        1. LLM 边界检测 → 识别每页逻辑表边界
        2. 同页碎片合并 + 跨页续表拼接
        3. liteparse 精确文本填充每个单元格

    Args:
        tables: 当前全部表格列表（含 page, data, type 等字段）
        liteparse_data: liteparse ParseResult.to_dict()（可选，无则跳过 LLM 步骤）
        progress_callback: callable(percent, message)
        pdf_path: PDF 文件路径（用于生成日志文件）

    Returns:
        {
            "tables": [...],          # 增强后的表格列表
            "llm_applied": bool,      # LLM 边界检测是否成功执行
            "fill_applied": bool,     # liteparse 文本填充是否成功执行
            "merge_logs": [...],      # 合并日志
            "stats": {
                "tables_before": N,   # 增强前表格数
                "tables_after": N,    # 增强后表格数
                "cells_changed": N,   # 填充改动的单元格数
                "rows_added": N,      # 从 liteparse 补充的行数
                "tokens_used": N,     # LLM token 消耗
            },
            "error": str | None,      # 错误信息
            "log_path": str | None,   # 日志文件路径
        }
    """
    import copy
    from .utils import load_config

    # ---- 初始化日志 ----
    log_lines = []
    log_path = None

    def log(msg=""):
        log_lines.append(msg)

    def log_save():
        nonlocal log_path
        if not pdf_path:
            return
        try:
            cache_dir = get_pdf_cache_dir(pdf_path)
            cache_dir.mkdir(parents=True, exist_ok=True)
            log_path = str(cache_dir / "llm_boundary_log.txt")
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(log_lines))
        except Exception as e:
            print(f"  [LLM边界日志] 保存失败: {e}")

    def log_data_snapshot(label, data, max_rows=8, max_cols=8):
        if not data:
            log(f"    {label}: （空）")
            return
        rows = len(data)
        cols = max((len(r) for r in data), default=0)
        log(f"    {label}: {rows}行 × {cols}列")
        for ri, row in enumerate(data[:max_rows]):
            cells = []
            for ci, c in enumerate(row[:max_cols]):
                s = str(c).strip() if c is not None else ""
                if len(s) > 16:
                    s = s[:13] + "..."
                cells.append(s)
            suffix = " ..." if len(row) > max_cols else ""
            log(f"      行{ri}: {' | '.join(cells)}{suffix}")
        if rows > max_rows:
            log(f"      ... (省略 {rows - max_rows} 行)")

    log("=" * 72)
    log(f"  LLM 边界优化日志 — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 72)
    log(f"  共 {len(tables)} 张表格待处理")
    if pdf_path:
        log(f"  PDF: {pdf_path}")
    log(f"  liteparse: {'可用' if liteparse_data else '不可用'}")
    log()

    # 深拷贝，不修改原数据
    tables = copy.deepcopy(tables)

    tables_before = len(tables)
    merge_logs = []
    llm_applied = False
    fill_applied = False
    cells_changed = 0
    rows_added = 0
    tokens_used = 0
    error = None

    # ---- 记录原始表格概览 ----
    log("─" * 3 + " 原始表格概览 " + "─" * 53)
    for i, t in enumerate(tables):
        page = t.get("page", "?")
        data = t.get("data", [])
        rows_t = len(data)
        cols_t = max((len(r) for r in data), default=0) if data else 0
        log(f"  表#{i} (P{page}): {rows_t}行 × {cols_t}列")
    log()

    print("\n" + "=" * 50)
    print("  [LLM增强] 手动触发开始")
    print("=" * 50)

    # ---- Step 1: LLM 边界检测 ----
    log("─" * 3 + " Step 1: LLM 边界检测 " + "─" * 46)

    if liteparse_data:
        config = load_config()
        api_key = config.get("deepseek_api_key", "")

        if api_key:
            try:
                if progress_callback:
                    progress_callback(5, "LLM 分析表格边界...")

                merged, logs, usage = detect_and_merge_with_llm(
                    tables, liteparse_data,
                    api_key=api_key,
                    endpoint=config.get("deepseek_endpoint"),
                    model=config.get("deepseek_model"),
                    progress_callback=progress_callback,
                    log_callback=log,
                )
                tables = merged
                merge_logs = logs
                tokens_used = usage.get("total_tokens", 0)
                llm_applied = True

                log(f"  LLM 边界检测: 成功")
                log(f"  模型: {usage.get('model', config.get('deepseek_model', ''))}")
                log(f"  Token 消耗: {tokens_used}")
                log(f"  API 调用次数: {usage.get('api_calls', 0)}")

                # 记录合并日志
                if merge_logs:
                    log("  合并操作:")
                    for ml in merge_logs:
                        log(f"    - {ml}")

                # 记录合并后的表格概览
                log("  合并后表格概览:")
                for i, t in enumerate(tables):
                    page = t.get("page", "?")
                    data = t.get("data", [])
                    rows_t = len(data)
                    cols_t = max((len(r) for r in data), default=0) if data else 0
                    cross = " [跨页]" if t.get("_cross_page_merged") else ""
                    log(f"    表#{i} (P{page}): {rows_t}行 × {cols_t}列{cross}")

            except Exception as e:
                error = f"LLM 边界检测失败: {e}"
                log(f"  ERROR: {error}")
                print(f"  [LLM增强] {error}")
        else:
            log("  未配置 API Key，跳过 LLM 边界检测")
            print("  [LLM增强] 未配置 API Key，跳过 LLM 边界检测")
    else:
        log("  无 liteparse 数据，跳过 LLM 边界检测")
        print("  [LLM增强] 无 liteparse 数据，跳过 LLM 边界检测")

    log()

    # ---- Step 2: liteparse 文本填充 ----
    log("─" * 3 + " Step 2: liteparse 文本填充 " + "─" * 44)

    if liteparse_data:
        try:
            if progress_callback:
                progress_callback(85, "liteparse 文本填充单元格...")

            from codes.table_validator.liteparse_cell_filler import fill_all_tables_with_liteparse
            tables, fill_stats = fill_all_tables_with_liteparse(tables, liteparse_data)
            cells_changed = fill_stats.get("total_cells_changed", 0)
            rows_added = fill_stats.get("total_rows_added", 0)
            fill_applied = True

            log(f"  文本填充: 成功")
            log(f"  单元格修正: {cells_changed}")
            log(f"  补充行: {rows_added}")

            # 记录每张表的填充详情
            table_stats = fill_stats.get("table_stats", [])
            if table_stats:
                log("  各表填充详情:")
                for ts in table_stats:
                    idx = ts.get("table_index", "?")
                    changed = ts.get("cells_changed", 0)
                    added = ts.get("rows_added", 0)
                    phantom = ts.get("phantom_rows_removed", 0)
                    parts = []
                    if changed:
                        parts.append(f"{changed}格修正")
                    if added:
                        parts.append(f"{added}行补充")
                    if phantom:
                        parts.append(f"{phantom}幽灵行删除")
                    if parts:
                        log(f"    表#{idx}: {', '.join(parts)}")

            # 记录填充后数据快照（有改动的表）
            for i, t in enumerate(tables):
                page = t.get("page", "?")
                data = t.get("data", [])
                # 只记录有改动的表
                ts_i = table_stats[i] if i < len(table_stats) else {}
                if ts_i.get("cells_changed", 0) > 0 or ts_i.get("rows_added", 0) > 0:
                    log(f"  表#{i} (P{page}) 填充后数据:")
                    log_data_snapshot("", data)

        except Exception as e:
            if error:
                error += f" | 文本填充失败: {e}"
            else:
                error = f"文本填充失败: {e}"
            log(f"  ERROR: {error}")
            print(f"  [LLM增强] {error}")
    else:
        log("  无 liteparse 数据，跳过文本填充")
        print("  [LLM增强] 无 liteparse 数据，跳过文本填充")

    log()

    # ---- 汇总 ----
    tables_after = len(tables)
    stats = {
        "tables_before": tables_before,
        "tables_after": tables_after,
        "cells_changed": cells_changed,
        "rows_added": rows_added,
        "tokens_used": tokens_used,
    }

    log("─" * 3 + " 最终汇总 " + "─" * 57)
    log(f"  表格数: {tables_before} → {tables_after}")
    log(f"  单元格修正: {cells_changed}")
    log(f"  补充行: {rows_added}")
    log(f"  Token 消耗: {tokens_used}")
    if error:
        log(f"  错误: {error}")
    log()

    print(f"  [LLM增强] 完成: {tables_before}→{tables_after}张表, "
          f"{cells_changed}格修正, {rows_added}行补充, "
          f"{tokens_used} tokens")
    print("=" * 50 + "\n")

    # 保存日志
    log_save()
    if log_path:
        print(f"  [LLM边界日志] 已保存到 {log_path}")

    return {
        "tables": tables,
        "llm_applied": llm_applied,
        "fill_applied": fill_applied,
        "merge_logs": merge_logs,
        "stats": stats,
        "error": error,
        "log_path": log_path,
    }
