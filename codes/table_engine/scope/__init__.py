# -*- coding: utf-8 -*-
"""表 scope：region 上下界、gap 表头回补。"""

from codes.table_engine.scope.gap_capture import PageScopePlan, plan_page_scopes
from codes.table_engine.scope.header_scope import scope_y0_for_region
from codes.table_engine.scope.region_scope import TableScope, build_table_scope

__all__ = [
    "PageScopePlan",
    "TableScope",
    "build_table_scope",
    "plan_page_scopes",
    "scope_y0_for_region",
]
