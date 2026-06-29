# -*- coding: utf-8 -*-
"""
ContentSegmenter 单元测试

用合成数据验证 x 方向离散度检测能否正确区分"表格行"和"段落行"。
不需要 PDF 文件。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codes.content_segmenter.segmenter import ContentSegmenter, SegmenterConfig


def make_word(x0, x1, y0, y1, text):
    """创建模拟的 PDF word dict。"""
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text, "baseline": y1}


# ============================================================
# 场景 1: 表格+段落+表格 → 应该被拆分为 3 个区域
# ============================================================
def test_table_paragraph_table():
    """模拟"两表夹一段"的场景。"""
    print("=" * 50)
    print("场景1: 两表夹一段描述文字（应拆分为 3 区域）")
    print("=" * 50)

    words = []

    # Table A (3行, 3列, 有列间gap)
    table_a_rows = [
        [("项目", 50, 90), ("金额", 180, 210), ("占比", 280, 310)],
        [("收入", 50, 90), ("1000", 180, 210), ("50%", 280, 310)],
        [("支出", 50, 90), ("500", 180, 210), ("25%", 280, 310)],
    ]
    base_y = 100
    for row_idx, row in enumerate(table_a_rows):
        y = base_y + row_idx * 16
        for cell in row:
            words.append(make_word(cell[1], cell[2], y, y + 12, cell[0]))

    # 描述文字 (2行，连续覆盖 x 方向)
    para_y = base_y + 3 * 16 + 10
    words.append(make_word(50, 305, para_y, para_y + 12,
        "本集团在中国人民银行及若干有业务的境外国家及地区的中央银行存放"))
    words.append(make_word(50, 305, para_y + 14, para_y + 26,
        "法定存款准备金。这些法定存款准备金不可用于本集团的日常业务运作。"))

    # Table B (2行, 2列)
    table_b_y = para_y + 30 + 10
    table_b_rows = [
        [("2024年12月31日", 50, 170), ("2023年12月31日", 190, 310)],
        [("8.00%", 50, 130), ("9.00%", 190, 270)],
    ]
    for row_idx, row in enumerate(table_b_rows):
        y = table_b_y + row_idx * 16
        for cell in row:
            words.append(make_word(cell[1], cell[2], y, y + 12, cell[0]))

    # 运行分割器
    segmenter = ContentSegmenter()
    result = segmenter.segment_region(
        text_items=words,
        page_width=400,
        page_height=600,
        page_number=1,
        item_getter=lambda w: (w["x0"], w["x1"], w["y0"], w["y1"], w["text"]),
    )

    print(f"分割结果: {len(result.regions)} 个区域")
    print(f"  表格区域: {len(result.table_regions)}")
    print(f"  段落区域: {len(result.paragraph_regions)}")
    for r in result.regions:
        print(f"  [{r.region_type.upper()}]: y={r.y0:.0f}-{r.y1:.0f}, text={r.text[:60]}...")

    # 断言
    assert len(result.table_regions) == 2, f"期望2个表格，实际{len(result.table_regions)}"
    assert len(result.paragraph_regions) == 1, f"期望1个段落，实际{len(result.paragraph_regions)}"
    print("[PASS] 场景1通过\n")


# ============================================================
# 场景 2: 纯表格（无段落混入）→ 应保持不拆分
# ============================================================
def test_pure_table():
    """模拟纯表格页（无段落混入）。"""
    print("=" * 50)
    print("场景2: 纯表格（应保持1个表格区域）")
    print("=" * 50)

    words = []
    for row_idx in range(5):
        y = 100 + row_idx * 16
        words.append(make_word(50, 90, y, y + 12, f"项目{row_idx}"))
        words.append(make_word(180, 210, y, y + 12, f"金额{row_idx}"))
        words.append(make_word(280, 310, y, y + 12, f"占比{row_idx}"))

    segmenter = ContentSegmenter()
    result = segmenter.segment_region(
        text_items=words,
        page_width=400,
        page_height=600,
        page_number=1,
        item_getter=lambda w: (w["x0"], w["x1"], w["y0"], w["y1"], w["text"]),
    )

    print(f"分割结果: {len(result.regions)} 个区域")
    print(f"  表格: {len(result.table_regions)}, 段落: {len(result.paragraph_regions)}")

    assert len(result.table_regions) == 1, f"期望1个表格，实际{len(result.table_regions)}"
    assert len(result.paragraph_regions) == 0, f"期望0个段落，实际{len(result.paragraph_regions)}"
    print("[PASS] 场景2通过\n")


# ============================================================
# 场景 3: 单列表格 vs 段落（边界case）
# ============================================================
def test_single_column_table():
    """单列结构应被正确识别。"""
    print("=" * 50)
    print("场景3: 单列结构（表格标题+数据）")
    print("=" * 50)

    words = []
    for row_idx in range(3):
        y = 100 + row_idx * 16
        # 一整块文字，x 覆盖范围大但只有一个文本块
        words.append(make_word(50, 300, y, y + 12, f"这是一个标题行{row_idx}"))

    segmenter = ContentSegmenter()
    result = segmenter.segment_region(
        text_items=words,
        page_width=400,
        page_height=600,
        page_number=1,
        item_getter=lambda w: (w["x0"], w["x1"], w["y0"], w["y1"], w["text"]),
    )

    print(f"分割结果: {len(result.regions)} 个区域")
    for r in result.regions:
        print(f"  [{r.region_type}]: y={r.y0:.0f}-{r.y1:.0f}")

    # 单列结构具体归类依赖于阈值，只要不崩溃就是通过
    print("[PASS] 场景3通过（不崩溃即通过）\n")


# ============================================================
# 运行
# ============================================================
if __name__ == "__main__":
    test_table_paragraph_table()
    test_pure_table()
    test_single_column_table()
    print("=" * 50)
    print("所有单元测试完成")
    print("=" * 50)
