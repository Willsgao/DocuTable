# -*- coding: utf-8
"""Layout 插件注册与选择。"""

from __future__ import annotations

from typing import List, Tuple

from codes.table_engine.config import default_config
from codes.table_engine.layout.base import LayoutContext, LayoutPlugin, LayoutSelection
from codes.table_engine.layout.generic import GenericLayoutPlugin
from codes.table_engine.layout.pillar_cc1 import CC1LayoutPlugin
from codes.table_engine.layout.pillar_cc2 import CC2LayoutPlugin
from codes.table_engine.layout.constraint_grid import ConstraintGridLayoutPlugin
from codes.table_engine.layout.pillar_ccrf import CCRFLayoutPlugin
from codes.table_engine.layout.pillar_disclosure import DisclosureLayoutPlugin
from codes.table_engine.layout.pillar_gsib import GSIBLayoutPlugin
from codes.table_engine.layout.pillar_dsib import DSIBLayoutPlugin
from codes.table_engine.layout.pillar_sec1 import SEC1LayoutPlugin

_PLUGINS: List[LayoutPlugin] = [
    SEC1LayoutPlugin(),
    CCRFLayoutPlugin(),
    DSIBLayoutPlugin(),
    GSIBLayoutPlugin(),
    DisclosureLayoutPlugin(),
    CC2LayoutPlugin(),
    CC1LayoutPlugin(),
    ConstraintGridLayoutPlugin(),
    GenericLayoutPlugin(),
]

_PLUGIN_BY_ID = {p.layout_id: p for p in _PLUGINS}


def all_plugins() -> List[LayoutPlugin]:
    return list(_PLUGINS)


def select_layout(
    ctx: LayoutContext,
    *,
    score_threshold: float | None = None,
) -> Tuple[LayoutSelection, LayoutPlugin]:
    """按 score 选最佳插件；低于阈值回退 generic。"""
    threshold = score_threshold
    if threshold is None:
        threshold = default_config().layout_score_threshold

    best_plugin = GenericLayoutPlugin()
    best_sel = best_plugin.infer(ctx)
    best_score = best_plugin.score(ctx)

    for plugin in _PLUGINS:
        if plugin.layout_id == "generic":
            continue
        s = plugin.score(ctx)
        if s <= best_score:
            continue
        sel = plugin.infer(ctx)
        if sel is None:
            continue
        best_score = s
        best_plugin = plugin
        best_sel = sel

    if best_sel is None or best_score < threshold:
        generic = GenericLayoutPlugin()
        sel = generic.infer(ctx)
        assert sel is not None
        return sel, generic

    assert best_sel is not None
    return best_sel, best_plugin


def plugin_for_layout_id(layout_id: str) -> LayoutPlugin:
    return _PLUGIN_BY_ID.get(layout_id, GenericLayoutPlugin())
