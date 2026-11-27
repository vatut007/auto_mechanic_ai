#!/usr/bin/env python3
"""QA runner v0 — drives the core API with the reference question set.

Usage:
    uv run python scripts/qa_run.py --model deepseek-chat
    uv run python scripts/qa_run.py --model glm-5.4 --base-url http://127.0.0.1:8000

Writes a markdown report to data/qa_results/ and prints the table.
Exit code 1 if pass rate is below --min-pass (default 0.8).
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import sys

import httpx
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NEGATIVE_QWORDS = ("точн", "распиновк")  # hint, not used for grading


def ask(client: httpx.Client, base_url: str, model: str, question: str) -> tuple[str, int]:
    resp = client.post(
        f"{base_url}/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": question}], "temperature": 0.2},
        timeout=240,
    )
    if resp.status_code != 200:
        return f"HTTP {resp.status_code}: {resp.text[:200]}", 0
    data = resp.json()
    text = data["choices"][0]["message"]["content"] or ""
    tokens = (data.get("usage") or {}).get("total_tokens") or 0
    return text, tokens


def grade(question: dict, answer: str) -> tuple[bool, str]:
    low = answer.lower()
    hit = next((p for p in question["expect_any"] if p.lower() in low), None)
    if hit is None:
        return False, "expected marker not found"
    banned = [p for p in question.get("expect_not", []) if p.lower() in low]
    if banned:
        return False, f"forbidden content present: {banned}"
    return True, f"matched {hit!r}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="public model alias, e.g. deepseek-chat")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--min-pass", type=float, default=0.8)
    args = parser.parse_args()

    questions = yaml.safe_load((ROOT / "scripts" / "qa_questions.yaml").read_text(encoding="utf-8"))["questions"]

    rows: list[dict] = []
    with httpx.Client() as client:
        for i, q in enumerate(questions, 1):
            try:
                answer, tokens = ask(client, args.base_url, args.model, q["q"])
            except httpx.HTTPError as e:
                answer, tokens = f"transport error: {e}", 0
            ok, detail = grade(q, answer)
            rows.append({"n": i, "q": q["q"], "ok": ok, "detail": detail,
                         "answer": answer, "tokens": tokens})
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {i:2d}. {q['q'][:60]}… → {detail}")

    passed = sum(r["ok"] for r in rows)
    rate = passed / len(rows) if rows else 0.0

    out_dir = ROOT / "data" / "qa_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d__%H%M%S")
    report = out_dir / f"qa__{args.model.replace(':', '_')}__{stamp}.md"

    lines = [
        f"# QA run — {args.model}",
        f"",
        f"- When: {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"- Core: {args.base_url}",
        f"- Result: **{passed}/{len(rows)} ({rate:.0%})** — threshold {args.min_pass:.0%}",
        "",
        "| # | Question | Result | Check | Tokens |",
        "|--:|----------|:------:|-------|-------:|",
    ]
    for r in rows:
        q_short = r["q"].replace("|", "\\|")[:70]
        lines.append(f"| {r['n']} | {q_short} | {'✅' if r['ok'] else '❌'} | {r['detail']} | {r['tokens']} |")
    lines += ["", "## Full answers", ""]
    for r in rows:
        lines += [f"### {r['n']}. {r['q']}", "", f"{'**PASS**' if r['ok'] else '**FAIL**'} — {r['detail']}", "",
                  "```", r["answer"][:1500], "```", ""]
    report.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n{passed}/{len(rows)} passed ({rate:.0%}); report → {report.relative_to(ROOT)}")
    return 0 if rate >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(main())
