"""PostgreSQL dialect: throwaway servers, schema snapshots and table descriptions.

Two ways to get a scratch server:

* `SG_DATABASE_URL` points at an existing server (CI service container, docker).
  Each `create_database` makes a uniquely named database there and `stop()` drops them.
* Otherwise we look for local PostgreSQL binaries and `initdb` a private cluster in a
  temp dir, listening only on a unix socket, with fsync off. It is torn down on `stop()`
  and, as a safety net, at interpreter exit.
"""

from __future__ import annotations

import atexit
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
from glob import glob
from pathlib import Path
from typing import Any

from sg.dialects.base import Dialect, EphemeralServer

ENV_URL = "SG_DATABASE_URL"
ENV_BIN = "SG_PG_BIN"
_BIN_GLOBS = (
    "/opt/homebrew/opt/postgresql@*/bin",
    "/usr/local/opt/postgresql@*/bin",
    "/usr/lib/postgresql/*/bin",
)
#: tables owned by migration tools, never part of the application schema
_IGNORED_TABLES = ("alembic_version", "schema_guard_history")


def _has_tools(d: Path) -> bool:
    return all((d / t).is_file() and os.access(d / t, os.X_OK) for t in ("initdb", "pg_ctl"))


def _version_of(path: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", Path(path).parent.name)
    return tuple(int(n) for n in nums) or (0,)


def find_pg_bin() -> Path | None:
    """Directory with initdb + pg_ctl: PATH, then $SG_PG_BIN, then well-known installs."""
    initdb = shutil.which("initdb")
    if initdb and shutil.which("pg_ctl"):
        return Path(initdb).resolve().parent
    if os.environ.get(ENV_BIN):
        d = Path(os.environ[ENV_BIN])
        if _has_tools(d):
            return d
    for pattern in _BIN_GLOBS:
        for cand in sorted(glob(pattern), key=_version_of, reverse=True):
            if _has_tools(Path(cand)):
                return Path(cand)
    return None


def _engine(dsn: str, **kw):
    from sqlalchemy import create_engine

    return create_engine(dsn, **kw)


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower())[:40] or "db"


# --------------------------------------------------------------------------------------
# servers


