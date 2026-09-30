"""Plain-SQL (Flyway-style) adapter.

Why this exists: it proves the MigrationAdapter abstraction is not secretly
shaped around Alembic, and it serves teams that keep hand-written SQL files
without an ORM or migration framework.

Layout, under `migrations/`, `db/migrations/`, `db/migration/`, `sql/`, Flyway's
`src/main/resources/db/migration/`, or `migrations_dir:` from schema-guard.yaml:

    V1__create_widgets.sql     forward migration, version 1
    U1__create_widgets.sql     optional undo for version 1 (forward-only repos have none)
    V2__add_note.sql

Versions sort numerically (dotted versions such as `V1.2` are allowed).
Applied versions are tracked in `schema_guard_history(version text primary key)`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sg.adapters.base import MigrationAdapter
from sg.core.models import Migration

_CANDIDATE_DIRS = ("migrations", "db/migrations", "db/migration", "src/main/resources/db/migration", "sql")
_FILE = re.compile(r"^(?P<kind>[VU])(?P<version>\d+(?:[._]\d+)*)__(?P<desc>.+)\.sql$")
HISTORY_TABLE = "schema_guard_history"


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.split(r"[._]", version))


@dataclass
class _Files:
    version: str
    forward: Path
    undo: Path | None

    @property
    def id(self) -> str:
        return f"V{self.version}"


def _find_dir(service_root: Path, configured: str | None = None) -> Path | None:
    for rel in ([configured] if configured else _CANDIDATE_DIRS):
        d = Path(service_root) / rel
        if d.is_dir() and any(
            (m := _FILE.match(p.name)) and m["kind"] == "V" for p in d.iterdir()
        ):
            return d
    return None


class RawSqlAdapter(MigrationAdapter):
    name = "raw_sql"
    # Undo files are optional in Flyway (and a paid feature), so forward-only is the norm.
    requires_downgrade = False
    migrations_dir: str | None = None  # set from the service's `migrations_dir:`

    @classmethod
    def detect(cls, service_root: Path) -> bool:
        root = Path(service_root)
        return not (root / "alembic.ini").exists() and _find_dir(root) is not None

    # -- files -----------------------------------------------------------------

    def _dir(self) -> Path:
        d = _find_dir(self.service_root, self.migrations_dir)
        if d is None:
            where = self.migrations_dir or "{" + ",".join(_CANDIDATE_DIRS) + "}"
            raise FileNotFoundError(
                f"no V<version>__<desc>.sql files under {self.service_root}/{where}; "
                "set `migrations_dir:` for this service in schema-guard.yaml"
            )
        return d

    def _files(self) -> list[_Files]:
        forward: dict[str, Path] = {}
        undo: dict[str, Path] = {}
        for p in self._dir().iterdir():
            m = _FILE.match(p.name)
            if not m:
                continue
            key = ".".join(str(n) for n in _version_key(m["version"]))
            target = forward if m["kind"] == "V" else undo
            if key in target:
                raise ValueError(f"duplicate {m['kind']}{key}: {target[key].name} and {p.name}")
            target[key] = p
        return [
            _Files(v, forward[v], undo.get(v))
            for v in sorted(forward, key=_version_key)
        ]

    def list_migrations(self) -> list[Migration]:
        out: list[Migration] = []
        parent: str | None = None
        for f in self._files():
            out.append(Migration(id=f.id, path=f.forward, parent=parent))
            parent = f.id
        return out

    def migration_dirs(self) -> list[Path]:
        return [self._dir()]

    def _get(self, migration_id: str) -> _Files:
        for f in self._files():
            if f.id == migration_id:
                return f
        raise KeyError(f"unknown migration {migration_id!r}")

    def render_sql(self, migration_id: str) -> str:
        return self._get(migration_id).forward.read_text()

    # -- database --------------------------------------------------------------

    @staticmethod
    def _exec_script(conn, sql: str) -> None:
        from sg.core.sql import split

        # Use the DBAPI cursor with no parameters so a literal '%' in the SQL is
        # never mistaken for a placeholder (exec_driver_sql always passes params).
        cur = conn.connection.cursor()
        try:
            for stmt in split(sql):
                cur.execute(stmt)
        finally:
            cur.close()

    def _applied(self, conn) -> set[str]:
        from sqlalchemy import text

        conn.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (version text PRIMARY KEY)"
        )
        return {r[0] for r in conn.execute(text(f"SELECT version FROM {HISTORY_TABLE}"))}

    def _resolve(self, files: list[_Files], target: str) -> int:
        """Index into files of the target (inclusive); -1 for base."""
        if target == "base":
            return -1
        if target == "head":
            return len(files) - 1
        for i, f in enumerate(files):
            if f.id == target or f.version == target.lstrip("V"):
                return i
        raise KeyError(f"unknown migration target {target!r}")

    def upgrade(self, dsn: str, target: str = "head") -> None:
        from sqlalchemy import create_engine, text

        files = self._files()
        stop = self._resolve(files, target)
        engine = create_engine(dsn)
        try:
            with engine.begin() as conn:
                applied = self._applied(conn)
            for f in files[: stop + 1]:
                if f.id in applied:
                    continue
                try:
                    with engine.begin() as conn:  # one transaction per file
                        self._exec_script(conn, f.forward.read_text())
                        conn.execute(
                            text(f"INSERT INTO {HISTORY_TABLE} (version) VALUES (:v)"),
                            {"v": f.id},
                        )
                except Exception as e:
                    raise RuntimeError(f"{f.forward.name} failed: {e}") from e
        finally:
            engine.dispose()

    def downgrade(self, dsn: str, target: str) -> None:
        from sqlalchemy import create_engine, text

        files = self._files()
        keep = self._resolve(files, target)
        engine = create_engine(dsn)
        try:
            with engine.begin() as conn:
                applied = self._applied(conn)
            for f in reversed(files[keep + 1 :]):
                if f.id not in applied:
                    continue
                if f.undo is None:
                    raise RuntimeError(
                        f"cannot downgrade {f.id}: no undo file U{f.version}__<desc>.sql "
                        f"next to {f.forward.name}"
                    )
                try:
                    with engine.begin() as conn:
                        self._exec_script(conn, f.undo.read_text())
                        conn.execute(
                            text(f"DELETE FROM {HISTORY_TABLE} WHERE version = :v"),
                            {"v": f.id},
                        )
                except Exception as e:
                    raise RuntimeError(f"{f.undo.name} failed: {e}") from e
        finally:
            engine.dispose()

    # -- LLM guidance ------------------------------------------------------------

    def conventions_hint(self) -> str:
        try:
            files = self._files()
            d = self._dir()
            nxt = (_version_key(files[-1].version)[0] + 1) if files else 1
            uses_undo = any(f.undo for f in files)
        except Exception:
            d, nxt, uses_undo = Path("migrations"), 1, False
        undo = (f"- Always add the matching undo file U{nxt}__<same description>.sql that exactly reverts it."
                if uses_undo else
                "- This repo is forward-only (no U files): no undo file; a rollback is a new forward migration.")
        return "\n".join(
            [
                "Plain-SQL (Flyway-style) conventions for a new migration:",
                f"- Add {d}/V{nxt}__<snake_case_description>.sql (next version: V{nxt}).",
                undo,
                "- Each file runs in one transaction; never edit a V file that has shipped.",
                "- Plain PostgreSQL DDL; NUMERIC for quantities, BIGINT cents for money; never float/real.",
                "- ALTERs on existing tables: start with SET lock_timeout = '3s';",
                "- Add constraints NOT VALID and VALIDATE them in a later migration.",
                "- CREATE INDEX CONCURRENTLY cannot run in a transaction: put it in its own file and flag it for review.",
                "- No backfills of hot tables here; ship a separate batched job.",
            ]
        )
