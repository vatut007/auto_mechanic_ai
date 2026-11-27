"""CLI: uv run python -m app.pipeline ingest | status"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __doc__ as _  # keep package doc near CLI  # noqa: F401
from .ingest import PipelineBusy, DEFAULT_DATA, run_ingest, status
from .manifest import Manifest


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.pipeline", description="Document pipeline (Stage 2)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="parse new PDFs from inbox/, store artifacts, move accepted files to library/")
    p_ingest.add_argument("--inbox", type=Path, default=DEFAULT_DATA / "inbox")
    p_ingest.add_argument("--data", type=Path, default=DEFAULT_DATA)

    p_status = sub.add_parser("status", help="manifest summary: counts, needs_review, last run")
    p_status.add_argument("--data", type=Path, default=DEFAULT_DATA)

    args = parser.parse_args()

    if args.cmd == "ingest":
        try:
            report = run_ingest(inbox=args.inbox, data_root=args.data)
        except PipelineBusy as e:
            print(f"ОТМЕНА: {e}", file=sys.stderr)
            return 2
        print(f"Прогон: scanned={report.scanned} new={report.new} duplicates={report.duplicates} "
              f"indexed={report.indexed} needs_review={report.needs_review} failed={report.failed}")
        for item in report.items:
            line = f"  [{item['status']:>12}] {item['file']}  ({item.get('sha256', '')})"
            if "meta" in item and any(item["meta"].values()):
                line += "  meta=" + "/".join(str(v) for v in item["meta"].values() if v)
            if item.get("reason"):
                line += f"  — {item['reason']}"
            print(line)
        return 0

    if args.cmd == "status":
        s = status(data_root=args.data)
        print("Манифест:", s["counts_by_status"] or "пусто")
        if s["needs_review"]:
            print("Ждут разбора (needs_review):")
            for r in s["needs_review"]:
                print(f"  - {r['original_name']} ({r['doc_id']}) — {r['reason']}")
        if s["last_run"]:
            lr = s["last_run"]
            print("Последний прогон:", {k: lr[k] for k in
                  ("started", "scanned", "new", "duplicates", "indexed", "needs_review", "failed") if lr.get(k) is not None})
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
