# -*- coding: utf-8 -*-
"""Step 5 并行三通道单元测试
用法: python _test_step5.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        failed += 1


# ====== 1. 通道提取测试 ======

print("=== 1. 通道独立提取 ===")

from codes.v2_steps.step5_triple_channel import (
    PyMuPDFChannel, PdfPlumberChannel, LiteParseChannel,
    TextFuser, Step5TripleChannel,
)

# PyMuPDF 通道（需要 fitz page，用 mock）
check("PyMuPDFChannel class exists", PyMuPDFChannel is not None)
check("PdfPlumberChannel class exists", PdfPlumberChannel is not None)
check("LiteParseChannel class exists", LiteParseChannel is not None)


# ====== 2. 融合去重测试 ======

print("\n=== 2. TextFuser 融合去重 ===")

# 模拟主通道 words
primary = [
    {"text": "货币资金", "x0": 50, "y0": 100, "x1": 120, "y1": 112, "source": "pymupdf"},
    {"text": "500,000", "x0": 200, "y0": 100, "x1": 260, "y1": 112, "source": "pymupdf"},
    {"text": "营业收入", "x0": 50, "y0": 120, "x1": 120, "y1": 132, "source": "pymupdf"},
]

# 补充通道 — 含重叠词和独有词
supplementary = [
    {"text": "货币资金", "x0": 50, "y0": 100, "x1": 120, "y1": 112, "source": "pdfplumber"},  # 完全重叠
    {"text": "450,000", "x0": 300, "y0": 100, "x1": 360, "y1": 112, "source": "pdfplumber"},  # 独有
    {"text": "净利润", "x0": 50, "y0": 140, "x1": 110, "y1": 152, "source": "pdfplumber"},    # 独有
]

fused = TextFuser.fuse(primary, supplementary)
check("融合后保留主通道全部", len(fused) >= len(primary),
      f"primary={len(primary)}, fused={len(fused)}")
check("补充独有词被加入", len(fused) > len(primary),
      f"fused={len(fused)}, primary={len(primary)}")
check("重叠词被去重", len(fused) == len(primary) + 2,  # 450,000 + 净利润
      f"expected {len(primary)+2}, got {len(fused)}")

# 检查独有词是否存在
texts = [w["text"] for w in fused]
check("补充词 '450,000' 存在", "450,000" in texts)
check("补充词 '净利润' 存在", "净利润" in texts)


# ====== 3. 多通道融合统计 ======

print("\n=== 3. fuse_multi 统计 ===")

channels = {
    "pymupdf": primary,
    "pdfplumber": supplementary,
}
merged, stats = TextFuser.fuse_multi(channels)
check("pymupdf 计数正确", stats["pymupdf"] == 3, f"got {stats['pymupdf']}")
check("pdfplumber_added 计数正确", stats.get("pdfplumber_added", 0) == 2,
      f"got {stats.get('pdfplumber_added', 0)}")
check("total 计数正确", stats["total"] == 5, f"got {stats['total']}")


# ====== 4. 空通道处理 ======

print("\n=== 4. 边界情况 ===")

# 仅主通道有数据
only_primary = TextFuser.fuse(primary, [])
check("补充为空时主通道不变", len(only_primary) == len(primary))

# 仅补充通道有数据
only_supp = TextFuser.fuse([], supplementary)
check("主通道为空时补充全保留", len(only_supp) == len(supplementary))

# 全部空
all_empty = TextFuser.fuse([], [])
check("全空返回空列表", len(all_empty) == 0)

# 接近但不完全重叠（偏移 < 5pt）
near_overlap = [
    {"text": "货币资金", "x0": 51, "y0": 101, "x1": 121, "y1": 113, "source": "pdfplumber"},
]
fused_near = TextFuser.fuse(primary, near_overlap)
check("接近重叠（文本相同+偏移<5pt）被去重", len(fused_near) == len(primary),
      f"expected {len(primary)}, got {len(fused_near)}")


# ====== 5. Step5TripleChannel 构造 ======

print("\n=== 5. Step5TripleChannel 集成 ===")

extractor = Step5TripleChannel({
    "enable_pymupdf": True,
    "enable_pdfplumber": True,
    "enable_liteparse": False,
    "parallel_workers": 2,
    "fusion_strategy": "pymupdf_primary",
})
check("extractor 创建成功", extractor is not None)
check("parallel_workers=2", extractor.parallel_workers == 2)
check("pdfplumber enabled", extractor.enable_pdfplumber)
check("liteparse disabled", not extractor.enable_liteparse)


# ====== 汇总 ======
print(f"\n{'='*40}")
print(f"Results: {passed} PASS, {failed} FAIL")
if failed == 0:
    print("All passed!")
else:
    print(f"FAILED: {failed} tests")
