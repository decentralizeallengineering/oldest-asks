"""Pull genuine human asks out of Claude Code session transcripts (JSONL)."""

import hashlib
import json
import re
from pathlib import Path

# Text that looks like a user turn but is injected by the harness.
_BLOCK_RES = [
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S),
    re.compile(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", re.S),
    re.compile(r"<command-(?:name|message|args)>.*?</command-(?:name|message|args)>", re.S),
    re.compile(r"<local-command-(?:stdout|stderr|caveat)>.*?</local-command-(?:stdout|stderr|caveat)>", re.S),
    re.compile(r"<task-notification>.*?</task-notification>", re.S),
    re.compile(r"```.*?```", re.S),
]
_SKIP_PREFIXES = (
    "caveat:",
    "this session is being continued",
    "[request interrupted",
    "<system-reminder>",
    "stop hook feedback",
)

_VERBS = set(
    """add allow ask audit make build create write implement fix change update remove delete rename move
    check verify test run show list print find search look read open close start stop restart enable disable
    set use switch replace refactor clean install configure document explain describe summarize review merge
    commit push pull deploy ship release publish send tell give get let keep ensure try consider include
    support handle parse convert generate wire hook connect log track save store load fetch download upload
    reduce increase improve optimize speed split join combine dedupe sort filter validate prevent avoid
    stop fail return expose export import integrate port migrate rewrite revert undo
    please need want should must""".split()
)
_ASK_RE = re.compile(
    r"\b(please|can you|could you|would you|will you|i want|i need|i would like|i'd like|we need|"
    r"we should|you should|make sure|don't forget|do not forget|todo)\b",
    re.I,
)
_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")
_BULLET_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def tokens(text):
    return set(_TOKEN_RE.findall(text.lower()))


def jaccard(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def ask_id(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def clean_text(text):
    for rx in _BLOCK_RES:
        text = rx.sub(" ", text)
    return text.strip()


def human_text(entry):
    """Return the text of a genuine human user turn, or None."""
    if entry.get("type") != "user":
        return None
    if entry.get("isMeta") or entry.get("isSidechain") or entry.get("isCompactSummary"):
        return None
    if entry.get("isVisibleInTranscriptOnly") or entry.get("toolUseResult") is not None:
        return None
    msg = entry.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                return None
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
    else:
        return None
    text = clean_text("\n".join(parts))
    if not text or text.lower().startswith(_SKIP_PREFIXES):
        return None
    return text


def looks_like_ask(sentence):
    words = re.findall(r"[A-Za-z']+", sentence)
    if len(words) < 3:
        return False
    first = words[0].lower()
    if first in _VERBS:
        return True
    if _ASK_RE.search(sentence):
        return True
    return sentence.rstrip().endswith("?") and first in {"can", "could", "would", "will", "why", "how", "what", "is", "are", "does", "do"}


def split_asks(text):
    """Split one message into individual asks using imperative-sentence heuristics."""
    out = []
    for piece in _SPLIT_RE.split(text):
        piece = _BULLET_RE.sub("", piece or "").strip()
        if piece and looks_like_ask(piece):
            out.append(piece)
    return out


def dedupe(items, threshold=0.6):
    """items: iterable of (timestamp, text). Merge near-duplicates by token Jaccard.

    The earliest timestamp and its wording are kept; `repeats` counts the later
    times the same ask came up.
    """
    merged = []
    for ts, text in sorted(items, key=lambda it: it[0]):
        toks = tokens(text)
        for m in merged:
            if jaccard(toks, m["_tok"]) >= threshold:
                m["repeats"] += 1
                m["last_seen"] = max(m["last_seen"], ts)
                break
        else:
            merged.append({"first_seen": ts, "last_seen": ts, "repeats": 0, "text": text, "_tok": toks})
    result = []
    for m in merged:
        m.pop("_tok")
        result.append({"id": ask_id(m["text"]), **m})
    return result


def iter_files(paths):
    for p in paths:
        p = Path(p)
        if p.is_dir():
            yield from sorted(p.rglob("*.jsonl"))
        elif p.is_file():
            yield p


def collect(paths, since=None):
    """Return (timestamp, ask text) for every ask in the transcripts."""
    items = []
    for f in iter_files(paths):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                text = human_text(entry)
                if text is None:
                    continue
                ts = str(entry.get("timestamp") or "")
                if since and ts[: len(since)] < since:
                    continue
                for ask in split_asks(text):
                    items.append((ts, ask))
    return items


def extract(paths, since=None, threshold=0.6):
    return dedupe(collect(paths, since), threshold)
