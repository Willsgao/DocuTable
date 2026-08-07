# -*- coding: utf-8 -*-
"""字框按重复年报表头带切段，避免两表混用同一批 _source_words。"""

from __future__ import annotations

import re
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

from codes.reconstruct.grid_nucleus.types import RowCluster
from codes.table_engine.geometry.numeric import is_numeric_data_cell, is_year_cell

_YEAR_RE = re.compile(r"(?:19|20)\d{2}\s*年")
_FOOTNOTE_MARK_RE = re.compile(r"^[（(]?\d+[)）]")
_CHANGE_RE = re.compile(r"增减|比上")


def _word_text(w: Dict[str, Any]) -> str:
    return str(w.get("text") or "").strip()


def _word_y0(w: Dict[str, Any]) -> float:
    return float(w.get("y0") or 0.0)


def _word_cy(w: Dict[str, Any]) -> float:
    y0 = float(w.get("y0") or 0.0)
    y1 = float(w.get("y1") or y0)
    return (y0 + y1) / 2.0


def _cluster_word_rows(
    words: Sequence[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    if not words:
        return []
    heights = [
        max(1.0, float(w.get("y1") or 0) - float(w.get("y0") or 0))
        for w in words
    ]
    h_med = median(heights) if heights else 10.0
    ordered = sorted(words, key=lambda w: (_word_cy(w), float(w.get("x0") or 0)))
    rows: List[List[Dict[str, Any]]] = []
    centers: List[float] = []
    gap = max(3.0, h_med * 0.65)
    for w in ordered:
        cy = _word_cy(w)
        if not rows or abs(cy - centers[-1]) > gap:
            rows.append([w])
            centers.append(cy)
        else:
            rows[-1].append(w)
            centers[-1] = sum(_word_cy(x) for x in rows[-1]) / len(rows[-1])
    return rows


def _row_is_year_header_band(row_words: Sequence[Dict[str, Any]]) -> bool:
    texts = [_word_text(w) for w in row_words if _word_text(w)]
    if len(texts) < 2:
        return False
    year_hits = 0
    for t in texts:
        if is_year_cell(t) or _YEAR_RE.search(t):
            year_hits += 1
    if year_hits < 2:
        return False
    if any(is_numeric_data_cell(t) for t in texts):
        return False
    return True


def _row_has_body_signal(row_words: Sequence[Dict[str, Any]]) -> bool:
    texts = [_word_text(w) for w in row_words if _word_text(w)]
    if not texts:
        return False
    if any(is_numeric_data_cell(t) for t in texts):
        return True
    if any(re.match(r"^\.\d+$", t) for t in texts):
        return True
    return False


def _row_is_note_start(row_words: Sequence[Dict[str, Any]]) -> bool:
    """表后「注：」起头行。"""
    texts = [_word_text(w) for w in row_words if _word_text(w)]
    if not texts:
        return False
    first = texts[0]
    if first in ("注：", "注:", "注") or first.startswith("注：") or first.startswith("注:"):
        return True
    joined = "".join(texts)
    return bool(re.match(r"^注[：:]", joined))


def find_footnote_cut_y(words: Sequence[Dict[str, Any]]) -> Optional[float]:
    """表体之后首次出现「注：」的 y0；无则 None。"""
    rows = _cluster_word_rows(words)
    body_seen = False
    for row in rows:
        if _row_is_note_start(row):
            if body_seen:
                return min(_word_y0(w) for w in row)
            continue
        if _row_is_year_header_band(row):
            # 新年头不算附注；重置后继续找表体
            body_seen = False
            continue
        if _row_has_body_signal(row):
            body_seen = True
    return None


def trim_trailing_footnote_words(
    words: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """丢掉表体后「注：」及以下字框（脚注属表外，不进网格）。"""
    words = [w for w in words if isinstance(w, dict)]
    if not words:
        return []
    cut = find_footnote_cut_y(words)
    if cut is None:
        return list(words)
    kept = [w for w in words if _word_y0(w) < cut - 0.5]
    return kept if len(kept) >= 2 else list(words)


def find_year_header_cut_ys(words: Sequence[Dict[str, Any]]) -> List[float]:
    """在「表体之后再次出现年列表头」处返回切点 y0（后段起点）。"""
    rows = _cluster_word_rows(words)
    cuts: List[float] = []
    body_seen = False
    for row in rows:
        if _row_is_year_header_band(row):
            if body_seen:
                cuts.append(min(_word_y0(w) for w in row))
            body_seen = False
            continue
        if _row_is_note_start(row):
            # 附注打断表体，其后年头视为新表
            body_seen = False
            continue
        if _row_has_body_signal(row):
            body_seen = True
    return cuts


def split_source_words_by_year_bands(
    words: Sequence[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """按重复年列表头把字框切成多段（自上而下）；每段再去掉表尾附注字框。"""
    words = [w for w in words if isinstance(w, dict)]
    if not words:
        return []
    cuts = find_year_header_cut_ys(words)
    if not cuts:
        return [trim_trailing_footnote_words(words)]
    segments: List[List[Dict[str, Any]]] = []
    remaining = list(words)
    for cut_y in cuts:
        left = [w for w in remaining if _word_y0(w) < cut_y - 0.5]
        right = [w for w in remaining if _word_y0(w) >= cut_y - 0.5]
        if left:
            segments.append(trim_trailing_footnote_words(left))
        remaining = right
    if remaining:
        segments.append(trim_trailing_footnote_words(remaining))
    return [s for s in segments if s] or [list(words)]


def _data_label_tokens(data: Sequence[Sequence[Any]]) -> List[str]:
    tokens: List[str] = []
    for row in data or []:
        if not row:
            continue
        for cell in row[:2]:
            t = str(cell or "").strip()
            if not t or len(t) < 2:
                continue
            if is_numeric_data_cell(t) or is_year_cell(t):
                continue
            if _YEAR_RE.search(t) and len(t) <= 12:
                continue
            if _CHANGE_RE.search(t) and len(t) <= 16:
                continue
            if re.search(r"[\u4e00-\u9fff]", t):
                tokens.append(t[:24])
                break
    seen = set()
    out: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def score_words_against_data(
    words: Sequence[Dict[str, Any]],
    data: Sequence[Sequence[Any]],
) -> int:
    blob = " ".join(_word_text(w) for w in words)
    score = 0
    for tok in _data_label_tokens(data):
        if tok and tok in blob:
            score += 2 if len(tok) >= 4 else 1
    return score


def select_source_words_for_data(
    words: Sequence[Dict[str, Any]],
    data: Sequence[Sequence[Any]],
) -> List[Dict[str, Any]]:
    """若字框含多段年报表，选与当前 data 标签最匹配的一段。"""
    words = [w for w in words if isinstance(w, dict)]
    if not words or not data:
        return trim_trailing_footnote_words(words)
    segs = split_source_words_by_year_bands(words)
    if len(segs) <= 1:
        return trim_trailing_footnote_words(words)
    scored = [
        (score_words_against_data(seg, data), i, seg)
        for i, seg in enumerate(segs)
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))
    best_score, _, best = scored[0]
    if best_score <= 0:
        return trim_trailing_footnote_words(words)
    return trim_trailing_footnote_words(best)


def assign_source_words_to_parts(
    words: Sequence[Dict[str, Any]],
    parts: Sequence[Sequence[Sequence[Any]]],
) -> List[List[Dict[str, Any]]]:
    """把整表字框分配给结构拆分后的各段 data。

    自上而下顺序对齐为主（第 i 段 ↔ 第 i 个年头带），标签得分仅作纠偏；
    各段字框互不重复，后段绝不回退成整表字框。
    """
    words = [w for w in words if isinstance(w, dict)]
    n = len(parts)
    if not words:
        return [[] for _ in parts]
    segs = split_source_words_by_year_bands(words)
    if len(segs) <= 1:
        trimmed = trim_trailing_footnote_words(words)
        out: List[List[Dict[str, Any]]] = []
        for i, part in enumerate(parts):
            if i == 0:
                out.append(list(trimmed))
                continue
            picked = select_source_words_for_data(trimmed, part)
            if picked and len(picked) < len(trimmed):
                out.append(picked)
            else:
                # 无法区分时留给空，避免与前段共用整表字框导致重建重复
                out.append([])
        return out

    used: set = set()
    assigned: List[List[Dict[str, Any]]] = [[] for _ in parts]
    for pi, part in enumerate(parts):
        candidates = [
            (score_words_against_data(seg, part), si, seg)
            for si, seg in enumerate(segs)
            if si not in used
        ]
        if not candidates:
            assigned[pi] = []
            continue
        candidates.sort(key=lambda x: (-x[0], x[1]))
        best_sc, best_si, best_seg = candidates[0]
        # 顺序段仍空闲且得分不差于最佳超过 1 → 用顺序段（防交叉）
        if pi < len(segs) and pi not in used:
            ord_sc = score_words_against_data(segs[pi], part)
            if ord_sc >= best_sc - 1:
                best_si, best_seg = pi, segs[pi]
        assigned[pi] = list(best_seg)
        used.add(best_si)
    return assigned


def row_cluster_is_footnote(row: RowCluster) -> bool:
    """附注/脚注行：不参与列槽推断，也不应出现在最终表体。"""
    texts = [str(n.text or "").strip() for n in row.nuclei if str(n.text or "").strip()]
    if not texts:
        return False
    joined = "".join(texts)
    if joined in ("注：", "注:", "注"):
        return True
    if joined.startswith("注：") or joined.startswith("注:"):
        return True
    if _FOOTNOTE_MARK_RE.match(joined) and (
        len(joined) > 20
        or "=" in joined
        or "＝" in joined
        or "／" in joined
        or "/" in joined
    ):
        return True
    from codes.reconstruct.grid_nucleus.preprocess import is_amount_nucleus

    amts = sum(1 for n in row.nuclei if is_amount_nucleus(n))
    if amts <= 1 and len(joined) > 48:
        wide = sum(1 for n in row.nuclei if n.width > 80)
        if wide >= 1:
            return True
    return False


def filter_rows_for_column_infer(rows: Sequence[RowCluster]) -> List[RowCluster]:
    """列槽推断用行：去掉附注，避免长脚注把年列并槽。"""
    kept = [r for r in rows if not row_cluster_is_footnote(r)]
    return kept if kept else list(rows)


def drop_footnote_rows_from_grid(
    rows: Sequence[RowCluster],
    data: List[List[str]],
) -> Tuple[List[RowCluster], List[List[str]]]:
    """从网格结果去掉附注行（注：/(1)…）。"""
    if not data or len(data) != len(rows):
        # 行数不一致时按单元格内容剥尾
        cleaned = strip_footnote_rows_from_data(data)
        return list(rows), cleaned
    keep_idx = [i for i, r in enumerate(rows) if not row_cluster_is_footnote(r)]
    if len(keep_idx) == len(rows) or len(keep_idx) < 2:
        return list(rows), list(data)
    new_rows = [rows[i] for i in keep_idx]
    new_data = [list(data[i]) for i in keep_idx]
    return new_rows, new_data


def strip_footnote_rows_from_data(data: Sequence[Sequence[Any]]) -> List[List[str]]:
    """从 data[][] 去掉表体后的「注：」及后续脚注行。"""
    from codes.table_engine.split.row_classify import is_tail_annotation_row, row_has_body_value_data

    rows = [list(r) for r in (data or [])]
    if len(rows) < 1:
        return rows
    width = max((len(r) for r in rows), default=0)
    last_body = -1
    for i, row in enumerate(rows):
        if row_has_body_value_data(row):
            last_body = i
    if last_body < 0:
        # 无表体：若整段都是注释行，全部剥掉
        if width > 0 and all(
            (not any(str(c).strip() for c in row))
            or is_tail_annotation_row(row, width)
            for row in rows
        ):
            return []
        return rows
    cut = -1
    for i in range(last_body + 1, len(rows)):
        cells = [str(c).strip() for c in rows[i] if str(c).strip()]
        if not cells:
            continue
        first = cells[0]
        if first in ("注：", "注:", "注") or first.startswith("注：") or first.startswith("注:"):
            cut = i
            break
        if is_tail_annotation_row(rows[i], width):
            cut = i
            break
    if cut < 0:
        remove = 0
        for i in range(len(rows) - 1, last_body, -1):
            if is_tail_annotation_row(rows[i], width):
                remove += 1
            else:
                break
        if remove:
            return rows[: len(rows) - remove]
        return rows
    return rows[:cut]


def strip_trailing_next_table_header_rows_from_data(
    data: Sequence[Sequence[Any]],
) -> List[List[str]]:
    """去掉表体后误挂的下一张表表头带 / 下节标题（如合计后的「3.2.3 利息收入」）。"""
    from codes.table_engine.split.row_classify import (
        find_trailing_non_body_start,
        is_inter_table_narrative_row,
        is_prependable_header_band_row,
        row_is_table_tail_section_caption_row,
        trailing_block_is_next_table_header,
    )

    rows = [list(r) for r in (data or [])]
    # 先剥表尾连续节标题 / 下一节字段名 / 叙述
    while len(rows) > 1 and (
        row_is_table_tail_section_caption_row(rows[-1])
        or is_inter_table_narrative_row(rows[-1])
    ):
        rows.pop()
    start = find_trailing_non_body_start(rows)
    if start is None or start >= len(rows):
        return rows
    tail = rows[start:]
    if trailing_block_is_next_table_header(tail):
        return rows[:start]
    # 表下节标题 / 叙述
    if tail and all(
        (not [str(c).strip() for c in row if str(c).strip()])
        or row_is_table_tail_section_caption_row(row)
        or is_inter_table_narrative_row(row)
        for row in tail
    ):
        if any(
            row_is_table_tail_section_caption_row(row)
            or is_inter_table_narrative_row(row)
            for row in tail
        ):
            return rows[:start]
    # 宽松：尾部全是可并入下表的表头/空行，且至少一行像表头
    saw_header = False
    for row in tail:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if not cells:
            continue
        if is_inter_table_narrative_row(row):
            continue
        if row_is_table_tail_section_caption_row(row):
            saw_header = True
            continue
        if is_prependable_header_band_row(row):
            saw_header = True
            continue
        return rows
    if saw_header:
        return rows[:start]
    return rows


def _row_joined_text(row: Sequence[Any]) -> str:
    return "".join(str(c).strip() for c in row if str(c).strip())


def row_looks_like_leading_page_chrome(row: Sequence[Any]) -> bool:
    """表顶行是否像页眉/章节 running header（非表体）。"""
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells:
        return True
    joined = "".join(cells)
    try:
        from codes.table_engine.scope.page_chrome import text_looks_like_page_chrome

        if text_looks_like_page_chrome(joined, role_hint="page_header"):
            return True
        if text_looks_like_page_chrome(" ".join(cells), role_hint="page_header"):
            return True
    except Exception:
        if any(k in joined for k in ("股份有限公司", "有限公司", "年度报告", "半年度报告")):
            return True
    # 章节碎片落进多列（公司名|第二章|摘要）
    if any(k in joined for k in ("会计数据", "财务指标摘要", "重要提示", "释义")):
        # 若同行已有年/金额，更像表头，勿剥
        if any(
            re.search(r"20\d{2}\s*年", c) or re.search(r"^\d+(\.\d+)?$", c)
            for c in cells
        ):
            return False
        return True
    if joined.startswith(("第", "附件", "目录")) and len(joined) <= 40:
        if not any(re.search(r"20\d{2}\s*年", c) for c in cells):
            return True
    return False


def row_looks_like_leading_non_table(row: Sequence[Any]) -> bool:
    """表顶行是否应剥离：页眉 / 表前说明 / 叙述残句。"""
    if row_looks_like_leading_page_chrome(row):
        return True
    cells = [str(c or "").strip() for c in (row or []) if str(c or "").strip()]
    joined = "".join(cells)
    if any(m in joined for m in ("如下：", "如下:", "指标如下", "下表列", "具体如下", "本集团根据")):
        # 勿剥真正表头
        if not any(c in ("序号", "指标", "指标值", "项目") for c in cells):
            return True
    if any(m in joined for m in ("未经审计", "财务报表补充资料", "止年度", "评估指标披露")):
        if not any(c in ("序号", "指标", "指标值", "项目") for c in cells):
            return True
    try:
        from codes.table_engine.split.row_classify import is_inter_table_narrative_row

        return is_inter_table_narrative_row(list(row))
    except Exception:
        return False


def strip_leading_page_chrome_rows_from_data(
    data: Sequence[Sequence[Any]],
) -> List[List[str]]:
    """去掉表顶误入的页眉行；至少保留一行，避免掏空。"""
    return strip_leading_non_table_rows_from_data(data)


def strip_leading_non_table_rows_from_data(
    data: Sequence[Sequence[Any]],
) -> List[List[str]]:
    """去掉表顶误入的页眉/叙述/「下表列出…」说明；至少保留一行。"""
    rows = [list(r) for r in (data or [])]
    if len(rows) < 2:
        return rows
    i = 0
    while i < len(rows) - 1 and row_looks_like_leading_non_table(rows[i]):
        i += 1
    if i == 0:
        return rows
    kept = rows[i:]
    if not any(_row_joined_text(r) for r in kept):
        return rows
    return kept


def trim_leading_page_chrome_words(
    words: Sequence[Dict[str, Any]],
    *,
    page_height: float = 842.0,
) -> List[Dict[str, Any]]:
    """从字框列表去掉页眉带内的 running header。

    注意：不得把表内金额整数（如 75）当成页码剥掉——text_looks_like_page_chrome
    会把纯数字判为页码，导致「75」+「.21」只剩「.21」再空格拼成「75 .21」类残缺。
    """
    from codes.table_engine.scope.page_chrome import (
        header_band_y1,
        text_looks_like_page_chrome,
    )

    band = header_band_y1(page_height) + 10.0
    kept: List[Dict[str, Any]] = []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        text = str(w.get("text") or "").strip()
        try:
            y0 = float(w.get("y0") or 0)
        except (TypeError, ValueError):
            y0 = 0.0
        if y0 <= band and text:
            # 纯数字 / 小数后缀：表体金额，绝非页眉
            if re.match(r"^\d{1,6}$", text) or re.match(r"^\.\d+\D?$", text):
                kept.append(w)
                continue
            if text_looks_like_page_chrome(text, role_hint="page_header"):
                # 允许「15页」「第15页」，禁止裸「15」
                if re.match(r"^\d{1,4}$", text):
                    kept.append(w)
                    continue
                continue
            if text.startswith(("第", "附件", "目录", "章节")) and len(text) <= 40:
                continue
            if any(k in text for k in ("会计数据", "财务指标摘要", "重要提示", "释义")):
                continue
            # 报表页眉残句（在表体之上时由 narrative 再剥一层；此处兜底）
            if any(
                k in text
                for k in (
                    "未经审计",
                    "财务报表补充资料",
                    "止年度",
                    "评估指标披露",
                )
            ):
                continue
        kept.append(w)
    return kept if kept else list(words or [])


def _word_text_is_table_intro_or_prose(text: str) -> bool:
    """字框文案是否像表前说明/叙述残句（非列头/金额）。"""
    t = str(text or "").strip()
    if not t or len(t) < 4:
        return False
    if re.match(r"^\(?%\)?$", t) or re.match(r"^20\d{2}\s*年", t):
        return False
    if re.match(r"^-?\d+(\.\d+)?%?$", t.replace(",", "")):
        return False
    # 表前引导句（含折行残句「制的…如下：」）
    if any(
        m in t
        for m in (
            "下表列出",
            "下表列",
            "具体如下",
            "如下：",
            "如下:",
            "指标如下",
            "披露如下",
            "有关要求",
            "最新规定",
        )
    ):
        return True
    if t.endswith(("如下", "如下：", "如下:")):
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", t))
    # 长叙述以冒号/句号收尾：表外说明，不是表内科目折行
    if t.endswith(("。", "；", "！", "：", ":")) and cn >= 8:
        return True
    if t.endswith(("。", "；")) and cn >= 2 and re.search(r"\d", t):
        if any(m in t for m in ("占比", "%", "％", "较上年", "同比", "亿元", "万元")):
            return True
    return False


def _word_y0(w: Dict[str, Any]) -> float:
    try:
        return float(w.get("y0") or 0)
    except (TypeError, ValueError):
        return 0.0


def _word_x0(w: Dict[str, Any]) -> float:
    try:
        return float(w.get("x0") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_strong_table_start_text(text: str) -> bool:
    """阅读序上的表起点强标记（列头/单位行）。"""
    t = str(text or "").strip().replace(" ", "")
    if not t:
        return False
    if t in {
        "序号", "指标", "指标值", "项目", "名称", "金额",
        "期末余额", "期初余额",
    }:
        return True
    if t.startswith("单位：") or t.startswith("单位:"):
        return True
    return False


def _is_soft_table_start_text(text: str) -> bool:
    """次强起点：年头 / (%) —— 无序号表时用。"""
    t = str(text or "").strip()
    if re.search(r"20\d{2}\s*年", t) and len(t) <= 24:
        return True
    return t in ("(%)", "%", "（%）")


_PERIOD_BUCKET_WORD_RE = re.compile(
    r"^(?:无期限|< ?6个?月|6[-–—]?12个?月|≥ ?1年|≧ ?1年|"
    r"实时偿还|即期|过夜|≤ ?3个?月|≤ ?1年)$"
)


def _is_table_header_continuation_text(text: str) -> bool:
    """表头折行上一行（本年比上年 / 单位说明 / 列码 a / 期限分档），非表外叙述。"""
    t = str(text or "").strip()
    if not t or _word_text_is_table_intro_or_prose(t):
        return False
    if any(k in t for k in ("未经审计", "如下", "本集团根据", "评估指标披露", "补充资料")):
        return False
    if _is_strong_table_start_text(t) or _is_soft_table_start_text(t):
        return True
    # 列码 a/b/c
    if re.fullmatch(r"[a-zA-Z]{1,3}", t):
        return True
    # 期限分档（≥1年 仅 1 个汉字，不能靠 cn 门槛）
    if _PERIOD_BUCKET_WORD_RE.match(t.replace(" ", "")):
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", t))
    if "百万元" in t or "除外" in t or "特别注明" in t:
        return True
    if "折算" in t and cn <= 8:
        return True
    return 2 <= cn <= 16 and len(t) <= 28


def _table_body_anchor_y(words: Sequence[Dict[str, Any]]) -> Optional[float]:
    """表体起点 y（兼容旧调用）：强/次强表头标记的最小 y。"""
    items = [w for w in (words or []) if isinstance(w, dict)]
    ys: List[float] = []
    for w in items:
        t = str(w.get("text") or "").strip()
        if _is_strong_table_start_text(t) or _is_soft_table_start_text(t):
            ys.append(_word_y0(w))
    return min(ys) if ys else None


def trim_leading_narrative_words(
    words: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """按阅读序硬切：表起点之前的字一律不进表。

    顺序：从上到下、从左到右找到首个表头标记（序号/指标/单位/年头），
    再向上收同带短表头折行；其前的页眉/叙述全部丢掉。
    不得把表外字事后并进表内（根除混入、改写表内数据）。
    """
    items = [w for w in (words or []) if isinstance(w, dict) and str(w.get("text") or "").strip()]
    if len(items) < 3:
        return list(words or [])

    ordered = sorted(items, key=lambda w: (_word_y0(w), _word_x0(w)))
    start_idx: Optional[int] = None
    for i, w in enumerate(ordered):
        t = str(w.get("text") or "").strip()
        if _is_strong_table_start_text(t):
            start_idx = i
            break
    if start_idx is None:
        for i, w in enumerate(ordered):
            t = str(w.get("text") or "").strip()
            if _is_soft_table_start_text(t):
                start_idx = i
                break
    if start_idx is None:
        return list(words or [])

    j = start_idx
    start_y = _word_y0(ordered[start_idx])
    while j > 0:
        prev = ordered[j - 1]
        pt = str(prev.get("text") or "").strip()
        if _word_text_is_table_intro_or_prose(pt):
            break
        if start_y - _word_y0(prev) > 30.0:
            break
        # 同带短表头才回挂；通栏长句不收
        try:
            width = float(prev.get("x1", 0)) - float(prev.get("x0", 0))
        except (TypeError, ValueError):
            width = 0.0
        cn = len(re.findall(r"[\u4e00-\u9fff]", pt))
        if width >= 280 and cn >= 10:
            break
        if not _is_table_header_continuation_text(pt):
            break
        j -= 1
        start_y = min(start_y, _word_y0(prev))

    # 保持相对稳定：按原列表过滤，而不是只返回排序副本
    keep_ids = {id(w) for w in ordered[j:]}
    kept = [w for w in items if id(w) in keep_ids]
    return kept if kept else list(words or [])


_NEXT_HEADER_WORD_RE = re.compile(
    r"^(平均|平均余额|利息收入|利息支出|收益率%?|成本率%?|本集团|本行)$"
)
# 节号：3.2.3 / 碎片 .3（表下标题被拆时）
_SECTION_NUM_WORD_RE = re.compile(r"^(?:\d+\.){2,}\d+$|^\.\d+$")


def _word_looks_like_amount_token(text: str) -> bool:
    t = str(text or "").strip().replace(",", "").replace("，", "")
    if not t:
        return False
    # 节号碎片不算表体金额，避免 last_amt_y1 被表下「.3」拉高
    if _SECTION_NUM_WORD_RE.match(t):
        return False
    return bool(re.match(r"^-?\d+(\.\d+)?%?$", t))


def _word_looks_like_post_table_field_caption(text: str) -> bool:
    """表下字段名：同业存拆放及其他利息支出（纯中文、无表体金额）。"""
    t = str(text or "").strip()
    if not t or _word_looks_like_amount_token(t):
        return False
    if re.fullmatch(r"(?:小计|合计|总计|净额)", t):
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", t))
    if cn < 6:
        return False
    # 排除带大额数字的叙述；节号已由 _SECTION_NUM_WORD_RE 处理
    if re.search(r"\d{3,}", t.replace(",", "").replace("，", "")):
        return False
    return True


def trim_trailing_next_header_words(
    words: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """去掉表体金额下方误挂的下一表表头 / 下节标题字框（如「平均」「.3」「利息收入」）。"""
    items = [w for w in (words or []) if isinstance(w, dict)]
    if len(items) < 4:
        return list(words or [])

    def _y1(w: Dict[str, Any]) -> float:
        try:
            return float(w.get("y1") or w.get("y0") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _y0(w: Dict[str, Any]) -> float:
        try:
            return float(w.get("y0") or 0)
        except (TypeError, ValueError):
            return 0.0

    last_amt_y1 = None
    for w in items:
        if _word_looks_like_amount_token(str(w.get("text") or "")):
            y1 = _y1(w)
            if last_amt_y1 is None or y1 > last_amt_y1:
                last_amt_y1 = y1
    if last_amt_y1 is None:
        return list(words or [])

    kept: List[Dict[str, Any]] = []
    for w in items:
        t = str(w.get("text") or "").strip()
        if _y0(w) > last_amt_y1 + 2.0 and (
            _NEXT_HEADER_WORD_RE.match(t)
            or _SECTION_NUM_WORD_RE.match(t)
            or bool(re.match(r"^(?:\d+\.){2,}\d+\s*[\u4e00-\u9fff]", t))
            or _word_looks_like_post_table_field_caption(t)
            or ("人民币" in t and "除外" in t)
            or t in ("（人民币百万元，百分比除外）", "(人民币百万元，百分比除外)")
        ):
            continue
        kept.append(w)
    return kept if kept else list(words or [])