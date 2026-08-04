# -*- coding: utf-8 -*-
"""表格质检单元测试：正常表全覆盖 + 异常表必检出（无需 pytest）"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

passed = 0
failed = 0


def check(name, result):
    global passed, failed
    if result:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}")
        failed += 1


print("=" * 60)
print("表格质检单元测试（正常契约 + 异常筛选）")
print("=" * 60)

from codes.v2_steps.step1_column_split import Step1ColumnSplit

DETECT = Step1ColumnSplit._detect_table_anomalies


def _rules(report):
    return set(report.get("rule_ids", []))


# ---- 正常表：基础三列文本 ----
print("\n>>> Test 1: 正常三列文本表不误报")
data1 = [
    ["名称", "地址", "电话"],
    ["支行A", "成都市XX路1号", "028-12345678"],
    ["支行B", "成都市YY路2号", "028-87654321"],
]
r1 = DETECT(data1, [], [(0, 10)] * 3, [0, 60, 200, 300])
check("正常表 needs_review=False", r1["needs_review"] is False)


# ---- 正常表：数值列 + 合法折行（P43 形态）----
print("\n>>> Test 2: 正常折行续行（契约2）不误报")
data_wrap = [
    ["序号", "机构名称", "地址", "机构数", "员工数", "资产"],
    ["10", "德阳分行", "四川省德阳市旌阳区沱江路 188 号", "3", "91", "16294813"],
    ["", "", "知汇华庭裙楼 1、2 层", "", "", ""],
    ["11", "阿坝分行", "马江街 115 号", "1", "24", "1228078"],
    ["", "", "四川省阿坝羌族藏族自治州马尔康县", "", "", ""],
    ["", "", "元 1、2 层", "", "", ""],
]
r_wrap = DETECT(data_wrap, [], [(0, 10)] * len(data_wrap), [0, 60, 140, 320, 360, 420, 520])
check("折行表 needs_review=False", r_wrap["needs_review"] is False)
check("折行表 rule_ids 为空", _rules(r_wrap) == set())


# ---- 正常表：多层表头 ----
print("\n>>> Test 3: 多层表头不误报")
data_mh = [
    ["", "2023年", "2024年"],
    ["项目", "金额", "金额"],
    ["营业收入", "1000", "1200"],
    ["净利润", "200", "250"],
]
r_mh = DETECT(data_mh, [], [(0, 10)] * 4, [0, 60, 140, 220])
check("多层表头 needs_review=False", r_mh["needs_review"] is False)


# ---- 正常表：首列无表头（序号列豁免）----
print("\n>>> Test 4: 首列序号无表头不误报")
data_serial = [
    ["", "名称", "金额"],
    ["1", "项目A", "100"],
    ["2", "项目B", "200"],
]
r_serial = DETECT(data_serial, [], [(0, 10)] * 3, [0, 40, 120, 200])
check("序号列无表头 needs_review=False", r_serial["needs_review"] is False)


# ---- 正常表：数值列少量短码 ----
print("\n>>> Test 5: 数值列少量 ≤3 字短码不误报")
data_short = [
    ["序号", "评级", "金额"],
    ["1", "a", "1000"],
    ["2", "b", "2000"],
    ["3", "c", "3000"],
    ["4", "a", "4000"],
]
r_short = DETECT(data_short, [], [(0, 10)] * 5, [0, 40, 80, 160])
check("少量短码 needs_review=False", r_short["needs_review"] is False)


# ---- 正常表：小节行（结构行）----
print("\n>>> Test 6: 小节标题行不误报")
data_struct = [
    ["序号", "项目", "金额"],
    ["", "一、资产类", ""],
    ["1", "现金", "100"],
    ["2", "存款", "200"],
]
r_struct = DETECT(data_struct, [], [(0, 10)] * 4, [0, 40, 120, 200])
check("小节行 needs_review=False", r_struct["needs_review"] is False)


# ---- 异常：数值合并 R04 ----
print("\n>>> Test 7: 数值合并 R04")
data2 = [
    ["名称", "2023", "2024"],
    ["支行A", "1234 5678", "2345"],
    ["支行B", "3456", "4567 8901"],
]
r2 = DETECT(data2, [], [(0, 10)] * 3, [0, 60, 140, 220])
check("数值合并检出 2 个", len(r2["merged_values"]) == 2)
check("命中 R04", "R04_merged_numeric" in _rules(r2))


# ---- Test 8: 全空白列删除后不再报幽灵列 ----
print("\n>>> Test 8: 全空白列删除")
data3 = [
    ["名称", "", "金额"],
    ["支行A", "", "12345"],
    ["支行B", "", "56789"],
]
r3 = DETECT(data3, [], [(0, 10)] * 3, [0, 60, 140, 220])
check("全空白列删除后不误报", r3["needs_review"] is False)
check("无 R06", "R06_ghost_column" not in _rules(r3))


# ---- 异常：数值列长文本 R05 ----
print("\n>>> Test 9: 数值列长文本 R05")
data4 = [
    ["序号", "金额"],
    ["1", "12345"],
    ["2", "67890"],
    ["3", "文本杂质AAAAAAAA"],
    ["4", "24680"],
]
r4 = DETECT(data4, [], [(0, 10)] * 5, [0, 60, 140])
check("类型混杂 needs_review", r4["needs_review"] is True)
check("命中 R05", "R05_text_in_numeric" in _rules(r4))


# ---- 异常：words=[] 仍检 R04 ----
print("\n>>> Test 10: words=[] 不跳过 R04")
data5 = [
    ["序号", "金额"],
    ["1", "12345 67890"],
    ["2", "99999 88888"],
]
r5 = DETECT(data5, [], [(0, 10)] * 3, [0, 60, 140])
check("words=[] 仍检出合并", len(r5["merged_values"]) >= 1)


# ---- 异常：P43 类分列错误（粘连/吞并）----
print("\n>>> Test 11: P43 类分列错误")
data6 = [
    ["序号", "机构名称", "地址", "机构数", "员工人数", "资产总额"],
    ["10", "德阳分行", "四川省德阳市旌阳区沱江路 188 号", "3", "91", "16,294,813"],
    ["", "", "知汇华庭裙楼 1、2 层", "", "", ""],
    ["", "", "四川省阿坝羌族藏族自治州马尔康县", "", "", ""],
    ["12", "泸州分行",
     "四川省泸州市江阳区一环路 17 号 2 号楼 四川省绵阳市高新区绵兴东路 113 号",
     "4", "90", "16,248,842"],
    ["", "", "樊华广场 1-3 层", "", "", ""],
    ["", "14 天府新区分行 成都市天府新区湖畔路西段 30 号", "", "8", "229", "24,954,876"],
]
r6 = DETECT(data6, [], [(0, 10)] * len(data6), [0, 60, 140, 320, 360, 420, 520])
check("P43错误表 needs_review=True", r6["needs_review"] is True)
check("命中 R02 或 R03", bool(_rules(r6) & {"R02_merged_in_short_col", "R03_stacked_long_text"}))


# ---- 异常：邻列表头错位 R08 ----
print("\n>>> Test 12: 邻列表头/数据错位 R08")
data8 = [
    ["序号", "有表头列", "", "金额"],
    ["1", "数据A", "错位列数据1", "100"],
    ["2", "", "错位列数据2", "200"],
    ["3", "", "错位列数据3", "300"],
    ["4", "", "错位列数据4", "400"],
]
r8 = DETECT(data8, [], [(0, 10)] * 5, [0, 40, 100, 160, 220])
check("R08 needs_review", r8["needs_review"] is True)
check("命中 R08", "R08_header_data_misalign" in _rules(r8))


# ---- 异常：中间列孤立碎片 R09 ----
print("\n>>> Test 13: 中间列孤立碎片 R09")
data9 = [
    ["序号", "项目", "2023", "2024"],
    ["1", "收入", "1000", "1200"],
    ["2", "支出", "800", "900"],
    ["", "", "孤立数值", ""],
    ["3", "利润", "200", "300"],
]
r9 = DETECT(data9, [], [(0, 10)] * 5, [0, 40, 80, 120, 160])
check("R09 needs_review", r9["needs_review"] is True)
check("命中 R09", "R09_interior_singleton" in _rules(r9))


# ---- 异常：缺表头 C01 ----
print("\n>>> Test 14: 数据列缺表头 C01")
data_c01 = [
    ["序号", "名称", ""],
    ["1", "A", "100"],
    ["2", "B", "200"],
]
r_c01 = DETECT(data_c01, [], [(0, 10)] * 3, [0, 40, 120, 200])
check("C01 不触发 needs_review", r_c01["needs_review"] is False)
check("C01 header_missing", r_c01.get("header_missing") is True)
check("C01 anomaly_class", r_c01.get("anomaly_class") == "missing_header")
check("命中 C01", "C01_missing_header" in _rules(r_c01))


# ---- 异常：孤立长文本（非折行）R01 ----
print("\n>>> Test 15: 孤立长文本 R01")
data_r01 = [
    ["序号", "名称", "地址", "金额"],
    ["", "小节标题", "", ""],
    ["", "", "无法接龙的孤立长文本片段在这里", ""],
    ["1", "A", "成都市XX路", "100"],
]
r_r01 = DETECT(data_r01, [], [(0, 10)] * 4, [0, 40, 100, 200, 260])
check("R01/C02 needs_review", r_r01["needs_review"] is True)
check("命中 C02 或 R01", bool(_rules(r_r01) & {"C02_unrecognized_data_row", "R01_orphan_extension"}))


# ---- 异常：C2 无法归类的数据行 ----
print("\n>>> Test 16: C2 无法归类数据行")
data_c02 = [
    ["列A", "列B", "列C"],
    ["foo", "bar", "baz"],
    ["xxx", "", "yyy"],
]
r_c02 = DETECT(data_c02, [], [(0, 10)] * 3, [0, 40, 80, 120])
check("C02 needs_review", r_c02["needs_review"] is True)
check("命中 C02", "C02_unrecognized_data_row" in _rules(r_c02))
check("非正常表 is_normal_table=False", r_c02.get("is_normal_table") is False)


# ---- 正常：多层表头 + 报告期列标 ----
print("\n>>> Test 17: 报告期/年份在表头区")
data_rp = [
    ["", "2025年", "2024年", "同比变动（%）"],
    ["项目", "金额", "金额", ""],
    ["营业收入", "1000", "900", "11.1"],
    ["净利润", "200", "180", "11.1"],
]
r_rp = DETECT(data_rp, [], [(0, 10)] * 4, [0, 60, 120, 180, 240])
check("报告期表头 needs_review=False", r_rp["needs_review"] is False)


# ---- 豁免：纯文本列邻列无空白时长文本不报错 ----
print("\n>>> Test 18: 纯文本列过长但邻列有内容不误报")
data_text_len = [
    ["序号", "名称", "地址", "金额"],
    ["1", "A", "四川省成都市高新区天府大道北段 1700 号环球中心 E2 栋", "100"],
    ["2", "B", "四川省绵阳市涪城区临园路东段 72 号", "200"],
]
r_text_len = DETECT(data_text_len, [], [(0, 10)] * 3, [0, 60, 140, 400, 480])
check("邻列有内容时长文本不误报", r_text_len["needs_review"] is False)


# ---- 异常：金额+文本粘连 R10（重点严格审核）----
print("\n>>> Test 19: 金额与文本粘连 R10")
data_glue = [
    ["地区 营业收入", "占比"],
    ["19,079,642 成都", "83.02%"],
    ["3,901,885 其他地区", "16.98%"],
]
r_glue = DETECT(data_glue, [], [(0, 10)] * 3, [0, 120, 200])
check("粘连 needs_review", r_glue["needs_review"] is True)
check("命中 R10", "R10_numeric_text_glue" in _rules(r_glue))
check("strict_review", r_glue.get("strict_review") is True)
check("评分不低于 0.85", float(r_glue.get("anomaly_score") or 0) >= 0.85)


# ---- 错误类型目录 + 按类型规则修 ----
print("\n>>> Test 20: error_types / invariants / typed_repair")
from codes.table_repair.error_types import (
    ERROR_TYPES,
    ERROR_BY_ID,
    GLOBAL_PRINCIPLES,
    build_typed_llm_instructions,
    catalog_as_markdown,
    errors_from_checklist_findings,
    partition_errors,
)
from codes.table_repair.invariants import strip_title_rows, locate_data_zone
from codes.table_repair.typed_repair import apply_typed_rule_fixes, run_typed_repair_on_table

check("错误类型目录非空", len(ERROR_TYPES) >= 8)
check("含 H_TITLE / H_ALIGN / N_LOSS", {"H_TITLE", "H_ALIGN", "N_LOSS"} <= set(ERROR_BY_ID))
check("全局原则非空", len(GLOBAL_PRINCIPLES) >= 4)
md = catalog_as_markdown()
check("catalog markdown 含全局原则", "数据区列界是真理" in md and "H_TITLE" in md)

title_grid = [
    ["XX银行股份有限公司2024年资本管理信息披露报告", "", "", ""],
    ["项目", "期末余额", "上年末余额", ""],
    ["核心一级资本", "1000", "900", ""],
    ["一级资本", "1100", "1000", ""],
]
stripped, removed, notes = strip_title_rows(title_grid)
check("strip_title 去掉标题行", len(stripped) == 3 and len(removed) >= 1)
zone = locate_data_zone(stripped)
check("数据区从金额行起", zone.start_row >= 1 and zone.end_row > zone.start_row)

errs = [ERROR_BY_ID["H_TITLE"]]
fixed_grid, fix_notes, fixed_ids = apply_typed_rule_fixes(title_grid, errs)
check("typed 规则修 H_TITLE", "H_TITLE" in fixed_ids and len(fixed_grid) < len(title_grid))

findings = [
    {"check_id": "H05", "passed": False, "fix_status": "needs_fix"},
    {"check_id": "H04", "passed": False, "fix_status": "needs_llm"},
    {"check_id": "N02", "passed": False, "fix_status": "needs_human"},
]
mapped = errors_from_checklist_findings(findings)
ids = {e.error_id for e in mapped}
check("findings 映射含 H_TITLE/H_ALIGN/N_LOSS", {"H_TITLE", "H_ALIGN", "N_LOSS"} <= ids)
parts = partition_errors(mapped)
check("partition human 含 N_LOSS", any(e.error_id == "N_LOSS" for e in parts["human"]))
instr = build_typed_llm_instructions(parts["llm"])
check("typed LLM 指令含对齐任务", "H_ALIGN" in instr and "禁止" in instr)

tbl = {"type": "table", "page": 1, "data": title_grid, "rows": 4, "cols": 4}
res = run_typed_repair_on_table(
    tbl,
    findings=[{"check_id": "H05", "passed": False, "fix_status": "needs_fix"}],
    run_llm=False,
    apply=False,
)
check("仅规则修成功且去掉标题", res.success and len(res.repaired_table or []) <= 3)


# ---- 表分流：目录不进结构纠错 ----
print("\n>>> Test 21: table_kind 目录/数据分流")
from codes.table_repair.table_kind import classify_table_kind, should_run_structure_repair
from codes.table_repair.validator import amounts_invented
from codes.format_corrector.candidates import _table_has_any_error

toc_data = [
    ["财务报表与监管风险暴露间的联系......................................................14", ""],
    ["..................14", "4.1 财务数据和监管数据间差异的原因......................................................14"],
    ["薪酬.........................................................................................................15", ""],
    ["..................15", "5.1 薪酬政策..........................................................................................15"],
    ["信用风险.....................................................................................................17", ""],
    ["..................17", ""],
    ["..................18", ""],
]
toc_kind = classify_table_kind(toc_data)
check("目录识别为 toc", toc_kind.kind == "toc")
toc_table = {
    "type": "table",
    "data": toc_data,
    "_anomaly": {"needs_review": True, "rule_ids": ["C01_no_header_band"]},
    "repair_status": "llm_candidate",
}
ok_toc, _, summary_toc = _table_has_any_error(toc_table)
check("目录不进结构AI", ok_toc is False and str(summary_toc).startswith("skip_toc"))

data_tbl = [
    ["项目", "期末余额", "上年末"],
    ["核心一级资本", "1,000,000", "900,000"],
    ["一级资本", "1,100,000", "1,000,000"],
    ["总资本", "1,200,000", "1,100,000"],
]
check("指标表识别为 data", classify_table_kind(data_tbl).kind == "data")
check(
    "指标表可结构修",
    should_run_structure_repair({"type": "table", "data": data_tbl}),
)
# 章节号不计入补造
before_sec = [["标题", ""], ["", "财务数据"]]
after_sec = [["标题", ""], ["4.1 财务数据", "财务数据"]]
inv = amounts_invented(before_sec, after_sec)
check("章节号4.1不报补造", "4.1" not in inv)


# ---- 还原主链外壳（不破坏旧行为）----
print("\n>>> Test 22: reconstruct 主链快照")
from codes.reconstruct import run_table_reconstruct, RECONSTRUCT_VERSION

rec_tbl = {
    "type": "table",
    "page": 1,
    "data": [
        ["项目", "金额"],
        ["资产", "1,000,000"],
        ["负债", "800,000"],
    ],
}
snap = run_table_reconstruct(rec_tbl, run_llm=False)
check("写入 _reconstruct", isinstance(rec_tbl.get("_reconstruct"), dict))
check("快照 version", snap.get("version") == RECONSTRUCT_VERSION)
check("未开放入库", snap.get("accepted_for_ingest") is False)
check("有 table_kind", bool(snap.get("table_kind")))
check("策略含 liteparse", "liteparse" in str(snap.get("policy") or ""))
check("有 policy_trace", bool(snap.get("policy_trace")))

# 粘连应在数据主体阶段被规则拆开
glue_tbl = {
    "type": "table",
    "page": 2,
    "data": [
        ["地区 营业收入", "占比"],
        ["19,079,642 成都", "83.02%"],
        ["3,901,885 其他地区", "16.98%"],
    ],
}
from codes.reconstruct.data_body import prepare_data_body
gb = prepare_data_body(glue_tbl)
check("粘连阶段执行", isinstance(glue_tbl.get("_data_body"), dict))
# 拆后不应再保留整格「金额+中文」
still_glued = any(
    "19,079,642" in str(c) and "成都" in str(c)
    for r in (glue_tbl.get("data") or [])
    if isinstance(r, list)
    for c in r
)
check("金额文本粘连已拆开", not still_glued or gb["meta"].get("glue_fixed") is True)
if gb["meta"].get("glue_fixed"):
    check("粘连拆开后无同格混写", not still_glued)

toc_rec = {
    "type": "table",
    "data": toc_data,
}
snap_toc = run_table_reconstruct(toc_rec, run_llm=False)
check("目录 stage 为 skipped", snap_toc.get("stage") == "skipped_non_data")


# Summary
print("\n" + "=" * 60)
print(f"结果: {passed} PASS, {failed} FAIL")
print("=" * 60)
if failed == 0:
    print("ANOMALY DETECT TESTS COMPLETE - All tests passed!")
else:
    print("Some tests failed, please check.")

sys.exit(0 if failed == 0 else 1)
