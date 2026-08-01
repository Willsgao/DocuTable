# -*- coding: utf-8 -*-
"""页眉/页脚拆分回归。"""
from __future__ import annotations

from pathlib import Path

from codes.table_engine.export.legacy_adapter import to_legacy_text
from codes.table_engine.pipeline import build_page
from codes.table_engine.scope.page_chrome import extract_page_chrome
from codes.table_engine.source.liteparse_loader import load_liteparse_document


def _cache_root() -> Path:
    mid = Path(__file__).resolve().parent / "data" / "mid_cache"
    cands = [p for p in mid.iterdir() if "601939" in p.name and (p / "liteparse" / "pages.json").exists()]
    assert cands, "缺少 601939 mid_cache"
    return max(cands, key=lambda p: (p / "data.json").stat().st_size)


def test_extract_header_on_p242():
    root = _cache_root()
    page = load_liteparse_document(root / "liteparse" / "pages.json").get_page(242)
    assert page is not None
    blocks, ids = extract_page_chrome(page)
    headers = [b for b in blocks if b.role == "page_header"]
    assert headers, "应抽出页眉"
    text = headers[0].text
    assert "年度报告" in text or "建设银行" in text or "股份有限公司" in text
    assert "财务报表附注" not in text
    assert "除特别注明" not in text
    assert ids


def test_build_page_tags_header_category():
    root = _cache_root()
    page = load_liteparse_document(root / "liteparse" / "pages.json").get_page(242)
    result = build_page(page)
    header_entries = [
        e
        for e in result.entries
        if e.kind == "text"
        and e.text_block is not None
        and e.text_block.role == "page_header"
    ]
    assert header_entries, "build_page 应产出 page_header 条目"
    legacy = to_legacy_text(header_entries[0].text_block)
    assert legacy["table_category"] == "页眉"
    # 页眉不应再与正文粘成超大 y 跨度块
    for e in header_entries:
        assert e.y1 - e.y0 < 80, (e.y0, e.y1, e.text_block.text[:40])


def test_footer_page_when_present():
    root = _cache_root()
    page = load_liteparse_document(root / "liteparse" / "pages.json").get_page(201)
    assert page is not None
    result = build_page(page)
    footers = [
        e
        for e in result.entries
        if e.text_block is not None and e.text_block.role == "page_footer"
    ]
    assert footers, "P201 应保留页底附注声明为页脚"
    assert "组成部分" in footers[0].text_block.text
    legacy = to_legacy_text(footers[0].text_block)
    assert legacy["table_category"] == "页脚"


def test_mid_page_not_footer():
    root = _cache_root()
    page = load_liteparse_document(root / "liteparse" / "pages.json").get_page(21)
    assert page is not None
    result = build_page(page)
    for e in result.entries:
        if e.text_block and e.text_block.role == "page_footer":
            assert e.y0 > 700, f"中部正文误标页脚: y={e.y0} {e.text_block.text[:40]}"


if __name__ == "__main__":
    test_extract_header_on_p242()
    test_build_page_tags_header_category()
    test_footer_page_when_present()
    test_mid_page_not_footer()
    print("OK")
