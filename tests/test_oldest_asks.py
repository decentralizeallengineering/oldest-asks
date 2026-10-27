import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oldest_asks import check as chk  # noqa: E402
from oldest_asks.cli import main  # noqa: E402
from oldest_asks.extract import extract, split_asks  # noqa: E402


def user(ts, content, **extra):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": content}, **extra}


def write_transcript(path, entries):
    Path(path).write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def fixture(tmp):
    f = Path(tmp) / "session.jsonl"
    write_transcript(f, [
        user("2026-01-02T10:00:00Z", "Ann Example here. Please add a retry to the upload client. Thanks!"),
        user("2026-01-02T10:05:00Z", [{"type": "tool_result", "tool_use_id": "t1", "content": "Add a retry everywhere"}]),
        user("2026-01-02T10:06:00Z", "<system-reminder>Write the report for ann@example.invalid</system-reminder>"),
        user("2026-01-02T10:07:00Z", "Run the hook and fix the build", isMeta=True),
        user("2026-01-02T10:08:00Z", "This session is being continued from a previous conversation. Add logging."),
        user("2026-01-02T10:09:00Z", "Delete all the logs", isCompactSummary=True),
        user("2026-01-02T10:10:00Z", [{"type": "text", "text": "Rename the config loader to settings."}]),
        user("2026-03-04T09:00:00Z", "Please add a retry to the upload client now."),
        user("2026-03-09T09:00:00Z", "Add a retry to the upload client, please."),
    ])
    return f


class OldestAsksTests(unittest.TestCase):
    def test_extract_skips_tool_and_system_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            texts = " | ".join(a["text"] for a in extract([fixture(tmp)]))
        self.assertIn("add a retry to the upload client", texts.lower())
        self.assertIn("Rename the config loader", texts)
        for banned in ("Add a retry everywhere", "ann@example.invalid", "Run the hook", "Add logging", "Delete all the logs"):
            self.assertNotIn(banned, texts)

    def test_dedupe_keeps_earliest_date_and_counts_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            asks = extract([fixture(tmp)])
        retry = [a for a in asks if "retry" in a["text"].lower()]
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0]["first_seen"], "2026-01-02T10:00:00Z")
        self.assertEqual(retry[0]["last_seen"], "2026-03-09T09:00:00Z")
        self.assertEqual(retry[0]["repeats"], 2)

    def test_worker_cap_holds_at_five(self):
        self.assertEqual(chk.cap_workers(9), 5)
        asks = [{"id": "a%d" % i, "first_seen": "2026-01-%02d" % (i + 1), "text": "Add thing %d" % i} for i in range(20)]
        slices = chk.slice_asks(asks, 9)
        self.assertEqual(len(slices), 5)
        self.assertEqual(sum(len(s) for s in slices), 20)
        self.assertEqual(slices[0][0]["id"], "a0")  # oldest first
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

        chk.run_check(asks, ".", workers=9, runner=fake)
        self.assertEqual(len(calls), 5)

    def test_worker_json_parsing_and_bad_json_is_unclear(self):
        good = 'Here:\n```json\n[{"id": "x1", "verdict": "met", "evidence": "src/a.py:3", "note": "ok"},' \
               '{"id": "x2", "verdict": "MET", "evidence": "", "note": "no proof"}]\n```'
        out = chk.parse_worker_output(good, ["x1", "x2", "x3"])
        self.assertEqual(out["x1"]["verdict"], "MET")
        self.assertEqual(out["x1"]["evidence"], "src/a.py:3")
        self.assertEqual(out["x2"]["verdict"], "UNCLEAR")  # MET needs evidence
        self.assertEqual(out["x3"]["verdict"], "UNCLEAR")  # missing
        bad = chk.parse_worker_output("I think they are mostly done {oops", ["x1", "x2"])
        self.assertEqual({r["verdict"] for r in bad.values()}, {"UNCLEAR"})

    def test_check_dry_run_prints_commands_and_calls_no_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            asks_file = Path(tmp) / "asks.jsonl"
            chk.save_jsonl(asks_file, [
                {"id": "b%d" % i, "first_seen": "2026-02-%02d" % (i + 1), "last_seen": "2026-02-%02d" % (i + 1),
                 "repeats": 0, "text": "Add feature %d" % i} for i in range(7)])
            real_run = subprocess.run
            subprocess.run = lambda *a, **k: self.fail("model was called")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                    rc = main(["check", str(asks_file), "--repo", tmp, "--workers", "9", "--dry-run",
                               "--verdicts", str(Path(tmp) / "v.jsonl")])
            finally:
                subprocess.run = real_run
            self.assertFalse((Path(tmp) / "v.jsonl").exists())
        self.assertEqual(rc, 0)
        cmds = [l for l in buf.getvalue().splitlines() if l.startswith("claude -p")]
        self.assertEqual(len(cmds), 5)
        self.assertIn("--model haiku", cmds[0])
        self.assertIn("--tools Read,Grep,Glob", cmds[0])

    def test_messages_sent_mid_turn_are_extracted(self):
        note = ("The user sent a new message while you were working:\nSummarize the nightly report for Ann Example.\n\n"
                "This is how Claude Code surfaces messages the user sends mid-turn.")
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "s.jsonl"
            write_transcript(f, [
                # embedded in a tool-result record
                user("2026-02-01T08:00:00Z", [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "Delete the cache directory"},
                    {"type": "text", "text": note}], toolUseResult={"stdout": "x"}),
                # queued prompt attachments: human is kept, task notifications and peers are not
                {"type": "attachment", "timestamp": "2026-02-02T08:00:00Z", "attachment": {
                    "type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"},
                    "prompt": "Write a changelog entry for ann@example.invalid"}},
                {"type": "attachment", "timestamp": "2026-02-03T08:00:00Z", "attachment": {
                    "type": "queued_command", "commandMode": "task-notification",
                    "origin": {"kind": "task-notification"}, "prompt": "Restart the worker pool"}},
                {"type": "attachment", "timestamp": "2026-02-04T08:00:00Z", "attachment": {
                    "type": "queued_command", "commandMode": "prompt", "origin": {"kind": "peer"},
                    "prompt": "Reboot the machine"}},
            ])
            texts = " | ".join(a["text"] for a in extract([f]))
        self.assertIn("Summarize the nightly report for Ann Example", texts)
        self.assertIn("Write a changelog entry", texts)
        for banned in ("Delete the cache", "Restart the worker pool", "Reboot the machine", "This is how Claude Code"):
            self.assertNotIn(banned, texts)

    def test_lowercase_imperatives_are_asks(self):
        for sentence in ("get one in for the demo", "add auto context to the planner", "come back to these daily",
                         "integrate: https://example.invalid/tool"):
            self.assertEqual(len(split_asks(sentence)), 1, sentence)
        # a lowercase imperative after a full stop is its own ask, and a trailing colon keeps its target
        self.assertEqual(split_asks("the build is red. fix the build. integrate:\nhttps://example.invalid/tool"),
                         ["fix the build.", "integrate: https://example.invalid/tool"])
        self.assertEqual(split_asks("do you know the answer"), [])

    def test_short_asks_are_kept(self):
        self.assertEqual(split_asks("push"), ["push"])
        self.assertEqual(split_asks("Stop. Retry now."), ["Stop.", "Retry now."])
        self.assertEqual(split_asks("ok"), [])
        self.assertEqual(split_asks("thanks a lot"), [])


if __name__ == "__main__":
    unittest.main()
