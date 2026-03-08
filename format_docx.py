# format_docx.py

import argparse
import os
from config import LLM_MODE
from service.format_service import format_docx_file


def main():
    parser = argparse.ArgumentParser(description="Format DOCX with report output")
    parser.add_argument("input", help="input docx")
    parser.add_argument("output", help="output docx")
    parser.add_argument("--spec", default="specs/default.yaml", help="spec yaml path")
    parser.add_argument("--report", default=None, help="report json output path")
    parser.add_argument("--no-report", action="store_true", help="disable report file writing")
    parser.add_argument(
        "--label-mode",
        default=LLM_MODE,
        choices=["hybrid", "react"],
        help="label mode: hybrid (规则+LLM校对) / react (ReAct 迭代 Agent)",
    )
    args = parser.parse_args()

    if args.label_mode == "react":
        from agent.graph.workflow import run_react_agent
        from config import REACT_MAX_ITERS
        result_state = run_react_agent(
            args.input,
            args.output,
            spec_path=args.spec,
            max_iters=REACT_MAX_ITERS,
        )
        report = result_state.get("report", {})
        if args.report and not args.no_report:
            import json
            report_dir = os.path.dirname(args.report) or "."
            os.makedirs(report_dir, exist_ok=True)
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"✅ Done: {args.output}")
    else:
        result = format_docx_file(
            args.input,
            args.output,
            spec_path=args.spec,
            report_path=args.report,
            write_report=not args.no_report,
            label_mode=args.label_mode,
        )
        print(f"✅ Done: {result.output_path}")
        if result.report_path:
            print(f"🧾 Report: {result.report_path}")


if __name__ == "__main__":
    main()
