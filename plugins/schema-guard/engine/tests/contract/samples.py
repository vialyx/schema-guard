"""Sample projects for the adapter contract tests: one builder per registered adapter.

Every builder creates the same two-migration project in its framework's layout:
  1. create table widgets (id bigint primary key)
  2. add column note text

Adding an adapter? Add a builder here keyed by the adapter's `name`.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Callable

# -- alembic ------------------------------------------------------------------------

_ALEMBIC_INI = """\
[alembic]
script_location = migrations

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
"""

_ENV_PY = '''\
import os

from alembic import context
from sqlalchemy import create_engine

target_metadata = None
url = os.environ["DATABASE_URL"]


def run_migrations_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

_MAKO = '''\
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade():
    ${upgrades if upgrades else "pass"}


def downgrade():
    ${downgrades if downgrades else "pass"}
'''

_ALEMBIC_M1 = '''\
"""create widgets"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("widgets", sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False))


def downgrade():
    op.drop_table("widgets")
'''

_ALEMBIC_M2 = '''\
"""add note"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("widgets", sa.Column("note", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("widgets", "note")
'''


def build_alembic(tmp_path: Path) -> Path:
    root = tmp_path / "alembic_svc"
    versions = root / "migrations" / "versions"
    versions.mkdir(parents=True)
    (root / "alembic.ini").write_text(_ALEMBIC_INI)
    (root / "migrations" / "env.py").write_text(_ENV_PY)
    (root / "migrations" / "script.py.mako").write_text(_MAKO)
    (versions / "0001_create_widgets.py").write_text(_ALEMBIC_M1)
    (versions / "0002_add_note.py").write_text(_ALEMBIC_M2)
    return root


# -- raw sql ------------------------------------------------------------------------


def build_raw_sql(tmp_path: Path) -> Path:
    root = tmp_path / "raw_sql_svc"
    d = root / "migrations"
    d.mkdir(parents=True)
    files = {
        "V1__create_widgets.sql": "CREATE TABLE widgets (id bigint PRIMARY KEY);\n",
        "U1__create_widgets.sql": "DROP TABLE widgets;\n",
        "V2__add_note.sql": "-- free-form note\nALTER TABLE widgets ADD COLUMN note text;\n",
        "U2__add_note.sql": "ALTER TABLE widgets DROP COLUMN note;\n",
    }
    for name, body in files.items():
        (d / name).write_text(dedent(body))
    return root


SAMPLES: dict[str, Callable[[Path], Path]] = {
    "alembic": build_alembic,
    "raw_sql": build_raw_sql,
}
