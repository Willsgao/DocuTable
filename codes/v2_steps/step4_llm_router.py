# -*- coding: utf-8 -*-
"""
Step 4: LLM 智能路由

决策哪些异常需要调 LLM，哪些规则足以处理。
目标：减少 LLM API 调用量，节省费用。

路由策略：
1. 高置信度（>0.8）→ 规则已自行解决，跳过
2. 纯位置问题（锚定偏移、弱锚定）→ 规则足够，跳过
3. 语义问题（文本断裂、层级推理、多表合并）→ 需要 LLM
4. 其余 → 按置信度 + 严重程度综合判断

设计原则：
- 本模块只做路由决策，不实际调用 LLM
- 把"需 LLM 的异常"收集起来，留给上层（validator / UI）批量提交
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

# 复用 table_structure_repair 的异常类型常量
from codes.table_validator.table_structure_repair import (
    ANCHOR_SHIFT,
    WEAK_ANCHOR,
    HEADER_TEXT_MISSING,
    DATA_HEADER_MISMATCH,
    ORPHAN_HEADER_TEXT,
    TRUNCATED_HEADER_MERGED,
    MULTI_TABLE_MERGED,
    ANOMALY_HIGH,
    ANOMALY_MEDIUM,
    ANOMALY_LOW,
)


# ============================================================
# 异常分类：哪些类型纯规则可处理、哪些必须 LLM
# ============================================================

# 纯位置/规则问题 — 规则引擎已自行修复，无需 LLM 复核
POSITIONAL_ANOMALY_TYPES = {ANCHOR_SHIFT, WEAK_ANCHOR}

# 语义/结构理解问题 — 规则只能猜测，需要 LLM 语义推理
SEMANTIC_ANOMALY_TYPES = {
    TRUNCATED_HEADER_MERGED,   # 表头合并后可能合错了
    HEADER_TEXT_MISSING,       # 表头文字缺失，规则推断可能不准
    ORPHAN_HEADER_TEXT,        # 孤立表头文字无法归位
    MULTI_TABLE_MERGED,        # 多表合并需要语义判断边界
    DATA_HEADER_MISMATCH,      # 数据列与表头结构不匹配
}


# ============================================================
# 路由结果
# ============================================================

@dataclass
class RouteResult:
    """单表的路由决策结果"""
    page: int = 0
    total_anomalies: int = 0
    need_llm: List[Dict[str, Any]] = field(default_factory=list)
    """需要 LLM 处理的异常列表"""
    skip: List[Dict[str, Any]] = field(default_factory=list)
    """跳过（规则已处理）的异常列表"""
    skip_reasons: Dict[int, str] = field(default_factory=dict)
    """跳过的原因（按索引）"""
    summary: str = ""
    """人类可读的摘要"""

    @property
    def should_call_llm(self) -> bool:
        """是否需要调用 LLM"""
        return len(self.need_llm) > 0

    @property
    def llm_saved_count(self) -> int:
        """节省的 LLM 调用次数（相对于全量调用）"""
        return len(self.skip)

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "total_anomalies": self.total_anomalies,
            "need_llm_count": len(self.need_llm),
            "skip_count": len(self.skip),
            "need_llm": [
                {"type": a.get("type"), "severity": a.get("severity"),
                 "description": a.get("description", "")}
                for a in self.need_llm
            ],
            "skip_reasons": self.skip_reasons,
            "summary": self.summary,
        }


# ============================================================
# 路由决策器
# ============================================================

class Step4LlmRouter:
    """LLM 智能路由（V2 Step 4）

    使用方式：
        # 单个异常判断
        if Step4LlmRouter.should_invoke_llm(anomaly):
            llm_results.append(anomaly)

        # 批量路由
        result = Step4LlmRouter.route_anomalies(anomalies, page_num=1)
        if result.should_call_llm:
            # 提交 result.need_llm 给 LLM
            ...
    """

    @staticmethod
    def should_invoke_llm(anomaly: Dict[str, Any]) -> bool:
        """判断单个异常是否需要 LLM 处理

        Args:
            anomaly: 异常字典，至少含 type, severity, confidence 字段

        Returns:
            True = 需要调 LLM, False = 规则足够
        """
        anomaly_type = anomaly.get("type", "")
        confidence = anomaly.get("confidence", 0.5)
        severity = anomaly.get("severity", ANOMALY_LOW)

        # 规则1: 高置信度 → 规则自行解决
        if confidence > 0.8:
            return False

        # 规则2: 纯位置问题 → 规则足够
        if anomaly_type in POSITIONAL_ANOMALY_TYPES:
            return False

        # 规则3: 语义问题 → 必须 LLM
        if anomaly_type in SEMANTIC_ANOMALY_TYPES:
            return True

        # 规则4: 中等置信度 + 非低严重度 → 需要 LLM
        if confidence < 0.6 and severity != ANOMALY_LOW:
            return True

        # 默认跳过（低严重度 或 规则可处理）
        return False

    @staticmethod
    def route_anomalies(anomalies: List[Dict[str, Any]],
                        page_num: int = 0) -> RouteResult:
        """对一批异常做批量路由决策

        Args:
            anomalies: 异常字典列表（通常来自 table_structure_repair 输出）
            page_num: 页码

        Returns:
            RouteResult 包含 need_llm / skip 分组
        """
        result = RouteResult(page=page_num, total_anomalies=len(anomalies))

        for i, a in enumerate(anomalies):
            anomaly_type = a.get("type", "unknown")
            confidence = a.get("confidence", 0.5)
            severity = a.get("severity", ANOMALY_LOW)

            if Step4LlmRouter.should_invoke_llm(a):
                result.need_llm.append(a)
            else:
                result.skip.append(a)
                # 生成跳过原因
                if confidence > 0.8:
                    result.skip_reasons[i] = f"高置信度({confidence:.0%})，规则自行解决"
                elif anomaly_type in POSITIONAL_ANOMALY_TYPES:
                    result.skip_reasons[i] = f"纯位置问题({anomaly_type})，规则已处理"
                elif severity == ANOMALY_LOW and confidence >= 0.6:
                    result.skip_reasons[i] = f"低严重度+中置信度，规则可控"
                else:
                    result.skip_reasons[i] = f"置信度{confidence:.0%}，规则可处理"

        # 生成摘要
        parts = []
        if result.need_llm:
            parts.append(f"需LLM: {len(result.need_llm)}处")
            # 列出类型
            type_counts: Dict[str, int] = {}
            for a in result.need_llm:
                t = a.get("type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
            parts.append("(" + ", ".join(f"{t}×{c}" for t, c in type_counts.items()) + ")")
        if result.skip:
            parts.append(f"规则已处理: {len(result.skip)}处")
        result.summary = "; ".join(parts) if parts else "无异常"

        return result

    @staticmethod
    def route_across_tables(table_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """跨多表的异常批量路由（用于同一文档的批量 LLM 提交）

        将多个表格的异常汇总，统一决策哪些需要 LLM。

        Args:
            table_results: 每项含 page / anomalies 字段的表格结果列表

        Returns:
            {
                "total_tables": int,
                "total_anomalies": int,
                "need_llm": [...],      # 所有需要 LLM 的异常（扁平列表）
                "llm_count": int,         # 节省的调用次数
                "per_table": [RouteResult, ...],  # 每表的路由结果
            }
        """
        all_need_llm: List[Dict] = []
        per_table: List[RouteResult] = []
        total_anomalies = 0

        for tr in table_results:
            anomalies = tr.get("anomalies", [])
            if anomalies:
                rr = Step4LlmRouter.route_anomalies(
                    anomalies, page_num=tr.get("page", 0))
            else:
                rr = RouteResult(page=tr.get("page", 0), total_anomalies=0)
            per_table.append(rr)
            total_anomalies += rr.total_anomalies
            all_need_llm.extend(rr.need_llm)

        return {
            "total_tables": len(table_results),
            "total_anomalies": total_anomalies,
            "need_llm": all_need_llm,
            "llm_count": len(all_need_llm),
            "per_table": [r.to_dict() for r in per_table],
        }