class ExistingServer(EphemeralServer):
    """Scratch databases on a server we don't own (SG_DATABASE_URL)."""

    def __init__(self, url: str):
        from sqlalchemy.engine import make_url

        self.url = make_url(url)
        if self.url.drivername in ("postgresql", "postgres"):
            self.url = self.url.set(drivername="postgresql+psycopg")
        self.created: list[str] = []
        atexit.register(self.stop)

    def _admin(self):
        return _engine(self.url, isolation_level="AUTOCOMMIT")

    def create_database(self, name: str) -> str:
        db = f"sg_{_safe_name(name)}_{secrets.token_hex(3)}"
        eng = self._admin()
        try:
            with eng.connect() as c:
                c.exec_driver_sql(f'CREATE DATABASE "{db}"')
        finally:
            eng.dispose()
        self.created.append(db)
        return self.url.set(database=db).render_as_string(hide_password=False)

    def stop(self) -> None:
        if not self.created:
            return
        eng = self._admin()
        try:
            with eng.connect() as c:
                for db in self.created:
                    c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        finally:
            eng.dispose()
        self.created = []


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class InitdbServer(EphemeralServer):
    """A private cluster created with initdb, reachable only through a unix socket."""

    def __init__(self, bin_dir: Path):
        self.bin = Path(bin_dir)
        self.data_dir = Path(tempfile.mkdtemp(prefix="sg-pgdata-"))
        # Unix socket paths are limited to ~100 chars and $TMPDIR can be long (macOS).
        self.sock_dir = Path(tempfile.mkdtemp(prefix="sgpg", dir="/tmp"))
        self.port = _free_port()
        self.log = self.data_dir.parent / f"{self.data_dir.name}.log"
        self._running = False
        atexit.register(self.stop)
        try:
            self._start()
        except Exception:
            self.stop()
            raise

    def _cmd(self, *args: str, what: str) -> None:
        env = dict(os.environ, LC_ALL="C", LANG="C")
        proc = subprocess.run(args, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            log = ""
            if self.log.exists():
                log = "\n--- server log ---\n" + self.log.read_text()[-2000:]
            raise RuntimeError(
                f"{what} failed (exit {proc.returncode}): {' '.join(args)}\n"
                f"{proc.stderr.strip() or proc.stdout.strip()}{log}"
            )

    def _start(self) -> None:
        self._cmd(
            str(self.bin / "initdb"), "-D", str(self.data_dir), "-A", "trust", "-U", "postgres",
            "--no-sync", "-E", "UTF8", "--locale=C",
            what="initdb",
        )
        opts = (
            f"-p {self.port} -k {self.sock_dir} -c listen_addresses='' -c fsync=off"
            " -c synchronous_commit=off -c full_page_writes=off"
        )
        self._cmd(
            str(self.bin / "pg_ctl"), "-w", "-t", "60", "-D", str(self.data_dir),
            "-l", str(self.log), "-o", opts, "start",
            what="pg_ctl start",
        )
        self._running = True

    def dsn(self, db: str = "postgres") -> str:
        return f"postgresql+psycopg://postgres@/{db}?host={self.sock_dir}&port={self.port}"

    def create_database(self, name: str) -> str:
        db = f"sg_{_safe_name(name)}_{secrets.token_hex(3)}"
        eng = _engine(self.dsn(), isolation_level="AUTOCOMMIT")
        try:
            with eng.connect() as c:
                c.exec_driver_sql(f'CREATE DATABASE "{db}"')
        finally:
            eng.dispose()
        return self.dsn(db)

    def stop(self) -> None:
        if self._running:
            subprocess.run(
                [str(self.bin / "pg_ctl"), "stop", "-m", "fast", "-w", "-D", str(self.data_dir)],
                capture_output=True,
            )
            self._running = False
        for p in (self.data_dir, self.sock_dir):
            shutil.rmtree(p, ignore_errors=True)
        try:
            self.log.unlink(missing_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------------------
# dialect

_COLUMNS_SQL = """
SELECT c.table_name, c.column_name, c.ordinal_position,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       c.is_nullable = 'YES' AS nullable,
       c.column_default
FROM information_schema.columns c
JOIN pg_catalog.pg_attribute a
  ON a.attrelid = (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass
 AND a.attname = c.column_name
WHERE c.table_schema = 'public'
"""

_INDEXES_SQL = """
SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'
"""

_CONSTRAINTS_SQL = """
SELECT cl.relname AS table_name, con.conname, con.contype,
       pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class cl ON cl.oid = con.conrelid
JOIN pg_namespace n ON n.oid = cl.relnamespace
WHERE n.nspname = 'public'
"""

_TABLES_SQL = """
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
"""


class PostgresDialect(Dialect):
    name = "postgres"
    sqlglot_dialect = "postgres"
    inexact_numeric_types = frozenset(
        {"real", "float", "float4", "float8", "double precision", "double"}
    )

    @classmethod
    def available(cls) -> tuple[bool, str]:
        if os.environ.get(ENV_URL):
            return True, ENV_URL
        d = find_pg_bin()
        if d is not None:
            return True, f"local initdb ({d})"
        return False, "no Postgres found: set SG_DATABASE_URL or install PostgreSQL"

    def ephemeral_server(self) -> EphemeralServer:
        url = os.environ.get(ENV_URL)
        if url:
            return ExistingServer(url)
        d = find_pg_bin()
        if d is None:
            raise RuntimeError(self.available()[1])
        return InitdbServer(d)

    # -- introspection ---------------------------------------------------------------

    @staticmethod
    def _query(conn, sql: str, **params) -> list[dict[str, Any]]:
        from sqlalchemy import text

        return [dict(r._mapping) for r in conn.execute(text(sql), params)]

    def schema_snapshot(self, dsn: str) -> dict[str, Any]:
        eng = _engine(dsn)
        try:
            with eng.connect() as conn:
                tables = {r["table_name"] for r in self._query(conn, _TABLES_SQL)}
                cols = self._query(conn, _COLUMNS_SQL)
                idx = self._query(conn, _INDEXES_SQL)
                cons = self._query(conn, _CONSTRAINTS_SQL)
        finally:
            eng.dispose()

        snap: dict[str, Any] = {}
        for t in sorted(tables - set(_IGNORED_TABLES)):
            snap[t] = {"columns": [], "indexes": [], "constraints": []}
        for c in sorted(cols, key=lambda r: (r["table_name"], r["column_name"])):
            if c["table_name"] in snap:
                snap[c["table_name"]]["columns"].append(
                    {
                        "name": c["column_name"],
                        "data_type": c["data_type"],
                        "is_nullable": bool(c["nullable"]),
                        "default": c["column_default"],
                    }
                )
        for i in sorted(idx, key=lambda r: (r["tablename"], r["indexname"])):
            if i["tablename"] in snap:
                snap[i["tablename"]]["indexes"].append(
                    {"name": i["indexname"], "definition": i["indexdef"]}
                )
        for k in sorted(cons, key=lambda r: (r["table_name"], r["conname"])):
            if k["table_name"] in snap:
                snap[k["table_name"]]["constraints"].append(
                    {"name": k["conname"], "type": k["contype"], "definition": k["definition"]}
                )
        return {"tables": snap}

    def describe_tables(self, dsn: str, tables: list[str]) -> dict[str, Any]:
        eng = _engine(dsn)
        out: dict[str, Any] = {}
        try:
            with eng.connect() as conn:
                existing = {r["table_name"] for r in self._query(conn, _TABLES_SQL)}
                for t in tables:
                    if t not in existing:
                        out[t] = {"exists": False}
                        continue
                    cols = self._query(
                        conn,
                        _COLUMNS_SQL + " AND c.table_name = :t"
                        " ORDER BY c.ordinal_position",
                        t=t,
                    )
                    idx = self._query(
                        conn, _INDEXES_SQL + " AND tablename = :t ORDER BY indexname", t=t
                    )
                    cons = self._query(
                        conn, _CONSTRAINTS_SQL + " AND cl.relname = :t ORDER BY con.conname", t=t
                    )
                    count = conn.exec_driver_sql(
                        f'SELECT count(*) FROM public."{t.replace(chr(34), chr(34) * 2)}"'
                    ).scalar()
                    out[t] = {
                        "exists": True,
                        "row_count": int(count or 0),
                        "columns": [
                            {
                                "name": c["column_name"],
                                "type": c["data_type"],
                                "nullable": bool(c["nullable"]),
                                "default": c["column_default"],
                            }
                            for c in cols
                        ],
                        "indexes": [i["indexdef"] for i in idx],
                        "constraints": [
                            {"name": k["conname"], "type": k["contype"], "definition": k["definition"]}
                            for k in cons
                        ],
                    }
        finally:
            eng.dispose()
        return out

    def row_estimate(self, dsn: str, table: str) -> int | None:
        from sqlalchemy import text

        eng = _engine(dsn)
        try:
            with eng.connect() as conn:
                v = conn.execute(
                    text(
                        "SELECT c.reltuples::bigint FROM pg_class c"
                        " JOIN pg_namespace n ON n.oid = c.relnamespace"
                        " WHERE n.nspname = 'public' AND c.relname = :t AND c.relkind IN ('r','p')"
                    ),
                    {"t": table},
                ).scalar()
        except Exception:
            return None
        finally:
            eng.dispose()
        # -1 means "never analyzed" on PG14+
        return None if v is None or v < 0 else int(v)
