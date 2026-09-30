"""Shared fixtures for contract tests: one throwaway Postgres server per test session."""

from __future__ import annotations

import pytest

from sg.dialects.postgres import PostgresDialect


@pytest.fixture(scope="session")
def pg_dialect() -> PostgresDialect:
    return PostgresDialect()


@pytest.fixture(scope="session")
def pg_server(pg_dialect):
    ok, why = PostgresDialect.available()
    if not ok:
        pytest.skip(why)
    server = pg_dialect.ephemeral_server()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def pg_dsn(pg_server, request):
    """A fresh empty database on the shared server, one per test."""
    return pg_server.create_database(request.node.name)
