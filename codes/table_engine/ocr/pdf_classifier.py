# -*- coding: utf-8 -*-
"""PDF 类型分类：原生文本 vs 扫描件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional

PdfKind = Literal["native", "scanned", "empty"]


@dataclass
class PdfClassifyResult:
    kind: PdfKind
    text_pages: int = 0
    image_pages: int = 0
    blank_pages: int = 0
    sample_pages: int = 0
    details: List[str] = field(default_factory=list)

    @property
    def is_scanned(self) -> bool:
        return self.kind == "scanned"

    @property
    def needs_ocr(self) -> bool:
        return self.kind == "scanned"


class PdfClassifier:
    """采样前几页 + 中部页，判断 PDF 是否以扫描图为主。"""

    @classmethod
    def classify(
        cls,
        pdf_path: str,
        *,
        context=None,
        max_sample: int = 8,
    ) -> PdfClassifyResult:
        import fitz  # PyMuPDF

        if context is not None:
            doc = context.doc
            close_doc = False
        else:
            if not pdf_path:
                return PdfClassifyResult(kind="empty")
            doc = fitz.open(pdf_path)
            close_doc = True

        try:
            total = len(doc)
            if total == 0:
                return PdfClassifyResult(kind="empty")

            sample_pages = list(range(min(5, total)))
            if total > 10:
                mid = total // 2
                for p in range(mid - 2, min(mid + 3, total)):
                    if p not in sample_pages:
                        sample_pages.append(p)
            sample_pages = sample_pages[:max_sample]

            text_pages = 0
            image_pages = 0
            blank_pages = 0
            details: List[str] = []

            for page_num in sample_pages:
                page = doc[page_num]
                text_dict = page.get_text("dict")
                blocks = text_dict.get("blocks", [])

                text_spans = 0
                total_chars = 0
                for block in blocks:
                    if block.get("type") == 0:
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                t = span.get("text", "").strip()
                                if t:
                                    total_chars += len(t)
                                    text_spans += 1

                images = page.get_images()
                if text_spans == 0 and len(images) > 0:
                    image_pages += 1
                    details.append(f"p{page_num + 1}=图片")
                elif text_spans > 0:
                    text_pages += 1
                    details.append(
                        f"p{page_num + 1}=文本({text_spans}span/{total_chars}字)"
                    )
                else:
                    blank_pages += 1
                    details.append(f"p{page_num + 1}=空白")

            if text_pages == 0 and image_pages == 0:
                kind: PdfKind = "empty"
            elif image_pages > text_pages:
                kind = "scanned"
            else:
                kind = "native"

            return PdfClassifyResult(
                kind=kind,
                text_pages=text_pages,
                image_pages=image_pages,
                blank_pages=blank_pages,
                sample_pages=len(sample_pages),
                details=details,
            )
        finally:
            if close_doc:
                doc.close()

    @classmethod
    def is_scanned_pdf(cls, pdf_path: str, *, context=None) -> bool:
        return cls.classify(pdf_path, context=context).is_scanned
