# -*- coding: utf-8 -*-
"""
Step 6: 统一 TextItem 格式

提供各通道 → TextItem 的标准化转换器。
每个通道的原始词格式不同，此模块统一归一化为 TextItem。

通道置信度基线：
- PyMuPDF words: 0.95（坐标最精确）
- pdfplumber:     0.85（坐标较精确，偶尔漏词）
- liteparse:      0.90（布局感知强，OCR可能有误差）
- paddleocr:      0.75（纯OCR，噪声较高）
"""

from __future__ import annotations

from typing import Dict, List, Optional, Any
from .models import TextItem


# ============================================================
# 通道置信度基线
# ============================================================

CHANNEL_CONFIDENCE = {
    "pymupdf": 0.95,
    "pdfplumber": 0.85,
    "liteparse": 0.90,
    "paddleocr": 0.75,
    "unknown": 0.50,
}


# ============================================================
# 转换器
# ============================================================

class Step6TextItemFormat:
    """统一 TextItem 格式转换器（V2 Step 6）

    使用方式：
        items = Step6TextItemFormat.from_pymupdf_words(words, page_num=1)
        items = Step6TextItemFormat.from_pdfplumber_words(words, page_num=1)
        items = Step6TextItemFormat.from_dicts(raw_dicts, source="pymupdf", page_num=1)
    """

    # ---- 通用：从原始 dict 转换 ----

    @staticmethod
    def from_dict(raw: Dict[str, Any],
                  source: str = "unknown",
                  page_num: int = 0) -> TextItem:
        """从原始 dict 构造 TextItem

        兼容现有 pipeline 中的 word 格式：
        {"text", "x0", "y0", "x1", "y1", "source"?, "font_size"?, "is_bold"?}

        Args:
            raw: 原始 word dict
            source: 来源通道标签
            page_num: 1-based 页码
        """
        return TextItem(
            text=str(raw.get("text", "")).strip(),
            x0=float(raw.get("x0", 0)),
            y0=float(raw.get("y0", 0)),
            x1=float(raw.get("x1", 0)),
            y1=float(raw.get("y1", 0)),
            page=page_num,
            source=raw.get("source", source),
            confidence=raw.get("confidence", CHANNEL_CONFIDENCE.get(source, 0.50)),
            font_size=float(raw.get("font_size", 0)),
            is_bold=bool(raw.get("is_bold", False)),
            block_type=raw.get("block_type", ""),
        )

    @staticmethod
    def from_dicts(raw_list: List[Dict[str, Any]],
                   source: str = "unknown",
                   page_num: int = 0) -> List[TextItem]:
        """批量转换"""
        return [Step6TextItemFormat.from_dict(r, source, page_num) for r in raw_list]

    # ---- PyMuPDF 通道（含 font 信息）----

    @staticmethod
    def from_pymupdf_words(words: List[Dict[str, Any]],
                           page_num: int = 0) -> List[TextItem]:
        """从 PyMuPDF words 转换（坐标最精确，置信度=0.95）"""
        items = []
        for w in words:
            item = Step6TextItemFormat.from_dict(w, source="pymupdf", page_num=page_num)
            item.confidence = 0.95
            items.append(item)
        return items

    @staticmethod
    def enrich_pymupdf_font(items: List[TextItem],
                            page: Any) -> List[TextItem]:
        """用 PyMuPDF span 信息补充 font_size 和 is_bold

        PyMuPDF 的 get_text("words") 不直接返回字体信息，
        需要从 get_text("dict") 的 spans 中按 bbox 匹配。

        Args:
            items: 已创建的 TextItem 列表（source="pymupdf"）
            page: fitz.Page 对象

        Returns:
            补充了 font_size / is_bold 的 TextItem 列表
        """
        try:
            text_dict = page.get_text("dict")
            blocks = text_dict.get("blocks", [])

            # 收集所有 span 的 font 信息
            span_infos: List[Dict] = []
            for block in blocks:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    bbox = line.get("bbox", [0, 0, 0, 0])
                    for span in line.get("spans", []):
                        span_text = span.get("text", "").strip()
                        if not span_text:
                            continue
                        span_infos.append({
                            "text": span_text,
                            "x0": span.get("bbox", bbox)[0],
                            "y0": span.get("bbox", bbox)[1],
                            "x1": span.get("bbox", bbox)[2],
                            "y1": span.get("bbox", bbox)[3],
                            "font_size": span.get("size", 0),
                            "font_name": span.get("font", ""),
                            "is_bold": "bold" in span.get("font", "").lower(),
                        })

            if not span_infos:
                return items

            # 按 bbox 匹配：item → 最近的 span
            for item in items:
                best_iou = 0.0
                best_span = None
                for si in span_infos:
                    # 计算 bbox 重叠度
                    x_left = max(item.x0, si["x0"])
                    y_top = max(item.y0, si["y0"])
                    x_right = min(item.x1, si["x1"])
                    y_bottom = min(item.y1, si["y1"])
                    if x_left >= x_right or y_top >= y_bottom:
                        continue
                    inter = (x_right - x_left) * (y_bottom - y_top)
                    area_item = max((item.x1 - item.x0) * (item.y1 - item.y0), 0.001)
                    area_span = max((si["x1"] - si["x0"]) * (si["y1"] - si["y0"]), 0.001)
                    iou = inter / min(area_item, area_span)
                    if iou > best_iou:
                        best_iou = iou
                        best_span = si

                if best_span and best_iou > 0.3:
                    item.font_size = best_span["font_size"]
                    item.is_bold = best_span["is_bold"]

        except Exception:
            pass  # font 信息补充失败不影响主流程

        return items

    # ---- pdfplumber 通道 ----

    @staticmethod
    def from_pdfplumber_words(words: List[Dict[str, Any]],
                              page_num: int = 0) -> List[TextItem]:
        """从 pdfplumber extract_words() 结果转换

        pdfplumber 输出格式：{"text", "x0", "top", "x1", "bottom"}
        需要将 "top"/"bottom" 映射到 "y0"/"y1"
        """
        items = []
        for w in words:
            item = TextItem(
                text=str(w.get("text", "")).strip(),
                x0=float(w.get("x0", 0)),
                y0=float(w.get("top", w.get("y0", 0))),
                x1=float(w.get("x1", 0)),
                y1=float(w.get("bottom", w.get("y1", 0))),
                page=page_num,
                source="pdfplumber",
                confidence=CHANNEL_CONFIDENCE["pdfplumber"],
            )
            items.append(item)
        return items

    # ---- liteparse 通道 ----

    @staticmethod
    def from_liteparse_textitems(text_items: List[Any],
                                 page_num: int = 0) -> List[TextItem]:
        """从 liteparse TextItem 对象转换

        liteparse 的 TextItem 格式：
        {text, x0, y0, x1, y1, font_size, font_name}
        """
        from codes.liteparse_extractor.models import TextItem as LPTextItem

        items = []
        for t in text_items:
            font_name = getattr(t, "font_name", "")
            item = TextItem(
                text=str(t.text).strip(),
                x0=float(t.x0), y0=float(t.y0),
                x1=float(t.x1), y1=float(t.y1),
                page=page_num,
                source="liteparse",
                confidence=CHANNEL_CONFIDENCE["liteparse"],
                font_size=float(getattr(t, "font_size", 0)),
                is_bold="bold" in str(font_name).lower(),
            )
            items.append(item)
        return items

    # ---- 统计工具 ----

    @staticmethod
    def summarize(items: List[TextItem]) -> Dict[str, Any]:
        """生成 TextItem 列表的统计摘要"""
        if not items:
            return {"count": 0}

        sources: Dict[str, int] = {}
        has_font = 0
        has_bold = 0
        for item in items:
            sources[item.source] = sources.get(item.source, 0) + 1
            if item.font_size > 0:
                has_font += 1
            if item.is_bold:
                has_bold += 1

        return {
            "count": len(items),
            "sources": sources,
            "with_font_size": has_font,
            "with_bold": has_bold,
            "avg_confidence": round(
                sum(t.confidence for t in items) / len(items), 3
            ) if items else 0,
        }
