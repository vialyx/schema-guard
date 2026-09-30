"""Render a verdict document as a PR-pasteable markdown card. Verdict first, details after."""

from __future__ import annotations

import re
from typing import Any

BADGE = {"GO": "🟢 GO", "NEEDS-HUMAN": "🟡 NEEDS-HUMAN", "REFUSED": "🔴 REFUSED"}
ICON = {"pass": "✅", "warn": "⚠️", "skipped": "⏭️", "ask": "❓", "refuse": "⛔"}


def card(doc: dict[str, Any]) -> str:
    out: list[str] = []
    out.append(f"## schema-guard: {BADGE[doc['verdict']]}")
    if doc.get("intent"):
        out.append(f"**Intent:** {doc['intent']}")
    if doc.get("summary"):
        out.append(doc["summary"])

    if doc["verdict"] != "GO" and doc["reasons"]:
        out.append("\n**Why not GO**")
        out += [f"- {r}" for r in doc["reasons"]]

    refusals = [f for f in doc["findings"] if f["severity"] == "refuse"]
    if refusals:
        out.append("\n**Refused — safe alternative**")
        for f in refusals:
            out.append(f"- `{f['rule']}` {f['message']}" + (f" → {f['fix']}" if f.get("fix") else ""))

    if doc["questions"]:
        out.append("\n**Questions for a human (answer, then re-run)**")
        out += [f"{i}. {_strip_number(q)}" for i, q in enumerate(doc["questions"], 1)]

    if doc.get("tables"):
        out.append("\n**Tables touched**")
        for t in doc["tables"]:
            rows = f", ~{t['est_rows']:,} rows" if t.get("est_rows") else ""
            out.append(f"- `{t['name']}` ({t['class']}{rows})")

    plan = doc.get("plan") or {}
    if plan.get("expand") or plan.get("contract"):
        out.append("\n**Plan**")
        for step in plan.get("expand", []):
            out.append(f"- [expand] {step}")
        for step in plan.get("contract", []):
            out.append(f"- [contract — follow-up ticket] {step}")

    if doc["checks"]:
        # One line; anything that isn't a pass is already explained under "Why not GO".
        out.append("\n**Checks:** " + " · ".join(f"{ICON[c['status']]} {c['name']}" for c in doc["checks"]))
    else:
        out.append("\n**Checks:** none run yet (run `sg check` once a migration exists).")

    asks = [f for f in doc["findings"] if f["severity"] == "ask"]
    if asks:
        out.append("\n**Findings**")
        out += [_finding(f) for f in asks]
    warns = [f for f in doc["findings"] if f["severity"] == "warn"]
    if warns:
        out.append(f"\n<details><summary>{len(warns)} warning(s), non-blocking</summary>\n")
        out += [_finding(f) for f in warns]
        out.append("\n</details>")

    if doc.get("suggestions"):
        out.append("\n**Suggestions (non-blocking)**")
        out += [f"- {x}" for x in doc["suggestions"]]

    if doc["owners"]:
        out.append(f"\n**Required reviewers:** {' '.join(doc['owners'])}")
    elif doc["files_changed"]:
        out.append("\n**Required reviewers:** none found (add a CODEOWNERS file to route reviews)")
    if doc["files_changed"]:
        out.append("\n<details><summary>Files</summary>\n\n" + "\n".join(f"- `{p}`" for p in doc["files_changed"]) + "\n</details>")
    return "\n".join(out) + "\n"


_LEADING_NUMBER = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+")


def _strip_number(s: str) -> str:
    """Drop a list marker the model already put on a question ("1. ", "2) ", "- "); the card numbers them."""
    return _LEADING_NUMBER.sub("", s, count=1)


def _finding(f: dict[str, Any]) -> str:
    line = f"- {ICON[f['severity']]} `{f['rule']}` {f['message']}" + (f" — {f['fix']}" if f.get("fix") else "")
    ev = (f.get("evidence") or "").strip()
    if not ev:
        return line
    if "\n" in ev or "`" in ev or len(ev) > 120:  # tracebacks, SQL: a fenced block keeps the markdown intact
        return f"{line}\n  ```\n" + "\n".join(f"  {x}" for x in ev.splitlines()[-12:]) + "\n  ```"
    return f"{line} (`{ev}`)"
