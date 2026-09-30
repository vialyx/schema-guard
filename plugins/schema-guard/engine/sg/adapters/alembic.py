"""Alembic adapter.

Listing migrations happens in-process through Alembic's ScriptDirectory, which only
parses revision files and never executes env.py. Anything that needs env.py
(offline SQL rendering, upgrade, downgrade) runs in a subprocess using the service's
own Python (env.py usually imports application code), through alembic_runner.py,
which forces the throwaway database URL and refuses to connect anywhere else.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from sg.adapters.base import MigrationAdapter
from sg.core.models import Migration

#: URL handed to env.py when rendering offline; nothing ever connects to it.
OFFLINE_URL = "postgresql+psycopg://offline@localhost/offline"
RUNNER = Path(__file__).resolve().parent / "alembic_runner.py"


class AlembicAdapter(MigrationAdapter):
    name = "alembic"

    # -- discovery ---------------------------------------------------------

    @classmethod
    def detect(cls, service_root: Path) -> bool:
        return (Path(service_root) / "alembic.ini").is_file()

    def _config(self):
        from alembic.config import Config

        ini = self.service_root / "alembic.ini"
        cfg = Config(str(ini))
        loc = cfg.get_main_option("script_location") or "alembic"
        # Relative locations are relative to the service root, not our cwd.
        if ":" not in loc or Path(loc).is_absolute():
            loc_path = Path(loc.replace("%(here)s", str(self.service_root)))
            if not loc_path.is_absolute():
                loc_path = self.service_root / loc_path
            cfg.set_main_option("script_location", str(loc_path))
        return cfg

    def _script(self):
        from alembic.script import ScriptDirectory

        return ScriptDirectory.from_config(self._config())

    def list_migrations(self) -> list[Migration]:
        script = self._script()
        # walk_revisions yields head -> base; reverse for apply order.
        revs = list(script.walk_revisions("base", "heads"))
        revs.reverse()
        out: list[Migration] = []
        for rev in revs:
            down = rev.down_revision
            if isinstance(down, (tuple, list)):
                # Merge revision: down_revision is a tuple like ("a1", "b2").
                # Migration.parent is a single id, so we keep the first branch;
                # the full tuple stays in the revision file for readers.
                down = down[0] if down else None
            out.append(Migration(id=rev.revision, path=Path(rev.path), parent=down))
        return out

    def migration_dirs(self) -> list[Path]:
        script = self._script()
        dirs = {Path(d) for d in script.version_locations}
        return sorted(d for d in dirs if d.is_dir()) or super().migration_dirs()

    # -- subprocess helpers --------------------------------------------------

    def _run(self, args: list[str], database_url: str) -> str:
        cmd = [*self.python, str(RUNNER), database_url, "-c", "alembic.ini", *args]
        proc = subprocess.run(cmd, cwd=self.service_root, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"alembic {' '.join(args)} failed (exit {proc.returncode}) in "
                f"{self.service_root}:\n{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout

    def render_sql(self, migration_id: str) -> str:
        by_id = {m.id: m for m in self.list_migrations()}
        if migration_id not in by_id:
            raise KeyError(f"unknown alembic revision {migration_id!r}")
        parent = by_id[migration_id].parent
        rng = f"{parent}:{migration_id}" if parent else migration_id
        return self._run(["upgrade", rng, "--sql"], OFFLINE_URL)

    def upgrade(self, dsn: str, target: str = "head") -> None:
        self._run(["upgrade", target], dsn)

    def downgrade(self, dsn: str, target: str) -> None:
        self._run(["downgrade", target], dsn)

    # -- LLM guidance ----------------------------------------------------------

    def _next_revision(self) -> tuple[str, str, str | None]:
        """(suggested file name pattern, suggested revision id, current head)."""
        migs = self.list_migrations()
        head = migs[-1].id if migs else None
        numeric = [m for m in migs if re.fullmatch(r"\d+", m.id)]
        if migs and len(numeric) == len(migs):
            width = max(len(m.id) for m in numeric)
            nxt = str(max(int(m.id) for m in numeric) + 1).zfill(width)
            return f"{'N' * width}_slug.py", nxt, head
        if not migs:
            return "0001_slug.py", "0001", None
        return "<revision>_slug.py (alembic revision --autogenerate style)", "<new hex id>", head

    def conventions_hint(self) -> str:
        try:
            pattern, nxt, head = self._next_revision()
            dirs = ", ".join(str(d) for d in self.migration_dirs())
        except Exception as e:  # never let guidance break a run
            pattern, nxt, head, dirs = "NNNN_slug.py", "<next>", f"<unknown: {e}>", "?"
        return "\n".join(
            [
                "Alembic conventions for a new migration:",
                f"- Put it in {dirs}; file name follows the existing pattern `{pattern}`.",
                f"- Next revision id: revision = \"{nxt}\", down_revision = \"{head}\" (current head).",
                "- Always implement downgrade(); it must exactly undo upgrade().",
                "- op.add_column with explicit SQLAlchemy types; NUMERIC for quantities, BIGINT cents for money; never Float.",
                "- New columns on existing tables: nullable or with a constant server_default.",
                "- Indexes on existing tables: build CONCURRENTLY outside the transaction:",
                "    with op.get_context().autocommit_block():",
                "        op.create_index(..., postgresql_concurrently=True)",
                "- Constraints: add NOT VALID, then VALIDATE in a separate step via op.execute(...).",
                "- ALTERs on existing tables: op.execute(\"SET lock_timeout = '3s'\") first.",
                "- No data backfills of hot tables inside the migration; ship a separate batched job.",
                "- One logical change per migration; never edit a migration that has shipped.",
            ]
        )

    def caller_patterns(self, table: str, column: str | None) -> list[str]:
        t = re.escape(table)
        pats = [
            rf"__tablename__\s*=\s*['\"]{t}['\"]",
            rf"Table\(\s*['\"]{t}['\"]",
        ]
        if column:
            c = re.escape(column)
            pats += [
                rf"\.{c}\b",
                rf"['\"]{c}['\"]",
                rf"\b{c}\s*(?::\s*Mapped\[[^\]]*\]\s*)?=\s*(?:sa\.|sqlalchemy\.)?(?:mapped_column|Column)\(",
            ]
        return pats
