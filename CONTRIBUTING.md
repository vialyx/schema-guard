# Contributing

Thanks for helping. schema-guard has two halves with a hard boundary (see [docs/architecture.md](docs/architecture.md)):
the **engine** (`plugins/schema-guard/engine`, deterministic Python) and the **skill** (`plugins/schema-guard/skills`, prompt text).
Changes to either are welcome; they are tested differently.

## Setup
Requirements: [uv](https://docs.astral.sh/uv/), git, Node (for `npx squawk-cli`), and PostgreSQL 16 binaries
or a disposable server in `SG_DATABASE_URL`.

```bash
cd plugins/schema-guard/engine
uv run pytest -q
```

If `initdb` can't run on your machine (for example, as root in a container), point the tests at a throwaway server:

```bash
docker run -d --rm -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
export SG_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
```

## Engine changes
- Rules, checks, adapters and dialects are plugins registered via entry points in `engine/pyproject.toml`.
  Adding one is one file plus one registration line; [docs/adding-a-framework.md](docs/adding-a-framework.md) walks through an adapter.
- Every new rule needs unit tests in `tests/unit/test_rules.py` (a positive and a negative case).
- Every new adapter needs a sample in `tests/contract/samples.py`; the contract tests then run against it automatically.
- The verdict rules in `sg/core/verdict.py` are the trust boundary. Anything that could make GO easier to reach needs
  a test in `tests/unit/test_verdict.py` and a clear reason in the PR.

## Skill changes
Prompt changes can't be unit-tested, so they are measured with the eval suite:

```bash
# free, no LLM: deterministic baseline only (also runs in CI)
uv run --project plugins/schema-guard/engine python evals/run_evals.py --baseline-only

# the real thing: headless `claude -p`, about $0.25 per run
uv run --project plugins/schema-guard/engine python evals/run_evals.py --runs 3 --model sonnet
```

- Include the before/after eval summary (`evals/results/latest.md`) in the PR.
- Fix behaviour with a general principle or a structural change, not a case-specific patch.
  If you add a case to cover a bug, add it under `evals/cases/` with an `expect` block.
- Runs that fail to execute (auth, usage limits, network) are reported as `ERROR` and excluded from the score. Re-run them.
- The skill text must stay framework-neutral: framework knowledge belongs in adapters.

## Walkthrough recording
`examples/walkthrough/record.py` re-records the README example with a real two-turn session. It scrubs local paths,
but read the output before committing it.

## Pull requests
- Keep PRs focused; CI runs the engine tests, the deterministic baseline and gitleaks.
- Don't commit anything from `.schema-guard/` or real customer data. Seed data must be synthetic.
