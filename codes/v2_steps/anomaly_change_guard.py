# -*- coding: utf-8 -*-
"""表格质检修改防护 — 改前改后标准化检查。

用法:
  python -m codes.v2_steps.anomaly_change_guard preflight   # 改代码前
  python -m codes.v2_steps.anomaly_change_guard verify      # 改代码后
  python -m codes.v2_steps.anomaly_change_guard golden      # 仅黄金样本
  python -m codes.v2_steps.anomaly_change_guard unit        # 仅单元测试
  python -m codes.v2_steps.anomaly_change_guard snapshot    # 更新基准快照

  python -m codes.v2_steps.anomaly_change_guard init-scope "任务描述"  # 从用户原话生成 scope
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "tests" / "anomaly_golden"
GOLDEN_CASES = GOLDEN_DIR / "cases"
MANIFEST = GOLDEN_DIR / "manifest.json"
SNAPSHOT = GOLDEN_DIR / "baseline_snapshot.json"
SCOPE_FILE = ROOT / "anomaly-change-scope.txt"
TEMPLATE = ROOT / "anomaly-change-scope.template.txt"
UNIT_TEST = ROOT / "_test_step1_anomaly_detect.py"
CONTRACT_DOC = ROOT / "docs" / "正常表契约.md"
FLOW_DOC = ROOT / "docs" / "表格质检-修改标准流程.md"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class GuardReport:
    phase: str
    results: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.ok for r in self.results)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(CheckResult(name, ok, detail))


def _detect_report(table_data: List[List[str]]) -> Dict[str, Any]:
    from codes.v2_steps.table_anomaly_rules import (
        evaluate_table_issues,
        issues_to_report,
        strip_blank_rows_cols,
    )
    cleaned = strip_blank_rows_cols([list(row) for row in table_data])
    issues, _ctx = evaluate_table_issues(cleaned)
    return issues_to_report(issues, cleaned)


def _match_expect(case_id: str, report: Dict[str, Any], expect: Dict[str, Any]) -> Tuple[bool, str]:
    rule_ids = set(report.get("rule_ids", []))

    if "needs_review" in expect:
        if report.get("needs_review") != expect["needs_review"]:
            return False, (
                f"needs_review 期望 {expect['needs_review']} "
                f"实际 {report.get('needs_review')} rules={sorted(rule_ids)}"
            )

    if "is_normal_table" in expect:
        if report.get("is_normal_table") != expect["is_normal_table"]:
            return False, (
                f"is_normal_table 期望 {expect['is_normal_table']} "
                f"实际 {report.get('is_normal_table')}"
            )

    if "rule_ids" in expect:
        exp = set(expect["rule_ids"])
        if rule_ids != exp:
            return False, f"rule_ids 期望 {sorted(exp)} 实际 {sorted(rule_ids)}"

    if "rule_ids_any" in expect:
        exp_any = set(expect["rule_ids_any"])
        if not rule_ids & exp_any:
            return False, f"rule_ids 应命中其一 {sorted(exp_any)} 实际 {sorted(rule_ids)}"

    if "rule_ids_none" in expect:
        bad = set(expect["rule_ids_none"]) & rule_ids
        if bad:
            return False, f"不应命中 {sorted(bad)}"

    if "header_missing" in expect:
        if report.get("header_missing") != expect["header_missing"]:
            return False, (
                f"header_missing 期望 {expect['header_missing']} "
                f"实际 {report.get('header_missing')}"
            )

    if "anomaly_class" in expect:
        if report.get("anomaly_class") != expect["anomaly_class"]:
            return False, (
                f"anomaly_class 期望 {expect['anomaly_class']} "
                f"实际 {report.get('anomaly_class')}"
            )

    return True, "ok"


def run_golden() -> GuardReport:
    report = GuardReport(phase="golden")
    if not MANIFEST.exists():
        report.add("manifest", False, f"缺少 {MANIFEST}")
        return report

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    case_files = manifest.get("cases", [])
    failed: List[str] = []

    for fname in case_files:
        path = GOLDEN_CASES / fname
        if not path.exists():
            failed.append(f"{fname}: 文件不存在")
            continue
        case = json.loads(path.read_text(encoding="utf-8"))
        cid = case.get("id", fname)
        data = case.get("data", [])
        expect = case.get("expect", {})
        try:
            det = _detect_report(data)
            ok, msg = _match_expect(cid, det, expect)
            if not ok:
                failed.append(f"{cid}: {msg}")
        except Exception as e:
            failed.append(f"{cid}: 异常 {e}")

    report.add(
        f"黄金样本 ({len(case_files)} 条)",
        len(failed) == 0,
        "\n  ".join(failed) if failed else "全部通过",
    )
    return report


def run_unit() -> GuardReport:
    report = GuardReport(phase="unit")
    if not UNIT_TEST.exists():
        report.add("单元测试", False, f"缺少 {UNIT_TEST}")
        return report

    proc = subprocess.run(
        [sys.executable, str(UNIT_TEST)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ok = proc.returncode == 0
    tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-400:]
    report.add("_test_step1_anomaly_detect", ok, tail.strip() if not ok else "PASS")
    return report


def _parse_scope() -> Optional[Dict[str, Any]]:
    if not SCOPE_FILE.exists():
        return None
    text = SCOPE_FILE.read_text(encoding="utf-8")
    allowed: List[str] = []
    forbidden: List[str] = []
    must_pass: List[str] = []
    task = ""
    section = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("task:"):
            task = s.split(":", 1)[1].strip()
            continue
        if s == "allowed:":
            section = "allowed"
            continue
        if s == "forbidden:":
            section = "forbidden"
            continue
        if s == "must_pass:":
            section = "must_pass"
            continue
        if s.startswith("- ") and section:
            val = s[2:].strip()
            if section == "allowed":
                allowed.append(val)
            elif section == "forbidden":
                forbidden.append(val)
            elif section == "must_pass":
                must_pass.append(val)
    return {
        "task": task,
        "allowed": allowed,
        "forbidden": forbidden,
        "must_pass": must_pass or ["golden", "unit"],
    }


def _git_changed_files() -> List[str]:
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        staged = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        unstaged = [ln.strip().replace("\\", "/") for ln in proc.stdout.splitlines() if ln.strip()]
        staged_files = [ln.strip().replace("\\", "/") for ln in staged.stdout.splitlines() if ln.strip()]
        return sorted(set(unstaged + staged_files))
    except Exception:
        return []


def _path_allowed(path: str, patterns: List[str]) -> bool:
    norm = path.replace("\\", "/")
    return any(fnmatch.fnmatch(norm, p) for p in patterns)


def check_scope() -> GuardReport:
    report = GuardReport(phase="scope")
    scope = _parse_scope()
    if scope is None:
        report.add(
            "改动范围",
            True,
            "未配置 anomaly-change-scope.txt（建议每次任务填写）",
        )
        return report

    changed = _git_changed_files()
    if not changed:
        report.add("改动范围", True, f"任务: {scope.get('task') or '(未写)'}；无 git 改动")
        return report

    allowed = scope.get("allowed", [])
    forbidden = scope.get("forbidden", [])
    violations: List[str] = []

    for f in changed:
        if forbidden and _path_allowed(f, forbidden):
            violations.append(f"禁止改动: {f}")
        elif allowed and not _path_allowed(f, allowed):
            violations.append(f"超出 allowed: {f}")

    report.add(
        "改动范围",
        len(violations) == 0,
        "\n  ".join(violations) if violations else f"任务: {scope.get('task')}；改动: {', '.join(changed)}",
    )
    return report


def check_docs_exist() -> GuardReport:
    report = GuardReport(phase="docs")
    ok = CONTRACT_DOC.exists() and FLOW_DOC.exists()
    report.add("契约/流程文档", ok, str(CONTRACT_DOC) if ok else "缺少文档")
    return report


def write_snapshot() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries: Dict[str, Any] = {}
    for fname in manifest.get("cases", []):
        case = json.loads((GOLDEN_CASES / fname).read_text(encoding="utf-8"))
        data = case.get("data", [])
        det = _detect_report(data)
        entries[case["id"]] = {
            "needs_review": det.get("needs_review"),
            "is_normal_table": det.get("is_normal_table"),
            "rule_ids": sorted(det.get("rule_ids", [])),
        }
    payload = {
        "version": manifest.get("version", 1),
        "hash": hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()[:16],
        "cases": entries,
    }
    SNAPSHOT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _safe_print(f"[snapshot] 已写入 {SNAPSHOT} hash={payload['hash']}")
    return 0


def _merge_reports(phase: str, parts: List[GuardReport]) -> GuardReport:
    merged = GuardReport(phase=phase)
    for p in parts:
        merged.results.extend(p.results)
    return merged


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8", errors="replace"
        ))


def _print_report(report: GuardReport) -> None:
    _safe_print("=" * 60)
    _safe_print(f"表格质检防护 — {report.phase}")
    _safe_print("=" * 60)
    for r in report.results:
        mark = "PASS" if r.ok else "FAIL"
        _safe_print(f"  [{mark}] {r.name}")
        if r.detail:
            for line in r.detail.splitlines():
                _safe_print(f"         {line}")
    _safe_print("-" * 60)
    status = "全部通过 [OK]" if report.passed else "存在失败 [FAIL] — 不得宣称修改完成"
    _safe_print(f"结果: {status}")
    _safe_print("=" * 60)


def init_scope(task: str, extra_allowed: Optional[List[str]] = None) -> int:
    """从模板生成 scope，task 用用户原话（Agent 调用，用户不手填）。"""
    if not task.strip():
        _safe_print("[init-scope] 需要任务描述")
        return 1
    if not TEMPLATE.exists():
        _safe_print(f"[init-scope] 缺少模板 {TEMPLATE}")
        return 1
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("（一句话描述本次目标）", task.strip())
    if extra_allowed:
        for pattern in extra_allowed:
            if f"  - {pattern}" not in text:
                text = text.replace(
                    "  - codes/v2_steps/table_anomaly_rules.py\n",
                    f"  - codes/v2_steps/table_anomaly_rules.py\n  - {pattern}\n",
                )
    SCOPE_FILE.write_text(text, encoding="utf-8")
    _safe_print(f"[init-scope] 已写入 {SCOPE_FILE}")
    _safe_print(f"  task: {task.strip()}")
    return 0


def preflight() -> int:
    report = _merge_reports("preflight", [
        check_docs_exist(),
        run_golden(),
        run_unit(),
    ])
    _print_report(report)
    return 0 if report.passed else 1


def verify() -> int:
    scope = _parse_scope()
    must = (scope or {}).get("must_pass", ["golden", "unit"])
    parts = [check_docs_exist(), check_scope()]
    if "golden" in must:
        parts.append(run_golden())
    if "unit" in must:
        parts.append(run_unit())
    report = _merge_reports("verify", parts)
    _print_report(report)
    return 0 if report.passed else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cmd = (args[0] if args else "verify").lower()
    if cmd in ("preflight", "pre"):
        return preflight()
    if cmd in ("verify", "post"):
        return verify()
    if cmd == "golden":
        r = run_golden()
        _print_report(r)
        return 0 if r.passed else 1
    if cmd == "unit":
        r = run_unit()
        _print_report(r)
        return 0 if r.passed else 1
    if cmd == "snapshot":
        return write_snapshot()
    if cmd in ("init-scope", "init_scope", "scope"):
        task = " ".join(args[1:]).strip() if len(args) > 1 else ""
        return init_scope(task)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
