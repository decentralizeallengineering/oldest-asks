"""Pull genuine human asks out of Claude Code session transcripts (JSONL)."""

import hashlib
import json
import re
from datetime import datetime
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
    come take bring put go do wait hold ping remind report schedule monitor watch continue proceed
    resume retry rerun re-run rebuild redo drop skip ignore pause note remember double-check recheck
    progress vet notify shorten work understand integrate push pull audit trace compare diff grep cat tail head
    never always don't dont
    please need want should must""".split()
)
_PRONOUNS = {"you", "we", "they", "it", "i", "he", "she"}
_ASK_RE = re.compile(
    r"\b(please|can you|could you|would you|will you|i want|i need|i would like|i'd like|we need|"
    r"we should|you should|make sure|don't forget|do not forget|todo|needs? to|have to|must|should|"
    r"how (?:do|can|could|would|should)|how i)\b",
    re.I,
)
_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[]|(?:%s)\b)|\n+" % "|".join(sorted(_VERBS - {"please", "need", "want", "should", "must"}, key=len, reverse=True))
)
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


_EMBEDDED_RE = re.compile(
    r"The user sent a new message while you were working:[ \t]*\n(.*?)(?:\n[ \t]*\nThis is how Claude Code surfaces|</system-reminder>|\Z)",
    re.S,
)
_NOT_HUMAN_ORIGINS = {"task-notification", "peer", "system", "hook"}


def _origin_kind(origin):
    if isinstance(origin, dict):
        return origin.get("kind")
    return None


def _blocks_text(content):
    """(texts of text blocks, whether any block is a tool result)."""
    if isinstance(content, str):
        return [content], False
    texts, has_result = [], False
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                has_result = True
            elif block.get("type") == "text":
                texts.append(block.get("text", ""))
    return texts, has_result


def _usable(text):
    text = clean_text(text)
    if not text or text.lower().startswith(_SKIP_PREFIXES):
        return None
    return text


def human_texts(entry):
    """Texts a human wrote in this record: typed turns, messages sent mid-turn, queued prompts.

    Tool results, hook text, system reminders, compaction summaries and subagent
    turns are never returned.
    """
    if not isinstance(entry, dict) or entry.get("isSidechain") or entry.get("isCompactSummary"):
        return []
    kind = entry.get("type")
    if kind == "attachment":
        att = entry.get("attachment")
        if not isinstance(att, dict) or att.get("type") != "queued_command":
            return []
        if att.get("commandMode") not in (None, "prompt") or _origin_kind(att.get("origin")) in _NOT_HUMAN_ORIGINS:
            return []
        prompt = att.get("prompt")
        return [t for t in [_usable(prompt)] if t] if isinstance(prompt, str) else []
    if kind != "user":
        return []
    msg = entry.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return []
    texts, has_result = _blocks_text(msg.get("content"))
    out = []
    # A message typed while the agent was busy is delivered inside another record.
    for raw in texts:
        for m in _EMBEDDED_RE.finditer(raw):
            t = _usable(m.group(1))
            if t:
                out.append(t)
    if (entry.get("isMeta") or entry.get("isVisibleInTranscriptOnly") or has_result
            or entry.get("toolUseResult") is not None or _origin_kind(entry.get("origin")) in _NOT_HUMAN_ORIGINS):
        return out
    for raw in texts:
        t = _usable(raw)
        if t:
            out.append(t)
    return out


def human_text(entry):
    """The first human text of a record, or None."""
    found = human_texts(entry)
    return found[0] if found else None


def looks_like_ask(sentence):
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)
    if not words:
        return False
    if len(words) > 1 and words[0].lower().endswith("ly") and words[0].lower() not in _VERBS:
        words = words[1:]  # "autonomously fix X" is still an imperative
    first = words[0].lower()
    if first in {"do", "go", "get", "let", "note"} and len(words) > 1 and words[1].lower() in _PRONOUNS:
        return False  # "do you know", "let it", questions and statements, not orders
    if first in _VERBS and first not in {"please", "need", "want", "should", "must"}:
        return True  # an imperative can be a single word, e.g. "push"
    if len(words) < 3:
        return False
    if first in _VERBS:
        return True
    if _ASK_RE.search(sentence):
        return True
    if first in {"why", "how", "what", "which", "where", "when", "who"}:
        return True
    return sentence.rstrip().endswith("?") and first in {"can", "could", "would", "will", "is", "are", "does", "do"}


def split_asks(text):
    """Split one message into individual asks using imperative-sentence heuristics."""
    out = []
    text = re.sub(r":[ \t]*\n+[ \t]*", ": ", text)  # "integrate:" then its target on the next line
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


def _secs(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def collect(paths, since=None):
    """Return (timestamp, ask text) for every ask in the transcripts."""
    items = []
    seen = {}
    for f in iter_files(paths):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                ts = str(entry.get("timestamp") or "")
                if since and ts[: len(since)] < since:
                    continue
                for text in human_texts(entry):
                    # The same message can surface as a queued prompt and again inside a later record.
                    key = " ".join(text.split())
                    if key in seen and abs(_secs(ts) - seen[key]) < 300:
                        continue
                    seen[key] = _secs(ts)
                    for ask in split_asks(text):
                        items.append((ts, ask))
    return items


def extract(paths, since=None, threshold=0.6):
    return dedupe(collect(paths, since), threshold)
