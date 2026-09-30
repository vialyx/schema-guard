"""Render a verdict document as a PR-pasteable markdown card. Verdict first, details after."""

from __future__ import annotations

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
        out += [f"{i}. {q}" for i, q in enumerate(doc["questions"], 1)]

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

    out.append("\n| Check | Status | Summary |\n|---|---|---|")
    for c in doc["checks"]:
        out.append(f"| {c['name']} | {ICON[c['status']]} {c['status']} | {_cell(c.get('summary', ''))} |")

    other = [f for f in doc["findings"] if f["severity"] in ("ask", "warn")]
    if other:
        out.append("\n**Findings**")
        for f in other:
            ev = f" (`{f['evidence']}`)" if f.get("evidence") else ""
            fix = f" — {f['fix']}" if f.get("fix") else ""
            out.append(f"- {ICON[f['severity']]} `{f['rule']}` {f['message']}{ev}{fix}")

    if doc.get("suggestions"):
        out.append("\n**Suggestions (non-blocking)**")
        out += [f"- {x}" for x in doc["suggestions"]]

    if doc["owners"]:
        out.append(f"\n**Required reviewers:** {' '.join(doc['owners'])}")
    if doc["files_changed"]:
        out.append("\n<details><summary>Files</summary>\n\n" + "\n".join(f"- `{p}`" for p in doc["files_changed"]) + "\n</details>")
    return "\n".join(out) + "\n"


def _cell(s: str) -> str:
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= 140 else s[:137] + "..."
