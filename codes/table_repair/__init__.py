# -*- coding: utf-8 -*-
"""表格问题初判 → 全量检查 → 按错误类型规则修 → 标注 → 可选分类型 LLM → 人工队列。

错误类型与纠正原则的单一事实来源见 error_types；结构不变量见 invariants。
"""

from codes.table_repair.problem_model import (
    PROBLEM_TAGS,
    TableProblemReport,
    build_problem_report,
    attach_problem_report,
)
from codes.table_repair.router import run_repair_router_on_table, run_repair_router_on_payload
from codes.table_repair.llm_facade import (
    FacadeResult,
    repair_for_ui,
    repair_table_dict_with_facade,
    repair_with_facade,
)
from codes.table_repair.validator import validate_repair
from codes.table_repair.human_queue import (
    HumanQueueItem,
    apply_queue_decisions,
    collect_human_queue,
    store_llm_proposal,
)
from codes.table_repair.pipeline import run_table_repair_pipeline
from codes.table_repair.check_catalog import CHECK_CATALOG, catalog_ids
from codes.table_repair.checklist import run_full_checklist
from codes.table_repair.column_roles import infer_column_roles
from codes.table_repair.error_types import (
    ERROR_TYPES,
    GLOBAL_PRINCIPLES,
    catalog_as_markdown,
    errors_from_checklist_findings,
)
from codes.table_repair.invariants import (
    locate_data_zone,
    validate_structure_invariants,
)
from codes.table_repair.typed_repair import (
    TypedRepairResult,
    run_typed_repair_on_table,
)
from codes.table_repair.table_kind import (
    TableKindResult,
    attach_table_kind,
    classify_table_kind,
    should_run_structure_repair,
)

__all__ = [
    "PROBLEM_TAGS",
    "TableProblemReport",
    "build_problem_report",
    "attach_problem_report",
    "run_repair_router_on_table",
    "run_repair_router_on_payload",
    "run_table_repair_pipeline",
    "run_full_checklist",
    "infer_column_roles",
    "CHECK_CATALOG",
    "catalog_ids",
    "ERROR_TYPES",
    "GLOBAL_PRINCIPLES",
    "catalog_as_markdown",
    "errors_from_checklist_findings",
    "locate_data_zone",
    "validate_structure_invariants",
    "TypedRepairResult",
    "run_typed_repair_on_table",
    "TableKindResult",
    "attach_table_kind",
    "classify_table_kind",
    "should_run_structure_repair",
    "FacadeResult",
    "repair_for_ui",
    "repair_table_dict_with_facade",
    "repair_with_facade",
    "validate_repair",
    "HumanQueueItem",
    "collect_human_queue",
    "apply_queue_decisions",
    "store_llm_proposal",
]
