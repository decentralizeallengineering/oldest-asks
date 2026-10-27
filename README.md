# oldest-asks

Finds what a person asked their agents for that was never delivered, oldest first. It reads Claude Code session
transcripts, pulls out the individual asks, and has a small fleet of read-only Haiku workers check each ask against a
repository. A repeated ask is a strong sign it was never delivered. oldest-asks only calls the `claude` command line
tool: it contains no model and trains nothing. Standard library only, Python 3.9 or newer.

The fleet is never larger than 5 workers. The cap is enforced in code (`MAX_WORKERS` in `oldest_asks/check.py`), so
`--workers 9` runs 5.

## Install

    git clone <this repo> && cd oldest-asks && chmod +x oldest-asks && ln -s "$PWD/oldest-asks" ~/.local/bin/oldest-asks

The `claude` command must be on your `PATH` and logged in for `check`.

## Use

    # 1. Collect asks from transcripts (files or directories of .jsonl), oldest wins, repeats are counted
    oldest-asks extract ~/.claude/projects/my-project --since 2026-01-01 --out asks.jsonl

    # 2. See the exact commands first, then run the workers
    oldest-asks check asks.jsonl --repo ~/code/my-project --workers 5 --dry-run
    oldest-asks check asks.jsonl --repo ~/code/my-project --workers 5

    # 3. Write the ledger
    oldest-asks report --out ledger.md

`asks.jsonl` holds `{id, first_seen, last_seen, repeats, text}`; `repeats` is how many later times the same ask came
up (near-duplicates are merged by token Jaccard, default threshold 0.6, `--threshold` to change). Only genuine human
turns are kept: tool results, system reminders, hook text, sidechain turns and compaction summaries are skipped.

Each worker is `claude -p --model haiku --tools Read,Grep,Glob --allowedTools Read,Grep,Glob` with its working
directory at the repo and the fixed prompt in `prompts/check.md`. It returns `{id, verdict, evidence, note}` with
verdict MET, PARTIAL, UNMET or UNCLEAR. MET requires evidence (`file:line` or a commit); invalid JSON, a missing
answer, a timeout or a MET without evidence is recorded as UNCLEAR and never guessed.

Verdicts go to `verdicts.jsonl`. Running `check` again keeps MET verdicts and re-checks only asks that are UNMET,
PARTIAL, UNCLEAR or new (`--all` re-checks everything). Every check is appended to that ask's history, which the
ledger shows. The ledger lists UNMET asks first, then PARTIAL, oldest first, with the overall share of MET, PARTIAL and
UNMET and the repeat counts.

Options for `check`: `--model` (default `haiku`), `--timeout` seconds per worker (default 600), `--verdicts`,
`--prompt`, `--show-prompt` with `--dry-run`.

## The same fan-out as a Claude Code subagent recipe

Instead of the `check` command, a parent Claude Code session can do the fan-out itself. After `extract`, ask the
parent session:

    Read asks.jsonl and sort it by first_seen, oldest first. Cut it into at most 5 contiguous slices of near-equal
    size; never use more than 5. In ONE message, start one subagent per slice in parallel, each with model haiku and
    only the tools Read, Grep and Glob. Give each subagent the full text of prompts/check.md with {{ASKS}} replaced
    by its slice (one JSON object per line). Collect the JSON arrays they return. Treat any reply that is not valid
    JSON, or a MET without evidence, as UNCLEAR. Append the results to verdicts.jsonl.

Or define the worker once as a subagent in `.claude/agents/ask-checker.md` and tell the parent to spawn it up to 5 times:

    ---
    name: ask-checker
    description: Checks whether a slice of old asks was delivered in this repository
    tools: Read, Grep, Glob
    model: haiku
    ---
    Follow the instructions in prompts/check.md. The asks to check are in the message you receive.

## Test

    python3 -m pytest

## License

Apache-2.0, see `LICENSE` and `NOTICE`.
