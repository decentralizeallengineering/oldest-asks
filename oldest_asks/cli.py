import argparse
import shlex
import sys
from pathlib import Path

from . import check as chk
from .extract import extract
from .report import build_report


def cmd_extract(a):
    asks = extract(a.paths, since=a.since, threshold=a.threshold)
    chk.save_jsonl(a.out, asks)
    print("wrote %d asks to %s" % (len(asks), a.out))
    return 0


def cmd_check(a):
    asks = chk.load_jsonl(a.asks)
    prior = chk.load_jsonl(a.verdicts)
    todo = asks if a.all else chk.pending(asks, prior)
    workers = chk.cap_workers(a.workers)
    if workers != a.workers:
        print("workers limited to %d (requested %d)" % (workers, a.workers), file=sys.stderr)
    slices = chk.slice_asks(todo, workers)
    if a.dry_run:
        for n, s in enumerate(slices, 1):
            prompt = chk.build_prompt(s, a.prompt)
            print("# worker %d: %d asks, cwd=%s, prompt on stdin (%d chars)" % (n, len(s), a.repo, len(prompt)))
            print(shlex.join(chk.build_command(a.model)))
            if a.show_prompt:
                print(prompt)
        if not slices:
            print("nothing to check")
        return 0
    results = chk.run_check(todo, a.repo, workers, a.model, a.timeout, a.prompt)
    chk.save_jsonl(a.verdicts, chk.merge_verdicts(prior, results))
    tally = {}
    for r in results.values():
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("checked %d asks with %d workers: %s" % (len(results), len(slices), tally or "none"))
    return 0


def cmd_report(a):
    text = build_report(chk.load_jsonl(a.asks), chk.load_jsonl(a.verdicts))
    Path(a.out).write_text(text, encoding="utf-8")
    print("wrote %s" % a.out)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="oldest-asks", description="Find asks that were never delivered, oldest first.")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="pull asks out of Claude Code transcripts")
    e.add_argument("paths", nargs="+", help="transcript .jsonl files or directories")
    e.add_argument("--since", help="only turns on or after this date or timestamp prefix, e.g. 2026-01-01")
    e.add_argument("--threshold", type=float, default=0.6, help="token Jaccard for near-duplicates (default 0.6)")
    e.add_argument("--out", default="asks.jsonl")
    e.set_defaults(fn=cmd_extract)

    c = sub.add_parser("check", help="fan asks out to read-only Haiku workers")
    c.add_argument("asks", help="asks.jsonl")
    c.add_argument("--repo", required=True, help="repository the workers search")
    c.add_argument("--workers", type=int, default=5, help="worker count, never more than %d" % chk.MAX_WORKERS)
    c.add_argument("--model", default="haiku")
    c.add_argument("--timeout", type=int, default=600, help="seconds per worker")
    c.add_argument("--verdicts", default="verdicts.jsonl")
    c.add_argument("--prompt", default=None, help="prompt template (default prompts/check.md)")
    c.add_argument("--all", action="store_true", help="re-check MET asks too")
    c.add_argument("--dry-run", action="store_true", help="print the commands, call no model")
    c.add_argument("--show-prompt", action="store_true", help="with --dry-run, also print each prompt")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("report", help="write the markdown ledger")
    r.add_argument("--asks", default="asks.jsonl")
    r.add_argument("--verdicts", default="verdicts.jsonl")
    r.add_argument("--out", default="ledger.md")
    r.set_defaults(fn=cmd_report)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.fn(a)
