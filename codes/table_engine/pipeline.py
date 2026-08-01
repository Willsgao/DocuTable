# -*- coding: utf-8
"""Table Engine 全文档 pipeline。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from codes.table_engine.config import DEFAULT_PILLAR_CACHE, TableEngineConfig, default_config
from codes.table_engine.models import BuildReport, Document, DocumentEntry, PageSource, StructuredTable, TextBlock
from codes.table_engine.conservation.item_conservation import apply_item_conservation
from codes.table_engine.scope.gap_capture import _collect_gap_items, _split_description_and_narrative, plan_page_scopes
from codes.table_engine.scope.page_chrome import (
    apply_page_chrome_to_entries,
    ensure_page_chrome_separated,
    extract_page_chrome,
)
from codes.table_engine.source.liteparse_loader import load_liteparse_document
from codes.table_engine.split.content_partition import (
    apply_content_partition,
    description_already_in_table,
    filter_description_captions,
)
from codes.table_engine.split.footnote_strip import apply_footnote_strip
from codes.table_engine.split.fragment_rejoin import apply_fragment_rejoin
from codes.table_engine.split.grid_prune import apply_grid_prune
from codes.table_engine.split.header_audit import apply_header_audit
from codes.table_engine.split.structure_split import (
    apply_adjacent_table_boundary_repair,
    apply_cell_decomposition,
    apply_structure_split,
    apply_sibling_compound_header_repair,
    dedupe_subset_overlapping_tables,
)
from codes.table_engine.scope.header_supplement import (
    supplement_scope_missing_body_above,
    supplement_scope_missing_body_below,
    supplement_scope_missing_headers,
    supplement_scope_missing_intra_label_rows,
)
from codes.table_engine.split.leading_header_reattach import apply_leading_header_reattach
from codes.table_engine.split.trailing_header_reattach import apply_trailing_header_reattach
from codes.table_engine.split.table_text_split import build_page_entries, count_entries
from codes.table_engine.split.y_calibrate import apply_y_calibration
from codes.table_engine.table_builder import build_table_from_scope
@dataclass
class PageBuildResult:
    page_number: int
    tables: List[StructuredTable] = field(default_factory=list)
    gap_texts: List[TextBlock] = field(default_factory=list)
    entries: List[DocumentEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _reading_order_key(entry: DocumentEntry) -> tuple:
    suffix = str(entry.table.metadata.get("split_suffix", "")) if entry.table else ""
    sub = 1 if suffix == "B" else 0
    kind_order = 0 if entry.kind == "text" else 1
    return (entry.page, entry.y0, sub, kind_order, entry.y1, entry.entry_id)


def _renumber_entries(entries: List[DocumentEntry]) -> None:
    for i, entry in enumerate(entries):
        entry.entry_id = i


def _sort_entries(entries: List[DocumentEntry]) -> List[DocumentEntry]:
    entries.sort(key=_reading_order_key)
    _renumber_entries(entries)
    return entries


def _dedupe_overlapping_text_entries(entries: List[DocumentEntry]) -> List[DocumentEntry]:
    """合并重复 TEXT（同页同文，如 gap 说明与表前 peel 重复）。"""
    out: List[DocumentEntry] = []
    for entry in entries:
        if entry.kind != "text" or entry.text_block is None:
            out.append(entry)
            continue
        text = entry.text_block.text.strip()
        if not text:
            out.append(entry)
            continue
        duplicate = False
        for kept in out:
            if kept.kind != "text" or kept.text_block is None:
                continue
            if kept.page != entry.page:
                continue
            kt = kept.text_block.text.strip()
            if kt == text or (len(text) >= 10 and text in kt):
                if abs(kept.y0 - entry.y0) <= 24:
                    duplicate = True
                    break
        if not duplicate:
            out.append(entry)
    return out


def _build_pure_text_page(page: PageSource) -> List[DocumentEntry]:
    """非 table 页：页眉/页脚独立块 + 正文。"""
    chrome_blocks, chrome_ids = extract_page_chrome(page)
    gap_items = _collect_gap_items(
        page, 65.0, page.page_height or 800.0, exclude_item_ids=chrome_ids,
    )
    if not gap_items:
        gap_items = [
            it for it in page.items
            if it.bbox.y0 > 65.0 and str(it.item_index) not in chrome_ids
        ]
    _, _, narrative = _split_description_and_narrative(gap_items)
    if not narrative:
        narrative = gap_items
    blocks = list(chrome_blocks)
    if narrative:
        text = "\n".join(
            line for line in (
                " ".join(
                    it.text.strip()
                    for it in sorted(
                        [x for x in narrative if abs(x.y_mid - y) < 4],
                        key=lambda z: z.x0,
                    )
                    if it.text.strip()
                )
                for y in sorted({round(it.y_mid, 1) for it in narrative})
            )
            if line.strip()
        )
        if text.strip():
            y0 = min(it.bbox.y0 for it in narrative)
            y1 = max(it.bbox.y1 for it in narrative)
            blocks.append(
                TextBlock(
                    page=page.page_number,
                    y0=y0,
                    y1=y1,
                    text=text,
                    source_items=[it.item_index for it in narrative],
                )
            )
    blocks = ensure_page_chrome_separated(page, blocks)
    entries: List[DocumentEntry] = []
    for i, block in enumerate(sorted(blocks, key=lambda b: (b.y0, b.y1))):
        if not block.text.strip():
            continue
        entries.append(
            DocumentEntry(
                kind="text",
                page=page.page_number,
                y0=block.y0,
                y1=block.y1,
                text_block=block,
                entry_id=i,
            )
        )
    return entries


def build_page(page: PageSource, *, with_entries: bool = True) -> PageBuildResult:
    """单页：scope → 建表 → 表文分裂 → 结构/脚注/Y 校准。"""
    if not page.table_regions:
        entries = _build_pure_text_page(page) if with_entries else []
        return PageBuildResult(
            page_number=page.page_number,
            entries=entries,
        )

    plan = plan_page_scopes(page)
    tables: List[StructuredTable] = []
    warnings: List[str] = []

    for scope in plan.scopes:
        scope = supplement_scope_missing_headers(page, scope)
        scope = supplement_scope_missing_body_above(page, scope)
        scope = supplement_scope_missing_body_below(page, scope)
        scope = supplement_scope_missing_intra_label_rows(page, scope)
        table = build_table_from_scope(scope)
        if table is None:
            warnings.append(
                f"P{page.page_number} region {scope.region_index}: build failed"
            )
            continue
        tables.append(table)

    entries: List[DocumentEntry] = []
    if with_entries:
        gap_texts = filter_description_captions(tables, list(plan.gap_texts))
        gap_texts = ensure_page_chrome_separated(page, gap_texts)
        entries = build_page_entries(tables=tables, gap_texts=gap_texts)
        entries = apply_page_chrome_to_entries(entries, page)
        entries = apply_structure_split(entries, page)
        entries = apply_adjacent_table_boundary_repair(entries)
        entries = dedupe_subset_overlapping_tables(entries)
        entries = apply_fragment_rejoin(entries)
        entries = apply_trailing_header_reattach(entries)
        entries = apply_leading_header_reattach(entries, page)
        entries = apply_footnote_strip(entries)
        entries = apply_content_partition(entries, page, warnings=warnings)
        entries = _dedupe_overlapping_text_entries(entries)
        entries = apply_header_audit(entries, page, warnings)
        entries = apply_y_calibration(entries, page)
        entries = apply_grid_prune(entries, page)
        entries = apply_item_conservation(entries, page, warnings)
        entries = apply_adjacent_table_boundary_repair(entries)
        entries = dedupe_subset_overlapping_tables(entries)
        entries = apply_sibling_compound_header_repair(entries)
        entries = apply_item_conservation(entries, page, warnings)
        entries = apply_adjacent_table_boundary_repair(entries)
        entries = dedupe_subset_overlapping_tables(entries)
        entries = apply_grid_prune(entries, page)
        entries = apply_content_partition(entries, page, warnings=warnings)
        entries = apply_cell_decomposition(entries)
        entries = _sort_entries(entries)

    return PageBuildResult(
        page_number=page.page_number,
        tables=tables,
        gap_texts=list(plan.gap_texts),
        entries=entries,
        warnings=warnings,
    )


def build_page_by_number(cache_path: str, page_num: int) -> PageBuildResult:
    doc = load_liteparse_document(cache_path)
    page = doc.get_page(page_num)
    if page is None:
        raise ValueError(f"页码 {page_num} 不存在")
    return build_page(page)


def primary_table(result: PageBuildResult) -> Optional[StructuredTable]:
    if not result.tables:
        return None
    return max(result.tables, key=lambda t: (t.y1 - t.y0) * (t.x1 - t.x0))


def entry_counts(result: PageBuildResult) -> tuple[int, int]:
    return count_entries(result.entries)


class DocumentBuilder:
    """全文档构建入口。"""

    def __init__(self, config: Optional[TableEngineConfig] = None) -> None:
        self.config = config or default_config()

    def build(
        self,
        pages_json_path: Optional[Union[str, Path]] = None,
    ) -> Document:
        path = Path(
            pages_json_path
            or self.config.pages_json_path
            or DEFAULT_PILLAR_CACHE
        )
        lite_doc = load_liteparse_document(path)
        all_entries: List[DocumentEntry] = []
        report = BuildReport()

        for page in lite_doc.pages:
            result = build_page(page, with_entries=True)
            all_entries.extend(result.entries)
            report.pages_processed += 1
            report.tables_built += len(result.tables)
            report.text_blocks += len([e for e in result.entries if e.kind == "text"])
            report.warnings.extend(result.warnings)
            if page.is_table_page and not result.tables:
                report.warnings.append(f"P{page.page_number}: table_page_no_table")

        all_entries.sort(key=_reading_order_key)
        _renumber_entries(all_entries)

        return Document(
            entries=all_entries,
            source_pdf=lite_doc.pdf_path,
            parse_kind="native",
            build_report=report,
        )
