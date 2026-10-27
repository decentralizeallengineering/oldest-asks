"""Markdown ledger of undelivered asks, oldest first."""


def pct(n, total):
    return "%.0f%%" % (100.0 * n / total) if total else "0%"


def build_report(asks, verdicts):
    by_id = {v["id"]: v for v in verdicts}
    asks = sorted(asks, key=lambda a: (a["first_seen"], a["id"]))
    total = len(asks)
    counts = {k: 0 for k in ("MET", "PARTIAL", "UNMET", "UNCLEAR", "UNCHECKED")}
    for a in asks:
        counts[by_id.get(a["id"], {}).get("verdict", "UNCHECKED")] += 1
    lines = ["# Oldest asks ledger", "", "%d asks in total." % total, ""]
    lines += ["| Verdict | Count | Share |", "| --- | --- | --- |"]
    for k in ("MET", "PARTIAL", "UNMET", "UNCLEAR", "UNCHECKED"):
        lines.append("| %s | %d | %s |" % (k, counts[k], pct(counts[k], total)))
    lines.append("")
    for title, key in (("Unmet, oldest first", "UNMET"), ("Partial, oldest first", "PARTIAL")):
        rows = [a for a in asks if by_id.get(a["id"], {}).get("verdict") == key]
        lines += ["## %s" % title, ""]
        if not rows:
            lines += ["None.", ""]
        for a in rows:
            v = by_id[a["id"]]
            lines.append("- **%s** (asked %d more time%s, last %s) `%s`" % (
                a["first_seen"][:10], a.get("repeats", 0), "" if a.get("repeats", 0) == 1 else "s",
                a["last_seen"][:10], a["id"]))
            lines.append("  - Ask: %s" % a["text"].replace("\n", " "))
            if v.get("evidence"):
                lines.append("  - Evidence: %s" % v["evidence"])
            if v.get("note"):
                lines.append("  - Note: %s" % v["note"])
            hist = v.get("history", [])
            if len(hist) > 1:
                lines.append("  - History: " + "; ".join("%s %s" % (h["checked_at"][:10], h["verdict"]) for h in hist))
        lines.append("")
    open_rest = [a for a in asks if by_id.get(a["id"], {}).get("verdict", "UNCHECKED") in ("UNCLEAR", "UNCHECKED")]
    lines += ["## Unclear or not yet checked", ""]
    if not open_rest:
        lines.append("None.")
    for a in open_rest:
        lines.append("- %s `%s` %s" % (a["first_seen"][:10], a["id"], a["text"].replace("\n", " ")))
    lines.append("")
    return "\n".join(lines)
