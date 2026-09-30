# Adding a migration framework

Example: Django. The same steps apply to Prisma, Rails, Flyway, Liquibase, golang-migrate, …
You should not need to touch `sg/core`, `SKILL.md`, or any other adapter.

## 1. Write the adapter — `sg/adapters/django.py`

```python
from pathlib import Path
from sg.adapters.base import MigrationAdapter
from sg.core.models import Migration


class DjangoAdapter(MigrationAdapter):
    name = "django"

    @classmethod
    def detect(cls, service_root: Path) -> bool:
        return (service_root / "manage.py").exists()

    def list_migrations(self) -> list[Migration]:
        # `python manage.py showmigrations --plan` gives apply order;
        # map each to Migration(id="app.0005_x", path=<file>, parent=<previous id>)
        ...

    def render_sql(self, migration_id: str) -> str:
        # `python manage.py sqlmigrate <app> <name>` — offline, no DB needed
        ...

    def upgrade(self, dsn: str, target: str = "head") -> None:
        # `python manage.py migrate [<app> <name>]` with DATABASES from dsn
        ...

    def downgrade(self, dsn: str, target: str) -> None:
        # `python manage.py migrate <app> <previous>`
        ...

    def conventions_hint(self) -> str:
        return "Django: one migration per change; use AddField(null=True); RunSQL for CONCURRENTLY with atomic=False; …"

    def caller_patterns(self, table: str, column: str | None) -> list[str]:
        return [rf"\b{column}\b\s*=", rf"['\"]{column}['\"]", rf"\.{column}\b"] if column else []
```

The contract is in `sg/adapters/base.py`. Key expectations:
- `detect` is cheap and has no side effects.
- `render_sql(id)` renders **only** that migration, from its parent.
- `upgrade`/`downgrade` raise on failure (the checks turn exceptions into findings).
- `conventions_hint` is how framework knowledge reaches the LLM. Keep it short and imperative.

## 2. Register it — `pyproject.toml`

```toml
[project.entry-points."schema_guard.adapters"]
django = "sg.adapters.django:DjangoAdapter"
```

Out-of-tree alternative: put the adapter in your own package and declare the same entry point there. `pip install` it next to schema-guard and it's discovered automatically.

## 3. Add a contract sample — `tests/contract/samples.py`

Add a builder under your adapter's name that creates a 2-migration project: create `widgets(id)`, then add `note text`.
`test_every_adapter_has_a_sample` fails until you do. Then run:

```bash
uv run pytest -q tests/contract
```

The contract suite checks detection (and non-detection of other frameworks), ordering and the parent chain, single-migration rendering, and a real upgrade → downgrade → base → upgrade cycle against Postgres.

## 4. Add eval cases

Add a service using the framework to `examples/acme-mini-org/` (and to `schema-guard.yaml` under `services:`). Then add at least one GO and one REFUSED case in `evals/cases/` with `service: services/<name>`.

```bash
uv run --project plugins/schema-guard/engine python evals/run_evals.py --cases '*<name>*' --runs 3
```

## Adding a database engine

Same pattern with `sg/dialects/<name>.py` (`Dialect` + `EphemeralServer`), registered under `schema_guard.dialects`, selected by `dialect:` in `schema-guard.yaml`.
Squawk is Postgres-only and reports itself as `skipped` elsewhere. Add an equivalent linter check for your engine (for MySQL, e.g. a gh-ost or pt-online-schema-change dry run) so GO stays reachable.
