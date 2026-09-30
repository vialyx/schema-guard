#!/usr/bin/env python3
"""Run the schema-guard skill against labelled cases and score it.

Each case builds a fresh copy of the Acme fixture (real git history), applies the
case's setup files, runs the skill headless with `claude -p`, and reads the
verdict the skill produced in .schema-guard/verdict.json.

It also runs a deterministic-only baseline (`sg check` + `sg verdict`, no LLM)
on the same setup, so the report shows what the model adds.

Usage:
  evals/run_evals.py                       # all cases, 1 run each
  evals/run_evals.py --runs 3 --parallel 4
  evals/run_evals.py --cases 'ref-*' --model sonnet
  evals/run_evals.py --baseline-only       # free, no LLM calls
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "schema-guard"
SG = PLUGIN / "bin" / "sg"
BUILD = ROOT / "examples" / "build_fixture.sh"
CASES = ROOT / "evals" / "cases"
RESULTS = ROOT / "evals" / "results"

HEADLESS_NOTE = (
    "\n\n(This is a non-interactive run: nobody can answer questions. "
    "Follow the skill to the end and make sure `sg verdict` has been run.)"
)
ALLOWED_TOOLS = ["Bash(sg:*)", "Bash(git:*)", "Bash(ls:*)", "Read", "Grep", "Glob", "Edit", "Write", "Task"]


def load_cases(pattern: str) -> list[dict]:
    cases = []
    for f in sorted(CASES.glob("*.yaml")):
        c = yaml.safe_load(f.read_text())
        if fnmatch.fnmatch(c["id"], pattern):
            cases.append(c)
    return cases


def sh(cmd: list[str], cwd: Path, env: dict | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def prepare(case: dict, workdir: Path) -> Path:
    repo = workdir / "repo"
    r = sh(["bash", str(BUILD), str(repo)], cwd=ROOT)
    if r.returncode != 0:
        raise RuntimeError(f"fixture build failed: {r.stderr}")
    for item in case.get("setup") or []:
        p = repo / item["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(item["content"])
    return repo


def clean_env(case: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update({k: str(v) for k, v in (case.get("env") or {}).items()})
    return env


def shipped_migrations_changed(repo: Path) -> list[str]:
    r = sh(["git", "diff", "--name-only", "HEAD"], cwd=repo)
    return [p for p in r.stdout.split() if "migrations" in p]


def new_migration_files(repo: Path, case: dict) -> list[str]:
    setup = {i["path"] for i in case.get("setup") or []}
    r = sh(["git", "ls-files", "--others", "--exclude-standard"], cwd=repo)
    return [p for p in r.stdout.split() if "migrations" in p and p not in setup and not p.endswith(".pyc")]


def read_verdict(repo: Path) -> dict | None:
    f = repo / ".schema-guard" / "verdict.json"
    return json.loads(f.read_text()) if f.exists() else None


def score(case: dict, verdict: dict | None, repo: Path) -> dict:
    exp = case["expect"]
    got = verdict["verdict"] if verdict else "MISSING"
    rules = {f["rule"] for f in (verdict or {}).get("findings", [])}
    problems = []
    if got != exp["verdict"]:
        problems.append(f"verdict {got} != {exp['verdict']}")
    for r in exp.get("must_find", []):
        if r not in rules:
            problems.append(f"missing finding {r}")
    for r in exp.get("must_not_find", []):
        if r in rules:
            problems.append(f"unexpected finding {r}")
    if exp.get("questions") and not (verdict or {}).get("questions"):
        problems.append("expected questions for a human")
    new = new_migration_files(repo, case)
    if exp.get("new_migrations") is True and not new:
        problems.append("expected a new migration")
    if exp.get("new_migrations") is False and new:
        problems.append(f"should not have written a migration: {new}")
    changed = shipped_migrations_changed(repo)
    if changed:  # always a failure, for every case
        problems.append(f"modified shipped migrations: {changed}")
    return {"verdict": got, "ok": not problems, "problems": problems, "rules": sorted(rules), "new_migrations": new}


def run_skill(case: dict, model: str | None, keep: bool) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix=f"sg-eval-{case['id']}-"))
    try:
        repo = prepare(case, workdir)
        prompt = f"/schema-guard:safe-schema-change {case['prompt'].strip()}{HEADLESS_NOTE}"
        cmd = ["claude", "-p", prompt, "--plugin-dir", str(PLUGIN), "--output-format", "json",
               "--permission-mode", "acceptEdits", "--max-turns", "60", "--allowedTools", *ALLOWED_TOOLS]
        if model:
            cmd += ["--model", model]
        t0 = time.time()
        try:
            r = sh(cmd, cwd=repo, env=clean_env(case), timeout=1200)
            meta = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"result": r.stdout[-500:], "stderr": r.stderr[-500:]}
        except subprocess.TimeoutExpired:
            meta = {"result": "TIMEOUT"}
        out = score(case, read_verdict(repo), repo)
        out.update({
            "case": case["id"], "kind": "skill",
            "cost_usd": meta.get("total_cost_usd"), "turns": meta.get("num_turns"),
            "seconds": round(time.time() - t0, 1), "final_message": (meta.get("result") or "")[-1200:],
        })
        if keep:
            out["workdir"] = str(workdir)
        return out
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)


def run_baseline(case: dict) -> dict:
    """Deterministic checks only, on the case as given (the LLM writes nothing)."""
    workdir = Path(tempfile.mkdtemp(prefix=f"sg-base-{case['id']}-"))
    try:
        repo = prepare(case, workdir)
        path = case.get("service", ".")
        env = clean_env(case)
        sh([str(SG), "check", "--path", path], cwd=repo, env=env)
        sh([str(SG), "verdict", "--path", path], cwd=repo, env=env)
        out = score(case, read_verdict(repo), repo)
        out.update({"case": case["id"], "kind": "baseline"})
        return out
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def summarize(cases: list[dict], results: list[dict], runs: int) -> str:
    lines = ["| case | expected | skill verdicts | skill ok | consistency | baseline | cost $ | time s |",
             "|---|---|---|---|---|---|---|---|"]
    skill_ok = skill_n = base_ok = 0
    consistencies = []
    total_cost = 0.0
    for c in cases:
        rs = [r for r in results if r["case"] == c["id"] and r["kind"] == "skill"]
        bs = [r for r in results if r["case"] == c["id"] and r["kind"] == "baseline"]
        verdicts = [r["verdict"] for r in rs]
        cons = Counter(verdicts).most_common(1)[0][1] / len(verdicts) if verdicts else 0
        if verdicts:
            consistencies.append(cons)
        ok = sum(r["ok"] for r in rs)
        skill_ok += ok
        skill_n += len(rs)
        cost = sum(r.get("cost_usd") or 0 for r in rs)
        total_cost += cost
        secs = sum(r.get("seconds") or 0 for r in rs) / max(len(rs), 1)
        b = bs[0] if bs else None
        base_ok += bool(b and b["ok"])
        lines.append(
            f"| {c['id']} | {c['expect']['verdict']} | {', '.join(verdicts) or '-'} | {ok}/{len(rs)} | "
            f"{cons:.0%} | {(b['verdict'] + (' ✓' if b['ok'] else ' ✗')) if b else '-'} | {cost:.2f} | {secs:.0f} |"
        )
    head = [
        f"# schema-guard eval — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"- Skill: **{skill_ok}/{skill_n}** runs correct ({runs} run(s) per case)"
        + (f", mean consistency {sum(consistencies) / len(consistencies):.0%}" if consistencies else ""),
        f"- Deterministic baseline (no LLM): **{base_ok}/{len(cases)}** cases correct",
        f"- Total skill cost: ${total_cost:.2f}",
        "",
    ]
    fails = [r for r in results if not r["ok"]]
    tail = ["", "## Failures", ""] + [f"- `{r['case']}` ({r['kind']}): {'; '.join(r['problems'])}" for r in fails] if fails else []
    return "\n".join(head + lines + tail) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default="*", help="glob on case id")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--model", help="model alias for claude -p (default: your Claude Code default)")
    ap.add_argument("--baseline-only", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep work dirs for inspection")
    args = ap.parse_args()

    cases = load_cases(args.cases)
    if not cases:
        print("no cases match", file=sys.stderr)
        return 2
    jobs = [("baseline", c) for c in cases]
    if not args.baseline_only:
        jobs += [("skill", c) for c in cases for _ in range(args.runs)]

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futs = {pool.submit(run_baseline if k == "baseline" else run_skill, c, *(() if k == "baseline" else (args.model, args.keep))): (k, c)
                for k, c in jobs}
        for fut in as_completed(futs):
            k, c = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = {"case": c["id"], "kind": k, "verdict": "ERROR", "ok": False, "problems": [str(exc)[:300]]}
            results.append(r)
            print(f"[{k:8}] {c['id']:32} {r['verdict']:12} {'ok' if r['ok'] else 'FAIL ' + '; '.join(r['problems'])}", flush=True)

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (RESULTS / f"{stamp}.json").write_text(json.dumps(results, indent=2))
    report = summarize(cases, results, 0 if args.baseline_only else args.runs)
    (RESULTS / "latest.md").write_text(report)
    print("\n" + report)
    skill = [r for r in results if r["kind"] == "skill"]
    return 0 if all(r["ok"] for r in skill) else 1


if __name__ == "__main__":
    sys.exit(main())
