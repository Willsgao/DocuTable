# -*- coding: utf-8 -*-
"""
DocuTable V2 — 模块化步骤流水线

8 个优化步骤，每步独立开发、测试、启用/禁用。
"""

from .models import (
    PipelineContext,
    GridResult,
    MergeSpan,
    ClassifyResult,
    TextItem,
)
from .pipeline import V2Pipeline
from .config import V2Config

__all__ = [
    # Pipeline
    "V2Pipeline",
    "V2Config",
    # Models
    "PipelineContext",
    "GridResult",
    "MergeSpan",
    "ClassifyResult",
    "TextItem",
]
