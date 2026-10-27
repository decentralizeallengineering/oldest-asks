"""Fan asks out to a small fleet of headless, read-only Claude Code workers."""

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

MAX_WORKERS = 5  # hard ceiling, never exceeded whatever the caller asks for
VERDICTS = ("MET", "PARTIAL", "UNMET", "UNCLEAR")
TOOLS = "Read,Grep,Glob"
DEFAULT_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "check.md"


def cap_workers(requested):
    """Clamp a requested worker count to 1..MAX_WORKERS."""
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        requested = 1
    return max(1, min(requested, MAX_WORKERS))


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slice_asks(asks, n):
    """Sort oldest first and cut into at most n contiguous, near-equal slices."""
    asks = sorted(asks, key=lambda a: (a["first_seen"], a["id"]))
    n = min(cap_workers(n), len(asks)) if asks else 0
    if n == 0:
        return []
    size, extra = divmod(len(asks), n)
    out, start = [], 0
    for i in range(n):
        end = start + size + (1 if i < extra else 0)
        out.append(asks[start:end])
        start = end
    return out


def build_command(model="haiku"):
    """Headless, read-only worker. The prompt is passed on stdin."""
    return [
        "claude", "-p",
        "--model", model,
        "--tools", TOOLS,
        "--allowedTools", TOOLS,
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
    ]


def build_prompt(slice_, template_path=None):
    template = Path(template_path or DEFAULT_PROMPT).read_text(encoding="utf-8")
    lines = [
        json.dumps({"id": a["id"], "first_seen": a["first_seen"], "repeats": a.get("repeats", 0), "text": a["text"]})
        for a in slice_
    ]
    return template.replace("{{ASKS}}", "\n".join(lines))


def _unclear(ids, note):
    return {i: {"id": i, "verdict": "UNCLEAR", "evidence": "", "note": note} for i in ids}


def _json_values(text):
    """Yield every JSON array or object found in free text."""
    dec = json.JSONDecoder()
    pos = 0
    while True:
        m = re.search(r"[\[{]", text[pos:])
        if not m:
            return
        pos += m.start()
        try:
            val, end = dec.raw_decode(text, pos)
        except ValueError:
            pos += 1
            continue
        yield val
        pos = end


def parse_worker_output(text, expected_ids):
    """Map id -> result. Anything missing, malformed or unproven is UNCLEAR, never guessed."""
    expected = list(expected_ids)
    found = {}
    for val in _json_values(text or ""):
        for item in val if isinstance(val, list) else [val]:
            if isinstance(item, dict) and item.get("id") in expected:
                found.setdefault(item["id"], item)
    if not found:
        return _unclear(expected, "worker output was not valid JSON")
    out = {}
    for i in expected:
        item = found.get(i)
        if item is None:
            out[i] = _unclear([i], "worker returned no verdict for this ask")[i]
            continue
        verdict = str(item.get("verdict", "")).strip().upper()
        evidence = str(item.get("evidence") or "").strip()
        note = str(item.get("note") or "").strip()
        if verdict not in VERDICTS:
            verdict, note = "UNCLEAR", "worker gave an unknown verdict"
        elif verdict == "MET" and not evidence:
            verdict, note = "UNCLEAR", "MET without evidence is not accepted"
        out[i] = {"id": i, "verdict": verdict, "evidence": evidence, "note": note}
    return out


def run_worker(slice_, repo, model="haiku", timeout=600, template_path=None, runner=subprocess.run):
    ids = [a["id"] for a in slice_]
    try:
        proc = runner(
            build_command(model),
            input=build_prompt(slice_, template_path),
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _unclear(ids, "worker timed out")
    except OSError as e:
        return _unclear(ids, "worker could not start: %s" % e.__class__.__name__)
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        return _unclear(ids, "worker exited with status %s" % proc.returncode)
    return parse_worker_output(proc.stdout, ids)


def run_check(asks, repo, workers=5, model="haiku", timeout=600, template_path=None, runner=subprocess.run):
    """Run the slices in parallel (never more than MAX_WORKERS threads). Returns id -> result."""
    slices = slice_asks(asks, workers)
    if not slices:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=min(len(slices), MAX_WORKERS)) as pool:
        futures = [pool.submit(run_worker, s, repo, model, timeout, template_path, runner) for s in slices]
        for f in futures:
            results.update(f.result())
    return results


def load_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def merge_verdicts(prior, results, stamp=None):
    """prior: list of stored verdict rows. Appends each new result to its history."""
    stamp = stamp or now()
    by_id = {r["id"]: r for r in prior}
    for i, res in results.items():
        row = by_id.get(i) or {"id": i, "history": []}
        entry = {"verdict": res["verdict"], "evidence": res["evidence"], "note": res["note"], "checked_at": stamp}
        row.update(entry)
        row["history"] = row.get("history", []) + [entry]
        by_id[i] = row
    return list(by_id.values())


def pending(asks, prior):
    """Asks still to check: never checked, or last verdict was not MET."""
    done = {r["id"] for r in prior if r.get("verdict") == "MET"}
    return [a for a in asks if a["id"] not in done]
