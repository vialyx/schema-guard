# Build notes

schema-guard was built almost entirely with Claude Code. These notes cover how AI was used, the design decisions behind it, and the mistakes that shaped it.

## How AI was used
Almost all the code, tests, fixtures and first drafts of the docs were written by Claude Code, working through the design in sessions I directed. I also used AI to check the AI:
- **Evals:** 15 labelled cases run through headless `claude -p`, 3 runs each, scored against a deterministic baseline.
- **Independent reviewers:** two Claude subagents reviewed the finished repo from different angles: checking the docs' claims against the code, and adopting it cold in a new repo. They found real bugs (below), which were then fixed.
- **A recorded session:** the walkthrough is a real two-turn session, recorded by a script, not a written-up example.

## Design decisions
- **The problem:** schema changes on money, ledger and inventory tables. They are high-stakes, frequent, and look fine in review when they are wrong.
- **The trust model:** a deterministic engine sets the minimum verdict, and the model may only escalate. "The AI said it's fine" is never evidence.
- **Three verdicts, not a score.** GO / NEEDS-HUMAN / REFUSED map directly to what CI and a reviewer do.
- **"Shipped" means on the base branch in git**, not "applied to a database". Nothing needs production credentials.
- **Org knowledge lives in versioned files owned by humans** (`schema-guard.yaml`, `LEARNINGS.md`), not in the prompt. The skill proposes learnings but never commits them.
- **Ask before generating** when units, NULL meaning, backfills or ledger impact are unclear. A wrong-but-plausible migration is worse than a question.
- **No vector database and no MCP server.** The context is small and structured.
- **Adapters, not a framework-specific skill.** The skill text never names Alembic, so adding a framework is one file.

## Lessons learned

| What happened | What I changed |
|---|---|
| **Skill v1 over-asked.** It escalated a safe new table to NEEDS-HUMAN with product questions ("how will this reach the ledger?"): 0/1 on the trap case, 43/45 overall. | A separate non-blocking `suggestions` channel that can't change the verdict, and a written test for what counts as a blocking question. That's a principle, not a patch for one case. The trap case went to 3/3 and the ambiguous case still asked 3/3. |
| **The squawk linter was noisy.** Every migration got BIGINT and identity-style advice. | The policy chooses which lint rules to ignore and which to escalate; the rest are warnings. Drops and renames are left to the policy-aware `destructive-change` rule, because dropping a scratch table is fine. |
| **The hook can't stop shell edits** (`sed -i` on a shipped migration). | I don't pretend it can: the CI gate is the enforcement, and the hook is a fast local guard. |
| **An eval run was silently wrong.** The CLI hit a usage limit and returned empty results in 2–4 s, and they were scored as model misses. | The runner marks CLI failures as `ERROR` and leaves them out of the score. |
| **Recordings leaked local paths.** The transcripts contained my home directory and temp paths. | The recorder removes them itself, and the repo is scanned with gitleaks in CI. |
| **The reviewers found fail-open bugs.** A stock Alembic `env.py` would have run against the database in `alembic.ini`. A PR could mark a ledger table `scratch` in its own policy and turn REFUSED into GO. A missing base branch made shipped migrations look new. A stale `checks.json` could produce an old card. | P0: the Alembic runner forces the throwaway database and exits before connecting elsewhere; checks use the base branch's policy; the base branch falls back to `origin/`, and a missing one is surfaced; results are fingerprinted. Each has a test. |
| **A perfect eval score hid a miss.** After P0 and P1 the suite scored 45/45, all runs consistent. Re-recording the walkthrough then showed the skill giving GO to a CHECK constraint on `orders`, even though a legacy job rewrites `orders` with SQL built at runtime, so an ERP update could violate the CHECK and fail the nightly batch. The model saw the job but reasoned "only adding a column" and filed it as a suggestion. The first recording had caught it. | A deterministic fix, not a prompt tweak: the callers check now flags any new constraint on a table that has dynamic-SQL writers (`constraint-dynamic-writers`, ASK). It has a unit test and a new eval case (16), and the no-LLM baseline catches it too. The lesson for rollout: evals only cover the cases you wrote, so every surprise becomes a case. |
| **Adoption was too manual.** Four hand-written files, a bash loop in CI that stopped at the first non-GO service under `bash -e`, and every PR that only changed code was blocked. | P1: `sg init`, `sg doctor`, `sg ci`, and a reusable workflow with an approval label. |

## Roadmap
- A callers scan across repos (the org's code-search index), since the current scan only sees this repo.
- Table sizes and seed data taken from production statistics and an anonymised sample, instead of hand-maintained.
- More eval cases from real incidents, and running the suite on every change to the skill.
- Adapters for Django, Prisma and Rails, which are the next most common in a mixed org.
