"""Run an Alembic command against ONE database, whatever the service's env.py does.

Usage: python alembic_runner.py <database-url> <alembic args...>

A stock env.py reads `sqlalchemy.url` from alembic.ini, which usually points at a
developer or shared database. This wrapper:
  1. overrides `sqlalchemy.url` (and $DATABASE_URL) with the throwaway database, and
  2. refuses any engine that would connect somewhere else (exit 97), before it connects.

It runs in the *service's* interpreter, so it imports only alembic and sqlalchemy.
"""

from __future__ import annotations

import os
import sys

# Run as a script, this directory is sys.path[0], and its alembic.py (our adapter) would shadow the package.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]

GUARD_EXIT = 97


def _same_target(a, b) -> bool:
    return (a.host or "localhost", a.port or 5432, a.database) == (b.host or "localhost", b.port or 5432, b.database)


def _install_guard(allowed_url: str) -> None:
    import sqlalchemy
    import sqlalchemy.engine
    import sqlalchemy.engine.create as create_mod
    from sqlalchemy.engine import make_url

    allowed = make_url(allowed_url)
    real_create_engine = create_mod.create_engine

    def guarded_create_engine(url, *args, **kwargs):
        target = make_url(url)
        if not _same_target(target, allowed):
            sys.stderr.write(
                "schema-guard: refusing to run migrations against "
                f"{target.render_as_string(hide_password=True)}; env.py ignored the throwaway database. "
                "Make env.py read config.get_main_option('sqlalchemy.url') or $DATABASE_URL.\n"
            )
            sys.exit(GUARD_EXIT)
        return real_create_engine(url, *args, **kwargs)

    # engine_from_config() looks create_engine up in its own module; env.py may import either name.
    create_mod.create_engine = guarded_create_engine
    sqlalchemy.create_engine = guarded_create_engine
    sqlalchemy.engine.create_engine = guarded_create_engine


def main(argv: list[str]) -> int:
    url, *alembic_args = argv
    os.environ["DATABASE_URL"] = url
    _install_guard(url)

    from alembic.config import CommandLine, Config

    cli = CommandLine(prog="alembic")
    options = cli.parser.parse_args(alembic_args)
    if hasattr(cli, "_inis_from_config"):  # alembic >= 1.16: -c may repeat, pyproject.toml supported
        toml, ini = cli._inis_from_config(options)
        cfg = Config(file_=ini, toml_file=toml, ini_section=options.name, cmd_opts=options)
    else:
        cfg = Config(file_=options.config, ini_section=options.name, cmd_opts=options)
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cli.run_cmd(cfg, options)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
