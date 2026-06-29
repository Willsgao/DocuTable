# -*- coding: utf-8 -*-
"""V2 Pipeline 编排器

将 8 个优化步骤按依赖关系串联，每步可独立启用/禁用。
"""

from pathlib import Path
from typing import List, Dict, Optional, Any, Callable

from .config import V2Config
from .models import PipelineContext, GridResult


class V2Pipeline:
    """V2 步骤编排器

    使用方式:
        pipeline = V2Pipeline()
        pipeline.disable("step2")           # 关闭合并检测
        pipeline.enable("step7")            # 开启表头树
        results = pipeline.run(pdf_path, max_pages=8)

    架构原则:
    - 每步只读 ctx，只写自己负责的字段
    - 步骤间无直接耦合，通过 ctx 传递数据
    - 失败隔离：单步异常不阻断整条流程
    """

    def __init__(self, config_path: Optional[str] = None):
        self._config = V2Config(config_path)
        # 默认：Step 1 开启，Step 2 参考用（不修改 table_data），其余待实施
        self._enabled: Dict[str, bool] = {
            "step1": True,
            "step2": True,     # 参考用，仅提供元数据
            "step3": True,     # 加权分类器 + needs_review
            "step4": True,     # LLM 智能路由
            "step5": True,     # 并行三通道提取
            "step6": True,     # 统一 TextItem 格式
            "step7": True,     # 表头树结构建模
            "step8": True,     # 质量评估 + cell 溯源
            "step_dedup": True,  # 相邻表格去重（防止两轮修正导致行重叠）
        }
        # Step 2 是否修改 table_data（默认不修改）
        self._step2_apply_merge: bool = False
        # 步骤执行日志
        self._log: List[Dict] = []

    # ---- 开关控制 ----

    def enable(self, step_name: str) -> None:
        """启用某个步骤"""
        if step_name in self._enabled:
            self._enabled[step_name] = True

    def disable(self, step_name: str) -> None:
        """禁用某个步骤"""
        if step_name in self._enabled:
            self._enabled[step_name] = False

    def is_enabled(self, step_name: str) -> bool:
        """查询步骤是否启用"""
        return self._enabled.get(step_name, False)

    def set_config(self, step_name: str, config: Dict[str, Any]) -> None:
        """覆盖某步的配置"""
        self._config._overrides[step_name.lower()] = config

    # ---- 主入口 ----

    def run(self,
            pdf_path: str,
            max_pages: Optional[int] = None,
            context: Any = None,
            progress_callback: Optional[Callable] = None,
            progress_base: int = 20,
            skip_drawings: bool = False) -> List[Dict]:
        """执行全管线

        Args:
            pdf_path: PDF 文件路径
            max_pages: 最大处理页数
            context: PDFContext 共享上下文（优先使用）
            progress_callback: callback(value, message) 逐页进度
            progress_base: 进度条起始值
            skip_drawings: 跳过 get_drawings()

        Returns:
            结果列表，每项包含 page/type/data/extractor/confidence 等字段
        """
        from .step1_column_split import Step1ColumnSplit  # noqa: F811 (ensure loaded)

        import fitz

        if context:
            doc = context.doc
            close_doc = False
        else:
            doc = fitz.open(pdf_path)
            close_doc = True

        total_pages = len(doc)
        if max_pages:
            total_pages = min(max_pages, total_pages)

        results: List[Dict] = []
        self._log = []

        for page_num in range(total_pages):
            page = doc[page_num]
            page_rect = page.rect

            if progress_callback:
                pct = progress_base + int((page_num + 1) / total_pages * 10)
                progress_callback(pct, f"V2扫描: 第{page_num + 1}/{total_pages}页")

            try:
                page_results = self._process_page(
                    pdf_path=pdf_path,
                    page_num=page_num,
                    page=page,
                    page_rect=page_rect,
                    skip_drawings=skip_drawings,
                )
                results.extend(page_results)
            except Exception as e:
                print(f"  [V2 Pipeline] 第{page_num + 1}页处理异常: {e}")
                import traceback
                traceback.print_exc()

        if close_doc:
            doc.close()

        return results

    def _process_page(self,
                      pdf_path: str,
                      page_num: int,
                      page: Any,
                      page_rect: Any,
                      skip_drawings: bool = False) -> List[Dict]:
        """处理单页：构建 ctx → 串行执行各步 → 收集结果"""
        import fitz

        # ---- 构建 PipelineContext ----
        ctx = PipelineContext(
            pdf_path=pdf_path,
            page_num=page_num + 1,
            page=page,
            page_rect=page_rect,
        )

        # 提取 words
        words_raw = page.get_text("words")
        ctx.words = []
        for w in words_raw:
            ctx.words.append({
                "x0": w[0], "y0": w[1],
                "x1": w[2], "y1": w[3],
                "text": w[4],
                "baseline": w[3],
            })

        # 提取 drawings
        if not skip_drawings:
            try:
                drawings_raw = page.get_drawings()
                for d in drawings_raw:
                    rect = d["rect"]
                    w_val = rect.width
                    h_val = rect.height
                    direction = None
                    if w_val > h_val * 5:
                        direction = "h"
                    elif h_val > w_val * 5:
                        direction = "v"
                    ctx.drawings.append({
                        "type": "line" if (w_val < h_val * 0.3 or h_val < w_val * 0.3) else "rect",
                        "direction": direction,
                        "x0": rect.x0, "y0": rect.y0,
                        "x1": rect.x1, "y1": rect.y1,
                        "color": d.get("color"),
                        "width": d.get("width", 1),
                        "fill": d.get("fill"),
                    })
            except Exception:
                print(f"  [V2] 第{page_num + 1}页: get_drawings() 失败，使用纯文本检测")

        # 回退：words 为空时尝试 dict 回退
        if not ctx.words:
            print(f"  [V2] 第{page_num + 1}页: get_text('words')返回空，尝试dict回退...")
            ctx.words = self._extract_words_from_dict(page)
            if ctx.words:
                print(f"  [V2] 第{page_num + 1}页: dict回退成功，提取到{len(ctx.words)}个文本片段")
            else:
                print(f"  [V2] 第{page_num + 1}页: dict回退也失败，跳过该页")
                return []

        # ---- Step 5: 并行多通道文本提取（增强 ctx.words）----
        if self._enabled.get("step5", True):
            self._run_step5(ctx, pdf_path, page_num)

        # ---- Step 6: 统一 TextItem 格式 ----
        if self._enabled.get("step6", True):
            self._run_step6(ctx, page_num)

        # 金融关键词过滤
        cfg = self._config.get("step1")
        full_text = " ".join(w["text"] for w in ctx.words)
        if not any(kw in full_text for kw in cfg.get("financial_keywords", [])):
            print(f"  [V2] 第{page_num + 1}页: 未匹配金融关键词，跳过")
            return []
        if len(full_text) < cfg.get("min_text_length", 50):
            print(f"  [V2] 第{page_num + 1}页: 文本长度不足，跳过")
            return []

        # ---- Step 1: 列切分 & 网格填充 ----
        from .step1_column_split import Step1ColumnSplit
        page_results: List[Dict] = []
        if self._enabled.get("step1", True):
            page_results = Step1ColumnSplit.execute(ctx, self._config.get("step1"))

        # ---- Step 2: 合并检测（参考用）----
        if self._enabled.get("step2", True) and page_results:
            self._run_step2(ctx, page_results)
        else:
            # Step2 禁用时也需规范化（补齐列 + 剔除首尾空行）
            for r in page_results:
                if r.get("type") == "table":
                    r["data"] = Step1ColumnSplit._normalize_table_columns(r["data"])
                    r["rows"] = len(r["data"])
                    r["cols"] = len(r["data"][0]) if r["data"] and r["data"][0] else 0

        # ---- Step 3: 加权分类器 + needs_review ----
        if self._enabled.get("step3", True) and page_results:
            self._run_step3(page_results)

        # ---- Step 4: LLM 智能路由 ----
        if self._enabled.get("step4", True) and page_results:
            self._run_step4(page_results)

        # ---- Step 7: 表头树结构建模 ----
        if self._enabled.get("step7", True):
            self._run_step7(page_results)

        # ---- Step 8: 质量评估 + cell 溯源 ----
        if self._enabled.get("step8", True) and page_results:
            self._run_step8(page_results)

        # ---- 第三轮：相邻表格去重 ----
        # 注意：如果 page_results 中的表格经过了 repair_and_split_tables 拆分，
        # 两个相邻子表可能因独立向上扫描表头而产生行级重叠。
        # 此步骤在 Pipeline 层面提供额外保护，对 page_results["data"] 列表进行去重。
        if self._enabled.get("step_dedup", True) and len(page_results) >= 2:
            self._run_step_dedup(page_results)

        return page_results

    def _run_step2(self, ctx: PipelineContext, page_results: List[Dict]) -> None:
        """执行 Step 2：合并单元格检测 + 规范化

        与 legacy 行为一致：
        1. Step2 检测并（可选）应用合并
        2. 规范化：补齐列 + 剔除首尾空行
        3. 更新 rows/cols
        """
        from .step2_merge_detect import Step2MergeDetect
        from .step1_column_split import Step1ColumnSplit

        cfg = self._config.get("step2")
        for idx, result in enumerate(page_results):
            if result.get("type") != "table":
                continue
            try:
                table_data = result["data"]
                row_bounds = result.get("_row_bounds", [])
                col_bounds = result.get("_col_bounds", [])
                modified_data, merge_info, stats = Step2MergeDetect.execute(
                    table_data, ctx.drawings, row_bounds, col_bounds, cfg,
                    apply_merge=self._step2_apply_merge,
                )
                if self._step2_apply_merge:
                    result["data"] = modified_data
                
                # 规范化（匹配 legacy 行为：补齐列 + 剔除首尾空行）
                result["data"] = Step1ColumnSplit._normalize_table_columns(result["data"])
                result["rows"] = len(result["data"])
                result["cols"] = len(result["data"][0]) if result["data"] and result["data"][0] else 0
                
                result["merge_info"] = merge_info
                result["merge_stats"] = stats
                if stats.get("total_spans", 0) > 0:
                    print(f"  [V2 Merge] 第{ctx.page_num}页: "
                          f"检测到 {stats['total_spans']} 个合并单元格")
            except Exception as e:
                print(f"  [V2 Merge] 第{ctx.page_num}页: 合并检测异常: {e}")

    def _run_step3(self, page_results: List[Dict]) -> None:
        """执行 Step 3：加权分类器 + needs_review 标记"""
        from .step3_classifier import Step3Classifier

        cfg = self._config.get("step3")
        weights = {
            "numeric_col_ratio": cfg.get("weight_numeric_col_ratio", 0.30),
            "data_rows": cfg.get("weight_data_rows", 0.20),
            "column_count": cfg.get("weight_column_count", 0.15),
            "toc_exclude": cfg.get("weight_toc_exclude", 0.15),
            "header_quality": cfg.get("weight_header_quality", 0.20),
        }
        thresholds = {
            "table": cfg.get("table_threshold", 0.65),
            "review": cfg.get("review_threshold", 0.45),
        }

        for result in page_results:
            if result.get("type") != "table":
                continue
            data = result.get("data", [])
            if not data:
                continue
            try:
                cr = Step3Classifier.classify(
                    data, page_num=result.get("page", 0),
                    weights=weights, thresholds=thresholds,
                )
                result["classify"] = cr.to_dict() if hasattr(cr, "to_dict") else {
                    "is_real_table": cr.is_real_table,
                    "confidence": cr.confidence,
                    "needs_review": cr.needs_review,
                    "weighted_score": cr.weighted_score,
                    "score_details": cr.score_details,
                    "reason": cr.reason,
                }
                if cr.needs_review:
                    print(f"  [V2 Classify] 第{result.get('page')}页: "
                          f"加权评分={cr.weighted_score:.2f}, 标记 needs_review")
            except Exception as e:
                print(f"  [V2 Classify] 分类异常: {e}")

    def _run_step4(self, page_results: List[Dict]) -> None:
        """执行 Step 4：LLM 智能路由

        基于 Step 3 的 needs_review 标记 + 异常信息，
        决策哪些表格需要 LLM 复核。
        """
        from .step4_llm_router import Step4LlmRouter

        # 收集本页所有表格的异常信息
        table_anomaly_list = []
        for result in page_results:
            if result.get("type") != "table":
                continue
            classify = result.get("classify", {})
            anomalies = result.get("anomalies", [])
            table_anomaly_list.append({
                "page": result.get("page", 0),
                "needs_review": classify.get("needs_review", False),
                "weighted_score": classify.get("weighted_score", 1.0),
                "anomalies": anomalies,
            })

        if not table_anomaly_list:
            return

        # 跨表批量路由
        batch_result = Step4LlmRouter.route_across_tables(table_anomaly_list)

        # 将路由结果附加到每页结果上
        for i, result in enumerate(page_results):
            if result.get("type") != "table":
                continue
            per_table = batch_result["per_table"][i] if i < len(batch_result["per_table"]) else {}
            classify = result.get("classify", {})
            result["llm_route"] = {
                "need_llm": per_table.get("need_llm_count", 0) > 0,
                "llm_count": per_table.get("need_llm_count", 0),
                "skip_count": per_table.get("skip_count", 0),
                "summary": per_table.get("summary", ""),
                "recommend_llm": (
                    classify.get("needs_review", False)
                    or per_table.get("need_llm_count", 0) > 0
                ),
            }
            if result["llm_route"]["recommend_llm"]:
                reason_parts = []
                if classify.get("needs_review"):
                    reason_parts.append(f"分类存疑(score={classify.get('weighted_score', 0):.2f})")
                if per_table.get("need_llm_count", 0) > 0:
                    reason_parts.append(f"语义异常×{per_table['need_llm_count']}")
                print(f"  [V2 LLMRoute] 第{result.get('page')}页: "
                      f"建议LLM复核 ({', '.join(reason_parts)})")

        # 全局汇总
        if batch_result["llm_count"] > 0:
            print(f"  [V2 LLMRoute] 本页汇总: {batch_result['total_anomalies']}处异常, "
                  f"需LLM {batch_result['llm_count']}处, 规则处理 {batch_result['total_anomalies'] - batch_result['llm_count']}处")

    def _run_step7(self, page_results: List[Dict]) -> None:
        """执行 Step 7：表头树结构建模

        对每个表格，通过数值列模式识别表头行，构建 HeaderNode 树，
        附加到 result["header_tree"]。
        """
        from .step7_header_tree import Step7HeaderTree

        for result in page_results:
            if result.get("type") != "table":
                continue
            data = result.get("data", [])
            if len(data) < 2:
                continue

            try:
                # 识别表头行：自上而下扫描，数值占比 < 30% = 表头行
                header_rows = self._detect_header_rows(data)
                if not header_rows or len(header_rows) < 1:
                    continue

                data_cols = len(data[0]) if data else 0
                tree = Step7HeaderTree.build_tree(header_rows, data_cols=data_cols)
                tree = Step7HeaderTree.fill_and_align(tree, data_cols=data_cols)

                result["header_tree"] = tree.to_dict()
                if len(header_rows) >= 2:
                    print(f"  [V2 HeaderTree] 第{result.get('page')}页: "
                          f"{len(header_rows)}级表头 → 树深度={tree._max_depth()}")
            except Exception as e:
                pass  # 静默失败，表头树为可选增强

    def _run_step8(self, page_results: List[Dict]) -> None:
        """执行 Step 8：质量评估 + cell 溯源

        对每个表格进行 4 维度质量评分，产出综合评级 A~E。
        """
        from .step8_quality_eval import Step8QualityEval

        tables = []
        for result in page_results:
            if result.get("type") != "table":
                continue
            tables.append({
                "page": result.get("page", 0),
                "data": result.get("data", []),
                "extractor": result.get("extractor", ""),
                "text_items": result.get("text_items", []),
            })

        if not tables:
            return

        try:
            reports = Step8QualityEval.evaluate_batch(tables)

            # 将评估结果附加到每页
            idx = 0
            for result in page_results:
                if result.get("type") != "table":
                    continue
                if idx < len(reports):
                    result["quality"] = reports[idx].to_dict()
                    grade = reports[idx].grade
                    score = reports[idx].overall_score
                    if grade in ("D", "E"):
                        print(f"  [V2 Quality] 第{result.get('page')}页: "
                              f"评级={grade}({score:.2f}) 质量低，建议检查")
                    idx += 1
        except Exception as e:
            print(f"  [V2 Quality] 评估异常: {e}")

    def _run_step_dedup(self, page_results: List[Dict]) -> None:
        """执行第三轮去重：检测相邻表格的行级重叠并清理。

        将 page_results 数据格式转换为 (table_data, repair_info) 元组列表，
        调用 deduplicate_adjacent_tables，然后将去重结果写回 page_results。
        """
        from codes.table_validator.rule_based_repair import deduplicate_adjacent_tables

        # 仅处理 table 类型的结果
        table_indices = [
            i for i, r in enumerate(page_results) if r.get("type") == "table" and r.get("data")
        ]
        if len(table_indices) < 2:
            return

        # 构造 (table_data, repair_info) 元组列表
        table_tuples = []
        for idx in table_indices:
            data = page_results[idx]["data"]
            # 构造一个轻量 repair_info（dedup 需要该字段来记录 overlap_removed）
            info = page_results[idx].get("repair_info", {})
            if not info:
                info = {"needed": False}
            table_tuples.append((data, info))

        # 调用去重
        deduped = deduplicate_adjacent_tables(table_tuples)

        # 写回结果
        for i, idx in enumerate(table_indices):
            page_results[idx]["data"] = deduped[i][0]

            # 记录去重信息（如果有）
            ri = deduped[i][1]
            if "overlap_removed" in ri:
                overlap = ri["overlap_removed"]
                page_results[idx].setdefault("repair_info", {})
                page_results[idx]["repair_info"]["overlap_removed"] = overlap
                print(f"  [V2 Dedup] 第{page_results[idx].get('page')}页"
                      f" 表{idx}: 去重移除 {overlap['count']} 行")

            # 更新 rows 计数
            page_results[idx]["rows"] = len(deduped[i][0])

    # ---- 工具方法：表头行检测（供 Step7 使用）----

    @staticmethod
    def _detect_header_rows(data: List[List[str]],
                            max_rows: int = 5) -> List[List[str]]:
        """从表格数据中检测表头行

        策略：自上而下扫描，数值列占比 < 30% 的视为表头行，
        遇到首个数据行（数值占比高）停止。

        Returns:
            表头行列表（保持原始顺序，从上到下）
        """
        def _is_num(v: str) -> bool:
            s = v.strip().rstrip('%').replace(',', '').replace(' ', '')
            if s.startswith('(') and s.endswith(')'):
                s = '-' + s[1:-1]
            try:
                float(s)
                return True
            except (ValueError, TypeError):
                return False

        def _row_num_ratio(row: List[str]) -> float:
            non_empty = [c for c in row if str(c).strip()]
            if not non_empty:
                return 0.0
            return sum(1 for c in non_empty if _is_num(c)) / len(non_empty)

        headers = []
        for i, row in enumerate(data[:min(max_rows, len(data))]):
            ratio = _row_num_ratio(row)
            if ratio < 0.30:
                headers.append([str(c).strip() for c in row])
            else:
                break  # 遇到数据行，停止

        return headers

    def _run_step5(self, ctx, pdf_path: str, page_num: int) -> None:
        """执行 Step 5：并行多通道文本提取

        在 PyMuPDF words 基础上，并行运行 pdfplumber 通道，
        融合去重后增强 ctx.words。
        """
        from .step5_triple_channel import Step5TripleChannel

        cfg = self._config.get("step5")
        extractor = Step5TripleChannel(cfg)

        try:
            enhanced_words, stats = extractor.extract_page(
                pdf_path=pdf_path,
                page_num=page_num,
                page=ctx.page,
            )

            # 确保 baseline 字段存在（兼容下游）
            pymupdf_count = sum(1 for w in enhanced_words if w.get("source") == "pymupdf")
            added = stats.get("pdfplumber_added", 0)
            total = stats["total"]

            ctx.words = enhanced_words
            ctx.metadata["step5_stats"] = stats

            if added > 0:
                print(f"  [V2 MultiCh] 第{page_num + 1}页: "
                      f"PyMuPDF={pymupdf_count}词 + pdfplumber补充={added}词 → 融合={total}词 "
                      f"({stats.get('time_ms', 0)}ms)")
            elif total > pymupdf_count:
                print(f"  [V2 MultiCh] 第{page_num + 1}页: "
                      f"融合={total}词 ({stats.get('time_ms', 0)}ms)")
        except Exception as e:
            # Step 5 失败不阻塞，降级使用原始 words
            print(f"  [V2 MultiCh] 第{page_num + 1}页: 多通道提取失败({e})，降级为单通道")

    def _run_step6(self, ctx, page_num: int) -> None:
        """执行 Step 6：统一 TextItem 格式

        将 ctx.words（原始 dict）转换为标准 TextItem 列表，
        并从 PyMuPDF span 补充 font_size / is_bold 信息。
        """
        from .step6_textitem_format import Step6TextItemFormat

        try:
            # 转换为 TextItem
            text_items = Step6TextItemFormat.from_dicts(
                ctx.words, source="pymupdf", page_num=page_num + 1)

            # 补充字体信息（is_bold 对表头检测有价值）
            if ctx.page is not None:
                text_items = Step6TextItemFormat.enrich_pymupdf_font(
                    text_items, ctx.page)

            ctx.text_items = text_items
            ctx.metadata["step6_stats"] = Step6TextItemFormat.summarize(text_items)

            # 打印 font 富化统计
            stats = ctx.metadata["step6_stats"]
            if stats.get("with_font_size", 0) > 0:
                print(f"  [V2 TextItem] 第{page_num + 1}页: "
                      f"{stats['count']} text_items, "
                      f"font富化={stats['with_font_size']}, "
                      f"粗体={stats['with_bold']}")
        except Exception as e:
            print(f"  [V2 TextItem] 第{page_num + 1}页: 格式标准化失败({e})，降级")

    # ---- 工具方法 ----

    @staticmethod
    def _extract_words_from_dict(page) -> List[dict]:
        """从 get_text('dict') 提取文本片段（PyMuPDF words 为空时的回退）"""
        from codes.pdf_extractor.processor import PDFProcessor
        return list(PDFProcessor._extract_words_from_dict(page))

    @staticmethod
    def _extract_context_text_from_words(words, x0, y0, x1, y1, margin=100.0) -> str:
        """提取表格区域上方的上下文文本"""
        context_words = [
            w for w in words
            if w["y1"] <= y0 and w["y1"] >= y0 - margin
            and w["x0"] >= x0 - 20 and w["x1"] <= x1 + 20
        ]
        context_words.sort(key=lambda w: w["y0"])
        return " ".join(w["text"] for w in context_words).strip()
