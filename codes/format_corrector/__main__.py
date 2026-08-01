# -*- coding: utf-8 -*-
"""CLI：python -m codes.format_corrector --pdf <path> [--llm] [--apply] [--write-back]"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立表格格式纠错（不改动旧 AI 纠错流程）")
    parser.add_argument("--pdf", required=True, help="PDF 路径（用于定位 mid_cache）")
    parser.add_argument("--llm", action="store_true", help="启用 LLM 裁判（可选）")
    parser.add_argument("--apply", action="store_true", help="自动应用高置信提案")
    parser.add_argument("--write-back", action="store_true", help="将结果写回 data.json（需同时 --apply）")
    parser.add_argument("--accepted", default="", help="逗号分隔的 task_id，显式接受后应用")
    parser.add_argument("--json-out", default="", help="将报告写到指定 JSON 路径")
    args = parser.parse_args(argv)

    from codes.format_corrector import FormatCorrectorEngine
    from codes.format_corrector.liteparse_bridge import load_tables_from_mid, load_liteparse_dict

    engine = FormatCorrectorEngine(
        args.pdf,
        use_llm=args.llm,
        auto_apply=False,
    )
    report = engine.run_from_pdf_cache()

    tables, payload = load_tables_from_mid(args.pdf)
    liteparse = load_liteparse_dict(args.pdf)

    if args.apply or args.accepted:
        accepted = set(x.strip() for x in args.accepted.split(",") if x.strip()) or None
        new_tables, report = engine.apply(
            tables,
            report,
            only_auto=bool(args.apply) and not accepted,
            accepted_ids=accepted,
            liteparse_data=liteparse,
        )
        if args.write_back:
            path = engine.write_back_mid_cache(new_tables, payload=payload)
            report.notes.append(f"已写回: {path}")
        report.summary["result_table_count"] = len(new_tables)

    out = report.to_dict()
    # 精简打印
    print(json.dumps({
        "summary": out["summary"],
        "task_count": len(out["tasks"]),
        "tasks": [
            {
                "id": t["task_id"],
                "type": t["task_type"],
                "table": t["table_index"],
                "status": t["status"],
                "confidence": t["confidence"],
                "reason": t["reason"],
            }
            for t in out["tasks"]
        ],
        "notes": out["notes"],
    }, ensure_ascii=False, indent=2))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[format_corrector] report -> {args.json_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
