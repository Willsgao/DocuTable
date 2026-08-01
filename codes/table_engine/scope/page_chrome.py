# -*- coding: utf-8 -*-
"""页眉/页脚识别：从 liteparse text_items 按位置+文案拆出独立 TEXT 块。"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.models import PageSource, SourceItem, TextBlock

# 页顶/页底带宽（pt）；与 page_height 比例取较宽者（勿过大，避免吞章节标题）
_HEADER_BAND_PT = 52.0
_FOOTER_BAND_PT = 50.0
_HEADER_BAND_RATIO = 0.065
_FOOTER_BAND_RATIO = 0.07

# 紧贴页眉下方的章节/表题，不得并入页眉
_SECTION_OPENER_MARKERS = (
    "财务报表附注",
    "合并资产负债表",
    "合并利润表",
    "合并现金流量表",
    "合并及银行",
    "合并股东权益",
    "公司资产负债表",
    "公司利润表",
    "现金流量表",
    "资产负债表",
    "利润表",
    "股东权益变动表",
    "重要提示",
    "释义",
    "目录",
    "除特别注明",
    "以人民币",
)

_PAGE_NUM_RE = re.compile(
    r"^(?:第\s*)?\d{1,4}\s*页?$|^\d{1,4}\s*/\s*\d{1,4}$"
)
_CHROME_MARKERS = (
    "年度报告",
    "半年度报告",
    "第三季度报告",
    "第一季度报告",
    "年报",
    "股份有限公司",
    "有限公司",
    "第三支柱",
    "信息披露报告",
    "信息披露",
)
# 页底常见声明（非表体）
_FOOTER_MARKERS = (
    "财务报表附注为",
    "构成本财务报表",
    "为本财务报表的组成部分",
    "本财务报表的组成部分",
    "本页无正文",
    "接下页",
    "（续）",
    "(续)",
)


def header_band_y1(page_height: float) -> float:
    h = float(page_height or 800.0)
    return max(_HEADER_BAND_PT, h * _HEADER_BAND_RATIO)


def footer_band_y0(page_height: float) -> float:
    h = float(page_height or 800.0)
    return h - max(_FOOTER_BAND_PT, h * _FOOTER_BAND_RATIO)


def is_page_chrome_role(role: Optional[str]) -> bool:
    return role in ("page_header", "page_footer")


def text_looks_like_page_chrome(text: str, *, role_hint: Optional[str] = None) -> bool:
    """文案是否像 running header/footer（不含章节正文「财务报表附注」单行标题）。"""
    s = str(text or "").strip()
    if not s:
        return False
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if not lines or len(lines) > 5:
        return False
    joined = "".join(lines)
    # 表题/章节 opener 优先排除（「合并及银行现金流量表」含「银行」但不是页眉）
    if any(m in joined for m in _SECTION_OPENER_MARKERS):
        if role_hint == "page_footer" and any(m in joined for m in _FOOTER_MARKERS):
            return True
        return False
    # 纯页码
    if all(_PAGE_NUM_RE.match(ln) for ln in lines):
        return True
    if any(m in joined for m in _CHROME_MARKERS):
        return True
    if role_hint == "page_footer" and any(m in joined for m in _FOOTER_MARKERS):
        return True
    # 极短顶栏：必须像公司名/报告名，禁止仅因含「银行」误伤正文
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    if cn <= 20 and len(joined) <= 36 and "。" not in joined and "，" not in joined:
        if any(k in joined for k in ("股份有限公司", "有限公司", "年度报告", "半年度报告")):
            return True
    return False


def items_look_like_page_chrome(
    items: Sequence[SourceItem],
    *,
    page_height: float,
    role_hint: Optional[str] = None,
) -> bool:
    if not items:
        return False
    y0 = min(it.bbox.y0 for it in items)
    y1 = max(it.bbox.y1 for it in items)
    text = _items_to_text(items)
    in_header = y1 <= header_band_y1(page_height) + 12
    in_footer = y0 >= footer_band_y0(page_height) - 12
    if role_hint == "page_header" or in_header:
        if in_header:
            # 顶带：页码行可单独成立；其余必须文案像 running header
            if all(
                _PAGE_NUM_RE.match(str(it.text or "").strip())
                for it in items
                if str(it.text or "").strip()
            ):
                return True
            return text_looks_like_page_chrome(text, role_hint="page_header")
    if role_hint == "page_footer" or in_footer:
        if in_footer:
            return text_looks_like_page_chrome(
                text, role_hint="page_footer"
            ) and not _looks_like_table_tail(text)
    # 非顶/底带：绝不靠文案单独判为页眉页脚（避免正文含「银行」误伤）
    return False


def _looks_like_table_tail(text: str) -> bool:
    """页底若像金额/日期表尾行，勿当页脚。"""
    t = str(text or "")
    if re.search(r"\d{1,3}(?:,\d{3})+", t):
        return True
    if re.search(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", t):
        return True
    return False


def _items_to_text(items: Sequence[SourceItem]) -> str:
    if not items:
        return ""
    dicts = source_items_to_dicts(list(items))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    parts: List[str] = []
    for row in rows:
        line = " ".join(
            str(it.get("text", "")).strip()
            for it in sorted(row.get("items", []), key=lambda d: d.get("x0", 0))
            if str(it.get("text", "")).strip()
        )
        if line:
            parts.append(line)
    return "\n".join(parts)


def _make_chrome_block(
    page_num: int,
    items: Sequence[SourceItem],
    role: str,
) -> TextBlock:
    return TextBlock(
        page=page_num,
        y0=min(it.bbox.y0 for it in items),
        y1=max(it.bbox.y1 for it in items),
        text=_items_to_text(items),
        source_items=[it.item_index for it in items],
        role=role,
    )


def _cluster_rows(items: Sequence[SourceItem]) -> List[List[SourceItem]]:
    if not items:
        return []
    dicts = source_items_to_dicts(list(items))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in items}
    out: List[List[SourceItem]] = []
    for row in rows:
        row_items: List[SourceItem] = []
        for d in row.get("items", []):
            idx = str(d.get("item_index", ""))
            src = index_map.get(idx)
            if src is not None:
                row_items.append(src)
        if row_items:
            out.append(row_items)
    return out


def _is_section_opener_text(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    # 页脚声明常含「财务报表附注为…组成部分」，不是章节 opener
    if any(m in t for m in _FOOTER_MARKERS):
        return False
    # 页眉里的「年度报告」不是章节 opener
    if any(m in t for m in ("年度报告", "半年度报告", "股份有限公司")) and len(t) <= 40:
        if not any(m in t for m in _SECTION_OPENER_MARKERS):
            return False
    return any(m in t for m in _SECTION_OPENER_MARKERS)


def extract_page_chrome(
    page: PageSource,
) -> Tuple[List[TextBlock], Set[str]]:
    """从整页 items 抽出页眉/页脚 TextBlock，并返回占用的 source item id。"""
    height = float(page.page_height or 800.0)
    hy1 = header_band_y1(height)
    fy0 = footer_band_y0(height)

    # 稍放宽收集，再按「顶行连续 chrome」截断，避免吞表题/附注标题
    header_items = [it for it in page.items if it.bbox.y0 <= hy1 + 18]
    footer_items = [it for it in page.items if it.bbox.y0 >= fy0 - 2]

    blocks: List[TextBlock] = []
    used: Set[str] = set()

    header_keep: List[SourceItem] = []
    for row in _cluster_rows(header_items):
        row_text = _items_to_text(row)
        if _is_section_opener_text(row_text):
            break
        row_y1 = max(it.bbox.y1 for it in row)
        if row_y1 > hy1 + 10 and header_keep:
            # 已有页眉行后，再往下超出带宽 → 停止
            break
        if (
            items_look_like_page_chrome(
                row, page_height=height, role_hint="page_header"
            )
            or all(
                _PAGE_NUM_RE.match(str(it.text or "").strip())
                or any(m in str(it.text or "") for m in _CHROME_MARKERS)
                or len(str(it.text or "").strip()) <= 6
                for it in row
            )
        ):
            header_keep.extend(row)
            continue
        if header_keep:
            break

    if header_keep and items_look_like_page_chrome(
        header_keep, page_height=height, role_hint="page_header"
    ):
        block = _make_chrome_block(page.page_number, header_keep, "page_header")
        if block.text.strip() and not _is_section_opener_text(block.text):
            blocks.append(block)
            used.update(str(i) for i in block.source_items)

    footer_keep: List[SourceItem] = []
    for row in _cluster_rows(footer_items):
        if items_look_like_page_chrome(
            row, page_height=height, role_hint="page_footer"
        ):
            footer_keep.extend(row)
    if footer_keep:
        block = _make_chrome_block(page.page_number, footer_keep, "page_footer")
        if block.text.strip() and not _looks_like_table_tail(block.text):
            blocks.append(block)
            used.update(str(i) for i in block.source_items)

    blocks.sort(key=lambda b: (b.y0, b.y1))
    return blocks, used


def filter_items_without_chrome(
    items: Iterable[SourceItem],
    chrome_ids: Set[str],
) -> List[SourceItem]:
    if not chrome_ids:
        return list(items)
    return [it for it in items if str(it.item_index) not in chrome_ids]


def _sanitize_chrome_block(block: TextBlock) -> List[TextBlock]:
    """已标记的页眉块若混入章节/表题，拆回 chrome + 正文。"""
    if block.role not in ("page_header", "page_footer"):
        return [block]
    lines = [ln.strip() for ln in str(block.text or "").splitlines() if ln.strip()]
    if not lines:
        return []
    if block.role == "page_footer":
        return [block]
    chrome_lines: List[str] = []
    rest: List[str] = []
    for ln in lines:
        if _is_section_opener_text(ln):
            rest.append(ln)
            rest.extend(lines[lines.index(ln) + 1 :])
            break
        if text_looks_like_page_chrome(ln, role_hint="page_header") or _PAGE_NUM_RE.match(ln):
            chrome_lines.append(ln)
        else:
            rest.append(ln)
            rest.extend(lines[lines.index(ln) + 1 :])
            break
    out: List[TextBlock] = []
    span = max(block.y1 - block.y0, 1.0)
    if chrome_lines:
        ratio = len(chrome_lines) / max(len(lines), 1)
        out.append(
            TextBlock(
                page=block.page,
                y0=block.y0,
                y1=block.y0 + span * ratio if rest else block.y1,
                text="\n".join(chrome_lines),
                source_items=list(block.source_items or []),
                role="page_header",
            )
        )
    if rest:
        out.append(
            TextBlock(
                page=block.page,
                y0=out[-1].y1 if out else block.y0,
                y1=block.y1,
                text="\n".join(rest),
                source_items=list(block.source_items or []),
                role=None,
            )
        )
    return out or [block]


def peel_chrome_from_text_block(
    block: TextBlock,
    *,
    page_height: float,
) -> List[TextBlock]:
    """若普通 TEXT 混入页眉/页脚行，拆成 role 标记块 + 正文块。"""
    if block.role in ("page_header", "page_footer"):
        return _sanitize_chrome_block(block)
    text = str(block.text or "").strip()
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        if block.y1 <= header_band_y1(page_height) + 8 and text_looks_like_page_chrome(
            text, role_hint="page_header"
        ):
            out = TextBlock(
                page=block.page,
                y0=block.y0,
                y1=block.y1,
                text=text,
                source_items=list(block.source_items or []),
                role="page_header",
            )
            return [out]
        if block.y0 >= footer_band_y0(page_height) - 8 and text_looks_like_page_chrome(
            text, role_hint="page_footer"
        ):
            out = TextBlock(
                page=block.page,
                y0=block.y0,
                y1=block.y1,
                text=text,
                source_items=list(block.source_items or []),
                role="page_footer",
            )
            return [out]
        return [block]

    header_lines: List[str] = []
    body_lines: List[str] = []
    footer_lines: List[str] = []
    # 顶部连续 chrome 行 → 页眉；底部连续 → 页脚
    i = 0
    while i < len(lines) and text_looks_like_page_chrome(
        lines[i], role_hint="page_header"
    ):
        # 章节标题「财务报表附注」单独一行且后面还有正文时，留给正文
        if lines[i] in ("财务报表附注", "附注") and i + 1 < len(lines):
            break
        header_lines.append(lines[i])
        i += 1
    j = len(lines)
    while j > i and text_looks_like_page_chrome(
        lines[j - 1], role_hint="page_footer"
    ):
        footer_lines.insert(0, lines[j - 1])
        j -= 1
    body_lines = lines[i:j]

    # 仅当位置也落在顶/底带时才拆（避免误伤正文里的「股份有限公司」）
    span = max(block.y1 - block.y0, 1.0)
    results: List[TextBlock] = []
    if header_lines and block.y0 <= header_band_y1(page_height) + 20:
        h_ratio = len(header_lines) / max(len(lines), 1)
        results.append(
            TextBlock(
                page=block.page,
                y0=block.y0,
                y1=block.y0 + span * h_ratio,
                text="\n".join(header_lines),
                source_items=list(block.source_items or []),
                role="page_header",
            )
        )
    else:
        body_lines = header_lines + body_lines
        header_lines = []

    if footer_lines and block.y1 >= footer_band_y0(page_height) - 20:
        f_ratio = len(footer_lines) / max(len(lines), 1)
        # body 先写
        pass
    else:
        body_lines = body_lines + footer_lines
        footer_lines = []

    if body_lines:
        b0 = block.y0 + span * (len(header_lines) / max(len(lines), 1))
        b1 = block.y1 - span * (len(footer_lines) / max(len(lines), 1))
        results.append(
            TextBlock(
                page=block.page,
                y0=b0,
                y1=max(b0 + 1.0, b1),
                text="\n".join(body_lines),
                source_items=list(block.source_items or []),
                role=None,
            )
        )
    if footer_lines and block.y1 >= footer_band_y0(page_height) - 20:
        f_ratio = len(footer_lines) / max(len(lines), 1)
        results.append(
            TextBlock(
                page=block.page,
                y0=block.y1 - span * f_ratio,
                y1=block.y1,
                text="\n".join(footer_lines),
                source_items=list(block.source_items or []),
                role="page_footer",
            )
        )
    return results or [block]


def ensure_page_chrome_separated(
    page: PageSource,
    gap_texts: List[TextBlock],
) -> List[TextBlock]:
    """保证页眉/页脚以独立 role 块存在，并从混排 TEXT 中剥离。"""
    height = float(page.page_height or 800.0)
    chrome_blocks, chrome_ids = extract_page_chrome(page)

    peeled: List[TextBlock] = []
    for block in gap_texts:
        # 已占用 chrome 的 source 从普通块去掉文案重复
        if chrome_ids and block.source_items:
            src = [s for s in block.source_items if str(s) not in chrome_ids]
            if not src and block.role not in ("page_header", "page_footer"):
                # 整块已被 chrome 覆盖
                continue
            if len(src) != len(block.source_items):
                # 按行再 peel
                parts = peel_chrome_from_text_block(block, page_height=height)
                for p in parts:
                    if p.role in ("page_header", "page_footer"):
                        continue  # 用 extract 的权威块
                    if p.text.strip():
                        peeled.append(p)
                continue
        parts = peel_chrome_from_text_block(block, page_height=height)
        peeled.extend(parts)

    # 合并：权威 chrome 优先；同 role 只保留最短干净块（去重「页眉+表题」污染块）
    headers = [b for b in chrome_blocks + peeled if b.role == "page_header"]
    footers = [b for b in chrome_blocks + peeled if b.role == "page_footer"]
    bodies = [b for b in chrome_blocks + peeled if b.role not in ("page_header", "page_footer")]

    out: List[TextBlock] = []
    hy1 = header_band_y1(height)
    fy0 = footer_band_y0(height)
    if headers:
        # 只保留落在页顶带的；优先含报告名/公司名的干净块
        headers = [
            b for b in headers
            if b.y1 <= hy1 + 20 and not _is_section_opener_text(b.text)
        ]
        if headers:
            def _header_rank(b: TextBlock) -> tuple:
                t = b.text or ""
                score = 0
                if "年度报告" in t or "半年度报告" in t:
                    score -= 10
                if "股份有限公司" in t or "有限公司" in t:
                    score -= 5
                if any(
                    _PAGE_NUM_RE.match(ln.strip())
                    for ln in t.splitlines()
                    if ln.strip()
                ):
                    score -= 3
                return (score, b.y0, len(t))
            out.append(min(headers, key=_header_rank))
    for b in footers:
        if b.y0 < fy0 - 20:
            continue
        if _looks_like_table_tail(b.text) or _is_section_opener_text(b.text):
            continue
        if not text_looks_like_page_chrome(b.text, role_hint="page_footer"):
            continue
        key = _norm_key(b.text)
        if any(_norm_key(x.text) == key for x in out if x.role == "page_footer"):
            continue
        out.append(b)
    for b in bodies:
        if not b.text.strip():
            continue
        # 正文若与已选页眉全文重复则丢
        if out and b.role is None and _norm_key(b.text) == _norm_key(out[0].text):
            continue
        out.append(b)
    out.sort(key=lambda b: (b.y0, 0 if b.role else 1, b.y1))
    return out


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def apply_page_chrome_to_entries(
    entries: List,
    page: PageSource,
) -> List:
    """对已生成的 DocumentEntry：拆出页眉页脚 TEXT，避免与正文粘连。"""
    from codes.table_engine.models import DocumentEntry

    height = float(page.page_height or 800.0)
    chrome_blocks, _chrome_ids = extract_page_chrome(page)

    text_blocks: List[TextBlock] = []
    tables: List[DocumentEntry] = []
    for entry in entries:
        if entry.kind == "text" and entry.text_block is not None:
            text_blocks.extend(
                peel_chrome_from_text_block(entry.text_block, page_height=height)
            )
        else:
            tables.append(entry)

    text_blocks = ensure_page_chrome_separated(page, chrome_blocks + text_blocks)
    out: List[DocumentEntry] = []
    for block in text_blocks:
        out.append(
            DocumentEntry(
                kind="text",
                page=page.page_number,
                y0=block.y0,
                y1=block.y1,
                text_block=block,
                entry_id=0,
            )
        )
    out.extend(tables)
    out.sort(
        key=lambda e: (
            e.page,
            e.y0,
            0 if e.kind == "text" else 1,
            e.y1,
        )
    )
    for i, e in enumerate(out):
        e.entry_id = i
    return out
