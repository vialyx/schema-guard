"""Deterministic verdict aggregation.

The rule that makes the whole system trustworthy lives here:
  * checks decide the floor of the verdict;
  * the LLM may only *raise* it (escalate), never lower it;
  * a skipped check, or any open question, can never produce GO.
"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import jsonschema

from sg.core.models import CheckResult, Status, Verdict

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "verdict.schema.json"


def aggregate(checks: list[CheckResult], llm: dict[str, Any]) -> tuple[Verdict, list[str]]:
    """Return (verdict, reasons). Reasons explain every step up from GO."""
    reasons: list[str] = []
    verdict = Verdict.GO

    def raise_to(v: Verdict, why: str) -> None:
        nonlocal verdict
        if v.rank > verdict.rank:
            verdict = v
        if v is not Verdict.GO:
            reasons.append(why)

    for c in checks:
        v = Verdict.from_status(c.status)
        if v is not Verdict.GO:
            raise_to(v, f"check `{c.name}` = {c.status.value}: {c.summary}")

    if not checks:
        raise_to(Verdict.NEEDS_HUMAN, "no checks were run")

    if llm.get("questions"):
        raise_to(Verdict.NEEDS_HUMAN, f"{len(llm['questions'])} open question(s) for a human")

    esc = llm.get("escalate")
    if esc:
        esc_v = Verdict(esc)
        raise_to(esc_v, f"escalated by reviewer: {llm.get('escalation_reason') or 'no reason given'}")

    return verdict, reasons


def codeowners_for(repo_root: Path, paths: list[str]) -> list[str]:
    """Owners of `paths` per CODEOWNERS (last matching rule wins, like GitHub)."""
    for candidate in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        f = repo_root / candidate
        if f.exists():
            break
    else:
        return []
    rules: list[tuple[str, list[str]]] = []
    for line in f.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            pat, *owners = line.split()
            rules.append((pat, owners))
    found: list[str] = []
    for p in paths:
        owners: list[str] = []
        for pat, o in rules:
            if _codeowners_match(pat, p):
                owners = o
        for o in owners:
            if o not in found:
                found.append(o)
    return found


def _codeowners_match(pattern: str, path: str) -> bool:
    # Simplified gitignore semantics (fnmatch's `*` also crosses `/`, which is fine here).
    pat = pattern.lstrip("/")
    anchored = pattern.startswith("/") or "/" in pat.rstrip("/")
    if pat.endswith("/"):
        pat += "*"
    parts = path.split("/")
    candidates = [path] if anchored else ["/".join(parts[i:]) for i in range(len(parts))]
    return any(fnmatch(c, pat) or fnmatch(c, pat + "/*") for c in candidates)


def build(
    checks: list[CheckResult],
    llm: dict[str, Any],
    *,
    repo_root: Path,
    migrations: list[str],
    tables: list[dict[str, Any]],
    policy_sources: list[str],
) -> dict[str, Any]:
    verdict, reasons = aggregate(checks, llm)
    files = sorted(set(llm.get("files_changed") or []) | set(migrations))
    doc = {
        "schema_version": 1,
        "verdict": verdict.value,
        "reasons": reasons,
        "intent": llm.get("intent", ""),
        "summary": llm.get("summary", ""),
        "tables": tables,
        "plan": llm.get("plan") or {"expand": [], "contract": []},
        "files_changed": files,
        "checks": [c.to_dict() for c in checks],
        "findings": [f.to_dict() for c in checks for f in c.findings],
        "questions": llm.get("questions") or [],
        "suggestions": llm.get("suggestions") or [],
        "owners": codeowners_for(repo_root, files),
        "llm_escalated": bool(llm.get("escalate")),
        "policy_sources": policy_sources,
    }
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text()))
    return doc


def worst_status(checks: list[CheckResult]) -> Status:
    return Status.worst([c.status for c in checks])
