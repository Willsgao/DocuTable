"""临时调试脚本"""
import sys, traceback
sys.path.insert(0, ".")

try:
    from codes.table_validator.hybrid_segmenter import (
        _strip_footnote_rows_from_data,
        _build_numeric_column_profile,
        _is_data_aligned,
    )
    print("Import OK")

    # Test 1: profile
    data1 = [
        ["现金、存款及同业存单", "1,008,220", "60.80", "20,512", "34.60"],
        ["债券", "440,983", "26.60", "5,052", "8.52"],
        ["总额", "1,658,154", "100.00", "59,285", "100.00"],
    ]
    profile = _build_numeric_column_profile(data1)
    print(f"Profile: {profile}")
    assert profile[0] is False, f"col0 should be False, got {profile[0]}"
    assert profile[1] is True, f"col1 should be True, got {profile[1]}"
    print("Test 1 PASS")

    # Test 2: user case
    data_user = [
        ["现金、存款及同业存单", "1,008,220", "60.80", "20,512", "34.60", "1,028,732", "59.90"],
        ["债券", "440,983", "26.60", "5,052", "8.52", "446,035", "25.97"],
        ["权益类资产", "1,793", "0.11", "25,679", "43.31", "27,472", "1.60"],
        ["非标准化债权类资产", "5,171", "0.31", "8,042", "13.57", "13,213", "0.77"],
        ["其他类资产^1", "201,987", "12.18", "－", "－", "201,987", "11.76"],
        ["总额", "1,658,154", "100.00", "59,285", "100.00", "1,717,439", "100.00"],
        ["1.", "包括公募基金、代客境外理财投资QDII、金融衍生品、拆放同业及债券买入返售。", "", "", "", "", ""],
    ]
    cleaned, notes = _strip_footnote_rows_from_data(data_user, 7)
    print(f"Cleaned: {len(cleaned)} rows, Notes: {notes}")
    assert len(cleaned) == 6, f"Expected 6, got {len(cleaned)}"
    print("Test 2 PASS")

    # Test 3: multi footnote
    data_multi = [
        ["营业收入", "100", "200", "300"],
        ["营业成本", "80", "160", "240"],
        ["净利润", "20", "40", "60"],
        ["1.", "包含子公司数据。", "", ""],
        ["2.", "数据已审计。", "", ""],
    ]
    cleaned2, notes2 = _strip_footnote_rows_from_data(data_multi, 4)
    print(f"Cleaned2: {len(cleaned2)} rows")
    assert len(cleaned2) == 3, f"Expected 3, got {len(cleaned2)}"
    print("Test 3 PASS")

    # Test 4: rank table (should NOT strip)
    data_rank = [
        ["排名", "名称", "金额", "占比"],
        ["1.", "工商银行", "500", "25%"],
        ["2.", "建设银行", "400", "20%"],
        ["3.", "农业银行", "300", "15%"],
    ]
    cleaned3, notes3 = _strip_footnote_rows_from_data(data_rank, 4)
    print(f"Cleaned3: {len(cleaned3)} rows")
    assert len(cleaned3) == 4, f"Expected 4, got {len(cleaned3)}"
    print("Test 4 PASS")

    # Test 5: data_aligned
    num_cols = [False, True, True, True, True, True, True]
    row_fn = ["1.", "包括公募基金...", "", "", "", "", ""]
    aligned = _is_data_aligned(row_fn, num_cols)
    print(f"Aligned: {aligned}")
    assert aligned is False, f"Should be False, got {aligned}"
    print("Test 5 PASS")

    print("\nALL TESTS PASSED!")
    sys.exit(0)

except Exception as e:
    traceback.print_exc()
    sys.exit(1)
