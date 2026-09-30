#!/usr/bin/env python3
"""Record the README walkthrough: a real two-turn session with the skill.

Turn 1: an ambiguous request -> the skill should investigate and ask.
Turn 2: the engineer answers -> the skill writes, validates and issues a verdict.

Outputs (in this directory): turn1.jsonl, turn2.jsonl (raw stream-json),
transcript.md (readable), card-turn1.md, card-turn2.md, verdict.json, changes.diff.

Usage: uv run --project plugins/schema-guard/engine python examples/walkthrough/record.py [--model sonnet]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PLUGIN = ROOT / "plugins" / "schema-guard"

TURN1 = (
    "/schema-guard:safe-schema-change Procurement needs grade deductions on orders: inspectors find "
    "contamination at goods-in, and reconciliation should use the accepted weight instead of the "
    "full net weight. Can you add that?"
)
TURN2 = (
    "Answers: the deduction is a weight in kilograms, recorded by the inspector per order; it is never "
    "negative and can't exceed the order's net weight. Price per kg does not change - value is "
    "accepted weight x price. Historical orders have no deduction: leave them NULL, and NULL means "
    "zero. Please go ahead."
)
TOOLS = ["Bash(sg:*)", "Bash(git:*)", "Bash(ls:*)", "Read", "Grep", "Glob", "Edit", "Write", "Task"]


def scrub(text: str, work: Path) -> str:
    """Replace machine-specific paths (temp dir, checkout, home) so recordings are safe to commit."""
    for real, fake in ((work.resolve(), "/tmp/sg-walkthrough"), (work, "/tmp/sg-walkthrough"),
                       (ROOT, "/opt/schema-guard"), (Path.home(), "/home/user")):
        text = text.replace(str(real), fake)
        text = text.replace(str(real).replace("/", "-"), fake.replace("/", "-"))  # ~/.claude/projects slugs
    return text


def run_turn(prompt: str, repo: Path, model: str, resume: str | None) -> list[dict]:
    cmd = ["claude", "-p", prompt, "--plugin-dir", str(PLUGIN), "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits", "--max-turns", "60", "--model", model, "--allowedTools", *TOOLS]
    if resume:
        cmd += ["--resume", resume]
    out = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=1800).stdout
    out = scrub(out, repo.parent)
    return [json.loads(line) for line in out.splitlines() if line.strip().startswith("{")]


def render(events: list[dict], title: str) -> str:
    lines = [f"## {title}", ""]
    for ev in events:
        if ev.get("type") == "assistant":
            for block in ev["message"].get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    lines += [block["text"].strip(), ""]
                elif block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    if block["name"] == "Bash":
                        lines.append(f"> **Bash** `{inp.get('command', '')[:200]}`")
                    elif block["name"] in ("Edit", "Write", "Read"):
                        lines.append(f"> **{block['name']}** `{inp.get('file_path', '').split('/repo/')[-1]}`")
                    elif block["name"] == "Task":
                        lines.append(f"> **Subagent** {inp.get('subagent_type', '')}: {inp.get('description', '')}")
                    else:
                        lines.append(f"> **{block['name']}**")
                    lines.append("")
    results = [ev for ev in events if ev.get("type") == "result"]
    if results:  # subagents emit their own result events; the session's is the last one
        ev = results[-1]
        lines.append(f"_turns: {ev.get('num_turns')}, cost: ${ev.get('total_cost_usd', 0):.2f}, "
                     f"time: {ev.get('duration_ms', 0) / 1000:.0f}s_")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="sonnet")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="sg-walkthrough-"))
    repo = work / "repo"
    subprocess.run(["bash", str(ROOT / "examples" / "build_fixture.sh"), str(repo)], check=True, capture_output=True)

    t1 = run_turn(TURN1, repo, args.model, None)
    (HERE / "turn1.jsonl").write_text("\n".join(json.dumps(e) for e in t1) + "\n")
    (HERE / "card-turn1.md").write_text(scrub((repo / ".schema-guard" / "card.md").read_text(), work))
    session = next(e["session_id"] for e in t1 if e.get("session_id"))

    t2 = run_turn(TURN2, repo, args.model, session)
    (HERE / "turn2.jsonl").write_text("\n".join(json.dumps(e) for e in t2) + "\n")
    (HERE / "card-turn2.md").write_text(scrub((repo / ".schema-guard" / "card.md").read_text(), work))
    (HERE / "verdict.json").write_text(scrub((repo / ".schema-guard" / "verdict.json").read_text(), work))

    subprocess.run(["git", "add", "-A", "--", ".", ":(exclude).schema-guard"], cwd=repo, check=True)
    diff = subprocess.run(["git", "diff", "--cached"], cwd=repo, capture_output=True, text=True).stdout
    (HERE / "changes.diff").write_text(scrub(diff, work))

    transcript = "# Walkthrough transcript\n\n" + render(t1, f"Turn 1 — engineer: “{TURN1.split(' ', 1)[1]}”") \
        + "\n" + render(t2, f"Turn 2 — engineer: “{TURN2}”")
    (HERE / "transcript.md").write_text(transcript)
    shutil.rmtree(work, ignore_errors=True)
    print("recorded to", HERE)


if __name__ == "__main__":
    main()
