# -*- coding: utf-8 -*-
"""可疑任务队列构建（只扫描、不改数据）。

跨页/相邻硬约束：
1. 只考虑文档顺序上相邻的两张「真表」
2. 页码只能相同或相差 1（绝不能跨更远）
3. 同页两表之间的夹层文本 = 后表说明/表头，绝不当页眉，有文本则禁止合并
4. 跨页夹层仅当位于后页页首时才可能是页眉；表前说明阻断合并
5. 合并一般要求列数相同；仅「跨页 + 后表缺表头」允许列数不同，且须特殊标注
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .conservation import (
    count_consecutive_empty_rows,
    count_empty_columns,
    nonempty_multiset,
    table_has_own_column_header,
    table_missing_header,
    table_starts_with_subsection_caption,
)
from .liteparse_bridge import (
    cross_page_gap_zones,
    meaningful_text_lines,
    page_gap_text,
    region_text_for_table,
)
from .models import Confidence, FormatTask, TaskStatus, TaskType

_NARRATIVE_GAP_RE = re.compile(
    r"(上述|如下|下列|列示|包括|分别为|本集团|本公司|期末余额|详见|参见|附注)"
)


def _ncols(table: dict) -> int:
    data = table.get("data") or []
    if isinstance(data, str):
        return 1 if data.strip() else 0
    if not isinstance(data, list):
        return 0
    return max((len(r) for r in data if isinstance(r, list)), default=0)


def _page_of(table: dict) -> int:
    return int(table.get("page") or 0)


def _is_real_table(table: dict) -> bool:
    """是否视为可参与跨页判断的真表（排除纯文本段落、空壳、质检判定非表）。"""
    if not table:
        return False
    if table.get("_format_hidden"):
        return False
    # 质检已判非表 → 不参与缺表头/跨页合并
    if table.get("is_real_table") is False:
        return False
    cat = str(table.get("table_category") or "")
    if cat in ("非表格", "文本段落"):
        return False
    t = table.get("type")
    if t in ("text", "paragraph"):
        return False
    data = table.get("data")
    if not data:
        return False
    if isinstance(data, str):
        return False
    if not isinstance(data, list):
        return False
    # 至少有一个非空单元格
    for row in data:
        if not isinstance(row, list):
            continue
        if any(str(c or "").strip() for c in row):
            return True
    return False


def _entry_text_blob(table: dict) -> str:
    """中间夹层条目的文本（用于判断是表头还是正文）。"""
    if table.get("type") in ("text", "paragraph"):
        data = table.get("data")
        if isinstance(data, str):
            return data.strip()
    ctx = (table.get("context_text") or table.get("caption") or
           table.get("title") or table.get("llm_title") or "")
    if ctx.strip():
        return ctx.strip()
    data = table.get("data")
    if isinstance(data, list):
        parts = []
        for row in data[:3]:
            if isinstance(row, list):
                parts.extend(str(c).strip() for c in row if str(c or "").strip())
        return "\n".join(parts)
    return ""


def _is_page_running_header_text(text: str) -> bool:
    """跨页后页页首：公司名/年报标题/页码等页眉。"""
    s = (text or "").strip()
    if not s:
        return True
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if not lines or len(lines) > 4:
        return False
    for ln in lines:
        if re.match(r"^第?\s*\d+\s*页?$", ln):
            continue
        if "股份有限公司" in ln or "有限公司" in ln:
            continue
        if "年度报告" in ln or "年报" in ln or "半年度报告" in ln:
            continue
        # 其它短行若像叙述则否
        if _NARRATIVE_GAP_RE.search(ln) or len(ln) > 40:
            return False
        if ln.count("。") >= 1:
            return False
    return True


def _is_headerish_text(text: str) -> bool:
    """短标题/表头说明（同页夹层用：识别是否「短」，但不因此允许合并）。"""
    s = (text or "").strip()
    if not s:
        return True
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if not lines:
        return True
    if len(lines) > 3:
        return False
    joined = "".join(lines)
    if len(joined) > 80:
        return False
    if _NARRATIVE_GAP_RE.search(joined):
        return False
    # 长叙述句（句号多、很长）视为正文
    if len(joined) > 40 and joined.count("。") >= 1:
        return False
    # 纯页码
    if all(re.match(r"^第?\s*\d+\s*页?$", ln) for ln in lines):
        return True
    return True


def _is_blocking_middle(table: dict) -> bool:
    """两表之间的夹层是否阻断「相邻」。"""
    if _is_real_table(table):
        return True  # 中间又冒出一张真表 → 不直接相邻
    text = _entry_text_blob(table)
    # 同页夹层：任意有意义文本都视为后表说明，打断「可合并相邻」
    # （仍可用于找相邻对，但合并阶段会再否决）
    return not _is_headerish_text(text)


def pages_are_adjacent(page_a: int, page_b: int) -> bool:
    """同页或后页 = 前页+1。"""
    return page_b == page_a or page_b == page_a + 1


def find_prev_adjacent_table(tables: List[dict], index: int) -> Optional[int]:
    """在 index 之前找一张文档顺序相邻的真表。

    - 页距必须 ≤ 1（后表页 ∈ {前表页, 前表页+1}）
    - 中间只允许空条目/短表头文本；出现另一张真表或长正文则无相邻前表
    """
    if index <= 0 or index >= len(tables):
        return None
    cur_page = _page_of(tables[index])
    for j in range(index - 1, -1, -1):
        tj = tables[j]
        pj = _page_of(tj)
        # 已经比当前页早超过 1 页 → 不可能再相邻
        if pj < cur_page - 1:
            return None
        if _is_real_table(tj):
            if not pages_are_adjacent(pj, cur_page):
                return None
            return j
        if _is_blocking_middle(tj):
            return None
        # 短表头文本：跳过继续往前找
    return None


def find_next_adjacent_table(tables: List[dict], index: int) -> Optional[int]:
    """在 index 之后找一张文档顺序相邻的真表。"""
    if index < 0 or index >= len(tables) - 1:
        return None
    cur_page = _page_of(tables[index])
    for j in range(index + 1, len(tables)):
        tj = tables[j]
        pj = _page_of(tj)
        if pj > cur_page + 1:
            return None
        if _is_real_table(tj):
            if not pages_are_adjacent(cur_page, pj):
                return None
            return j
        if _is_blocking_middle(tj):
            return None
    return None


def iter_adjacent_table_pairs(tables: List[dict]) -> List[Tuple[int, int]]:
    """全部相邻真表对 (prev, next)，保证页相邻且中间无阻断。"""
    pairs = []
    seen = set()
    for i, t in enumerate(tables):
        if not _is_real_table(t):
            continue
        j = find_next_adjacent_table(tables, i)
        if j is None:
            continue
        key = (i, j)
        if key in seen:
            continue
        seen.add(key)
        pairs.append((i, j))
    return pairs


def build_page_seq_map(tables: List[dict]) -> dict:
    """全局下标 → (page, 页内序号)，与对比预览一致。"""
    page_seq = {}
    mapping = {}
    for idx, table in enumerate(tables or []):
        page = int(table.get("page") or 0)
        page_seq[page] = page_seq.get(page, 0) + 1
        mapping[idx] = (page, page_seq[page])
    return mapping


def format_location(tables: List[dict], index: int, related: Optional[List[int]] = None) -> str:
    """生成 P页号_序号。

    有关联表时按文档顺序排列：前表+后表（避免把远页拼在后面造成误解）。
    """
    idx_map = build_page_seq_map(tables)

    def _one(i: int) -> str:
        r = idx_map.get(i)
        if r:
            return f"P{r[0]}_{r[1]}"
        page = int((tables[i].get("page") if 0 <= i < len(tables) else 0) or 0)
        return f"P{page}_?"

    if not related:
        return _one(index)

    indices = [index] + list(related)
    # 文档顺序
    indices = sorted(set(indices))
    return "+".join(_one(i) for i in indices)


def _attach_location(task: FormatTask, tables: List[dict]) -> FormatTask:
    loc = format_location(tables, task.table_index, task.related_indices or None)
    task.evidence = dict(task.evidence or {})
    task.evidence["location"] = loc
    idx_map = build_page_seq_map(tables)
    if task.table_index in idx_map:
        p, s = idx_map[task.table_index]
        task.evidence["page_seq"] = s
        task.page = p
    return task


def _gap_allows_merge(
    gap_lines: List[str],
    *,
    same_page: bool,
    page_header_lines: Optional[List[str]] = None,
    pre_table_lines: Optional[List[str]] = None,
) -> Tuple[bool, bool, str]:
    """返回 (允许合并候选, 是否仅页眉夹层, 否决原因)。

    规则：
    - 同页：有任何夹层文本 → 禁止（那是后表说明/表头，不是页眉）
    - 跨页：仅后页页首页眉可夹；表前说明/叙述 → 禁止
    - 无文本 → 允许
    """
    if same_page:
        if gap_lines:
            return False, False, "同页夹层文本视为后表说明/表头，禁止合并"
        return True, False, ""

    pre = meaningful_text_lines("\n".join(pre_table_lines or []))
    if pre:
        return False, False, "跨页但后表前有说明文字（非页首页眉），禁止合并"

    header_lines = meaningful_text_lines("\n".join(page_header_lines or []))
    if not header_lines and not gap_lines:
        return True, False, ""
    # 无分区信息时退回整段 gap：须像页眉
    check = header_lines or gap_lines
    blob = "\n".join(check)
    if _is_page_running_header_text(blob):
        return True, True, ""
    if _is_headerish_text(blob) and not _NARRATIVE_GAP_RE.search(blob):
        # 极短非叙述：仍标为页眉夹层，但不自动应用
        return True, True, ""
    return False, False, "跨页夹层不像页眉（含叙述/小节），禁止合并"


def _cols_merge_policy(
    ncols_a: int,
    ncols_b: int,
    *,
    same_page: bool,
    missing_hdr: bool,
) -> Tuple[bool, bool, str]:
    """列数策略。

    Returns:
        (允许继续, 列数不一致需特殊标注, 说明)
    """
    if ncols_a <= 0 or ncols_b <= 0:
        return False, False, "列数无效"
    if ncols_a == ncols_b:
        return True, False, ""
    # 仅跨页且后表缺表头：允许列数不同，强制人工复核（可能一侧有合并单元格）
    if (not same_page) and missing_hdr:
        return (
            True,
            True,
            "跨页缺表头但列数不同，可能存在合并单元格导致列数不一致，需特殊复核",
        )
    return False, False, "列数不同且非「跨页缺表头」情形，禁止合并"


def build_candidate_tasks(
    tables: List[dict],
    liteparse_data: Optional[dict] = None,
) -> List[FormatTask]:
    """构建四类候选任务。"""
    tasks: List[FormatTask] = []
    tasks.extend(_header_cross_page_tasks(tables, liteparse_data))
    tasks.extend(_empty_split_tasks(tables, liteparse_data))
    tasks.extend(_cross_page_merge_tasks(tables, liteparse_data, tasks))
    tasks.extend(_text_table_tasks(tables))
    tasks = _resolve_bidirectional_merge_conflicts(tasks, tables)
    return [_attach_location(t, tables) for t in tasks]


_CONF_RANK = {
    Confidence.HIGH: 3,
    Confidence.MEDIUM: 2,
    Confidence.LOW: 1,
    Confidence.UNCERTAIN: 0,
}


def _score_merge_edge(task: FormatTask) -> int:
    """合并边强度：置信度 + 证据加分。"""
    ev = task.evidence or {}
    score = _CONF_RANK.get(task.confidence, 0) * 10
    if ev.get("header_missing_next") or ev.get("header_missing"):
        score += 3
    pages = ev.get("pages") or []
    if isinstance(pages, (list, tuple)) and len(pages) == 2:
        try:
            if int(pages[1]) == int(pages[0]) + 1:
                score += 2  # 真跨页略优于同页粘连误召回
        except Exception:
            pass
    if ev.get("gap_headerish"):
        score -= 1
    if ev.get("cols_close") is False:
        score -= 2
    return score


def _demote_merge_task(task: FormatTask, *, drop_link: bool, note: str) -> None:
    """降权或切断一侧合并关联。"""
    task.evidence = dict(task.evidence or {})
    task.evidence["bidirectional_merge_conflict"] = True
    task.evidence["conflict_note"] = note
    if task.proposal:
        task.proposal = dict(task.proposal)
        task.proposal["auto_apply"] = False
    if drop_link:
        if task.task_type == TaskType.HEADER_CROSS_PAGE:
            task.related_indices = []
            task.evidence.pop("prev_index", None)
            task.confidence = Confidence.LOW
            task.reason = (
                f"{task.reason}；因该表同时挂前后合并，已切断较弱一侧关联"
            )
        else:
            task.confidence = Confidence.LOW
            task.status = TaskStatus.REJECTED
            task.reason = (
                f"{task.reason}；双向合并冲突：较弱一侧已否决"
            )
            task.proposal = dict(task.proposal or {})
            task.proposal["auto_apply"] = False
            task.proposal["rejected_by"] = "bidirectional_merge_conflict"
    else:
        # 两侧接近时：都降到需人工确认，禁止自动合并
        if task.confidence == Confidence.HIGH:
            task.confidence = Confidence.MEDIUM
        elif task.confidence == Confidence.MEDIUM:
            task.confidence = Confidence.LOW
        task.reason = (
            f"{task.reason}；同一表同时与前后合并，至少一侧存疑，请人工二选一"
        )


def _resolve_bidirectional_merge_conflicts(
    tasks: List[FormatTask],
    tables: List[dict],
) -> List[FormatTask]:
    """同一张表若同时是「前合并的后表」又是「后合并的前表」，则至少一侧易错。

    策略：
    - 两侧强度差明显 → 保留强侧，切断/否决弱侧
    - 两侧接近 → 两侧都降权且禁止 auto_apply，留给人工
    """
    from collections import defaultdict

    as_prev: dict = defaultdict(list)  # mid -> [(next, task)]
    as_next: dict = defaultdict(list)  # mid -> [(prev, task)]

    for task in tasks:
        if task.status == TaskStatus.REJECTED:
            continue
        if task.task_type == TaskType.CROSS_PAGE_MERGE and task.related_indices:
            i, j = task.table_index, int(task.related_indices[0])
            as_prev[i].append((j, task))
            as_next[j].append((i, task))
        elif task.task_type == TaskType.HEADER_CROSS_PAGE and task.related_indices:
            j = task.table_index
            i = int(task.related_indices[0])
            as_prev[i].append((j, task))
            as_next[j].append((i, task))

    for mid in sorted(set(as_prev) & set(as_next)):
        lefts = as_next[mid]   # 与前表：prev+mid
        rights = as_prev[mid]  # 与后表：mid+next
        if not lefts or not rights:
            continue

        # 各取该方向上最强的一条边代表
        left_other, left_task = max(lefts, key=lambda x: _score_merge_edge(x[1]))
        right_other, right_task = max(rights, key=lambda x: _score_merge_edge(x[1]))
        if left_task is right_task:
            continue

        ls, rs = _score_merge_edge(left_task), _score_merge_edge(right_task)
        note = (
            f"表#{mid} 同时候选合并前表#{left_other}与后表#{right_other}"
            f"（分 {ls}/{rs}）"
        )

        def _mark_kept(task: FormatTask, side: str) -> None:
            task.evidence = dict(task.evidence or {})
            task.evidence["bidirectional_merge_conflict"] = True
            task.evidence["conflict_kept"] = side
            task.evidence["conflict_note"] = note + f" → 保留{('前' if side == 'prev' else '后')}侧"
            if task.proposal:
                task.proposal = dict(task.proposal)
                # 冲突后即使较强也不自动应用
                task.proposal["auto_apply"] = False

        # 明显更强：分差 ≥ 4（约等于高置信 vs 中置信）
        if ls >= rs + 4:
            for _, t in rights:
                _demote_merge_task(t, drop_link=True, note=note)
            for _, t in lefts:
                _mark_kept(t, "prev")
        elif rs >= ls + 4:
            for _, t in lefts:
                _demote_merge_task(t, drop_link=True, note=note)
            for _, t in rights:
                _mark_kept(t, "next")
        else:
            for _, t in lefts + rights:
                _demote_merge_task(t, drop_link=False, note=note)

    # 已被否决的 merge 任务从队列去掉，避免 UI 仍展示「可接受」
    filtered: List[FormatTask] = []
    for t in tasks:
        if (
            t.task_type == TaskType.CROSS_PAGE_MERGE
            and t.status == TaskStatus.REJECTED
            and (t.proposal or {}).get("rejected_by") == "bidirectional_merge_conflict"
        ):
            continue
        filtered.append(t)
    return filtered


def _header_cross_page_tasks(
    tables: List[dict],
    liteparse_data: Optional[dict],
) -> List[FormatTask]:
    out = []
    for i, t in enumerate(tables):
        if not _is_real_table(t):
            continue
        if not table_missing_header(t):
            continue

        evidence = {
            "header_missing": True,
            "anomaly": (t.get("_anomaly") or {}).get("rule_ids"),
        }
        conf = Confidence.MEDIUM
        reason = "表格疑似缺失表头"
        related: List[int] = []

        # 独立新表（小节标题 / 自带列头）绝不当续表挂前表
        if _is_independent_new_table(t):
            continue

        prev_i = find_prev_adjacent_table(tables, i)
        if prev_i is not None:
            prev = tables[prev_i]
            page_a = _page_of(prev)
            page_b = _page_of(t)
            # 双保险：页必须相邻
            if not pages_are_adjacent(page_a, page_b):
                prev_i = None
            else:
                related = [prev_i]
                evidence["prev_index"] = prev_i
                evidence["prev_page"] = page_a
                evidence["page"] = page_b
                same_page = page_a == page_b
                na, nb = _ncols(prev), _ncols(t)
                cols_ok, cols_mismatch_note_flag, cols_note = _cols_merge_policy(
                    na, nb, same_page=same_page, missing_hdr=True,
                )
                evidence["cols"] = [na, nb]
                evidence["cols_equal"] = na == nb
                evidence["cols_mismatch_needs_review"] = cols_mismatch_note_flag
                gap = page_gap_text(liteparse_data, page_a, page_b, prev, t)
                gap_lines = meaningful_text_lines(gap)
                evidence["gap_line_count"] = len(gap_lines)
                evidence["gap_preview"] = gap_lines[:3]
                hdr_lines, pre_lines = ([], [])
                if not same_page:
                    hdr_lines, pre_lines = cross_page_gap_zones(
                        liteparse_data, page_b, t,
                    )
                allow, headerish, gap_note = _gap_allows_merge(
                    gap_lines,
                    same_page=same_page,
                    page_header_lines=hdr_lines,
                    pre_table_lines=pre_lines,
                )
                evidence["gap_headerish"] = headerish
                if gap_note:
                    evidence["gap_block_reason"] = gap_note
                if cols_note:
                    evidence["cols_note"] = cols_note
                if not allow:
                    conf = Confidence.LOW
                    related = []
                    evidence.pop("prev_index", None)
                    reason = f"缺表头，但{gap_note or '夹层阻断'}，不作合并关联"
                elif not cols_ok:
                    conf = Confidence.LOW
                    related = []
                    evidence.pop("prev_index", None)
                    reason = f"缺表头，但{cols_note}"
                elif same_page and na == nb:
                    conf = Confidence.MEDIUM
                    reason = "同页缺表头且列数相同、无夹层文本 → 疑似误拆，需确认"
                elif (not same_page) and not headerish and na == nb:
                    conf = Confidence.HIGH
                    reason = "缺表头 + 跨页相邻 + 中间无正文 + 列数相同 → 很可能跨页续表"
                elif (not same_page) and headerish and na == nb:
                    conf = Confidence.MEDIUM
                    reason = "缺表头 + 跨页，中间仅页首页眉，需确认是否续表"
                elif cols_mismatch_note_flag:
                    conf = Confidence.MEDIUM
                    reason = cols_note
                else:
                    conf = Confidence.LOW
                    reason = "缺表头，与前表相邻但信号不足"

        out.append(
            FormatTask(
                task_id=f"hdr-{i}",
                task_type=TaskType.HEADER_CROSS_PAGE,
                table_index=i,
                related_indices=related,
                page=_page_of(t),
                status=TaskStatus.CANDIDATE,
                confidence=conf,
                reason=reason,
                evidence=evidence,
            )
        )
    return out


def _empty_split_tasks(
    tables: List[dict],
    liteparse_data: Optional[dict],
) -> List[FormatTask]:
    out = []
    for i, t in enumerate(tables):
        if not _is_real_table(t):
            continue
        data = t.get("data") or []
        empty_row_ranges = count_consecutive_empty_rows(data)
        empty_cols = count_empty_columns(data)
        if not empty_row_ranges and not empty_cols:
            continue

        lp_text = region_text_for_table(liteparse_data, t)
        table_ms = nonempty_multiset(data)
        lp_tokens = [x for x in (lp_text or "").replace("\n", " ").split() if x.strip()]
        from collections import Counter
        from .conservation import cell_key

        lp_ms = Counter(cell_key(x) for x in lp_tokens if cell_key(x))
        missing_from_table = {
            k: n for k, n in lp_ms.items() if table_ms.get(k, 0) < n
        }
        miss_sample = list(missing_from_table.items())[:12]

        if miss_sample and (empty_row_ranges or empty_cols):
            conf = Confidence.HIGH
            reason = "连续空行/空列，且 liteparse 中有内容未在表单元格中独立出现 → 疑似分割粘连"
        elif empty_row_ranges or len(empty_cols) >= 1:
            conf = Confidence.MEDIUM
            reason = "存在连续空行或空列，建议对照 liteparse 检查分割"
        else:
            continue

        out.append(
            FormatTask(
                task_id=f"empty-{i}",
                task_type=TaskType.EMPTY_SPLIT,
                table_index=i,
                page=_page_of(t),
                status=TaskStatus.CANDIDATE,
                confidence=conf,
                reason=reason,
                evidence={
                    "empty_row_ranges": empty_row_ranges,
                    "empty_cols": empty_cols,
                    "liteparse_tokens_missing_in_table": miss_sample,
                    "liteparse_preview": (lp_text or "")[:400],
                },
            )
        )
    return out


def _is_independent_new_table(table: dict) -> bool:
    """后表已是独立新表（新小节/自带列头）→ 禁止与前表合并。"""
    if table_starts_with_subsection_caption(table):
        return True
    if table_has_own_column_header(table):
        return True
    return False


def _cross_page_merge_tasks(
    tables: List[dict],
    liteparse_data: Optional[dict],
    existing: List[FormatTask],
) -> List[FormatTask]:
    """仅对「相邻真表对」生成合并任务（页距≤1）。"""
    out = []
    hdr_pairs = set()
    for task in existing:
        if task.task_type != TaskType.HEADER_CROSS_PAGE:
            continue
        if task.related_indices and task.confidence in (Confidence.HIGH, Confidence.MEDIUM):
            # related 是前表，table_index 是后表
            hdr_pairs.add((task.related_indices[0], task.table_index))

    for i, j in iter_adjacent_table_pairs(tables):
        a, b = tables[i], tables[j]
        page_a, page_b = _page_of(a), _page_of(b)
        if not pages_are_adjacent(page_a, page_b):
            continue

        # 硬否决：后表以（五）开头或自带列头 → 新表，不合并
        if _is_independent_new_table(b):
            continue

        same_page = page_a == page_b
        gap = page_gap_text(liteparse_data, page_a, page_b, a, b)
        gap_lines = meaningful_text_lines(gap)
        hdr_lines, pre_lines = ([], [])
        if not same_page:
            hdr_lines, pre_lines = cross_page_gap_zones(liteparse_data, page_b, b)
        allow, headerish, gap_note = _gap_allows_merge(
            gap_lines,
            same_page=same_page,
            page_header_lines=hdr_lines,
            pre_table_lines=pre_lines,
        )
        if not allow:
            continue

        na, nb = _ncols(a), _ncols(b)
        missing_hdr = table_missing_header(b)
        cols_ok, cols_mismatch_flag, cols_note = _cols_merge_policy(
            na, nb, same_page=same_page, missing_hdr=missing_hdr,
        )
        if not cols_ok:
            continue

        suggest = b.get("_suggest_merge_to")
        suggest_hit = suggest == i
        in_hdr = (i, j) in hdr_pairs

        # 同页：仅「缺表头 + 列数相同 + 无夹层」可提案（误拆），绝不因短说明合并
        if same_page:
            if not missing_hdr and not suggest_hit and not in_hdr:
                continue
            conf = Confidence.MEDIUM
            reason = "同页相邻、无夹层且列数相同 → 疑似误拆，需确认（有夹层文本则已否决）"
        elif in_hdr and not headerish and not cols_mismatch_flag:
            conf = Confidence.HIGH
            reason = "缺表头跨页候选 + 相邻 + 中间无正文 + 列数相同 → 建议合并"
        elif (suggest_hit or missing_hdr) and not headerish and not cols_mismatch_flag:
            conf = Confidence.HIGH
            reason = "跨页相邻、中间无正文、列数相同 → 建议合并"
        elif missing_hdr and headerish and not cols_mismatch_flag:
            conf = Confidence.MEDIUM
            reason = "跨页缺表头，中间仅页首页眉，合并需确认"
        elif cols_mismatch_flag:
            conf = Confidence.MEDIUM
            reason = cols_note
        elif na == nb and page_b == page_a + 1:
            conf = Confidence.LOW
            reason = "跨页相邻且列数相同，弱信号复核"
        else:
            continue

        out.append(
            FormatTask(
                task_id=f"merge-{i}-{j}",
                task_type=TaskType.CROSS_PAGE_MERGE,
                table_index=i,
                related_indices=[j],
                page=page_a,
                status=TaskStatus.CANDIDATE,
                confidence=conf,
                reason=reason,
                evidence={
                    "pair": [i, j],
                    "pages": [page_a, page_b],
                    "gap_line_count": len(gap_lines),
                    "gap_preview": gap_lines[:3],
                    "gap_headerish": headerish,
                    "gap_block_reason": gap_note or None,
                    "cols": [na, nb],
                    "cols_equal": na == nb,
                    "cols_mismatch_needs_review": cols_mismatch_flag,
                    "cols_note": cols_note or None,
                    "header_missing_next": missing_hdr,
                    "suggest_merge_to": suggest,
                },
                proposal={
                    "action": "merge",
                    "keep_index": i,
                    "absorb_index": j,
                    # 列数不一致或仅页眉夹层：禁止自动合并
                    "auto_apply": (
                        conf == Confidence.HIGH
                        and not headerish
                        and not cols_mismatch_flag
                        and not same_page
                    ),
                },
            )
        )
    return out


def _text_table_tasks(tables: List[dict]) -> List[FormatTask]:
    """标记疑似误入表内的长叙述行（只提案，默认不自动删）。"""
    out = []
    for i, t in enumerate(tables):
        if not _is_real_table(t):
            continue
        data = t.get("data") or []
        suspect_rows = []
        for ri, row in enumerate(data):
            cells = [str(c or "").strip() for c in (row or []) if str(c or "").strip()]
            if len(cells) != 1:
                continue
            text = cells[0]
            if len(text) < 40:
                continue
            digit_n = sum(ch.isdigit() for ch in text)
            if digit_n / max(len(text), 1) < 0.08 and len(text) >= 40:
                suspect_rows.append({"row": ri, "preview": text[:80]})
        if not suspect_rows:
            continue
        out.append(
            FormatTask(
                task_id=f"text-{i}",
                task_type=TaskType.TEXT_TABLE_SPLIT,
                table_index=i,
                page=_page_of(t),
                status=TaskStatus.CANDIDATE,
                confidence=Confidence.MEDIUM if len(suspect_rows) <= 3 else Confidence.LOW,
                reason="表内出现长叙述单格行，疑似文表未分割干净（默认仅标记，不删内容）",
                evidence={"suspect_rows": suspect_rows},
                proposal={
                    "action": "flag_text_rows",
                    "rows": [x["row"] for x in suspect_rows],
                    "auto_apply": False,
                },
            )
        )
    return out
