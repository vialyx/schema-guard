"""SQL parsing helpers shared by rules and checks (sqlglot-based, dialect-aware)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlglot
import sqlglot.expressions as exp

from sg.core.models import Finding, Status
from sg.rules.base import Statement

if TYPE_CHECKING:
    from sg.core.models import Migration
    from sg.core.policy import Policy
    from sg.rules.base import Rule

# Alembic offline output wraps things we don't care about.
_NOISE = re.compile(r"^\s*(BEGIN|COMMIT|INSERT INTO alembic_version|UPDATE alembic_version|DELETE FROM alembic_version|CREATE TABLE alembic_version)\b", re.I)


def split(sql: str) -> list[str]:
    """Split a script into statements, dropping comments-only chunks and migration-tool noise."""
    parts: list[str] = []
    for chunk in _split_naive(sql):
        body = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--")).strip()
        if body and not _NOISE.match(body):
            parts.append(body)
    return parts


def _split_naive(sql: str) -> list[str]:
    """Split on semicolons outside quotes and dollar-quoted bodies."""
    out, buf, i, n = [], [], 0, len(sql)
    quote: str | None = None
    while i < n:
        ch = sql[i]
        if quote:
            if sql.startswith(quote, i):
                buf.append(quote)
                i += len(quote)
                quote = None
                continue
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "$":
            m = re.match(r"\$[A-Za-z_]*\$", sql[i:])
            if m:
                quote = m.group(0)
                buf.append(quote)
                i += len(quote)
                continue
        elif ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            buf.append(sql[i:j])
            i = j
            continue
        elif ch == ";":
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return out


def parse(sql: str, dialect: str) -> list[Statement]:
    stmts: list[Statement] = []
    # sqlglot logs a warning for every statement it falls back to `Command` for.
    # Callers report those themselves (see is_unparsed), so keep stderr clean.
    log = logging.getLogger("sqlglot")
    previous = log.level
    log.setLevel(logging.ERROR)
    try:
        for idx, text in enumerate(split(sql)):
            try:
                tree = sqlglot.parse_one(text, read=dialect or None)
            except Exception:
                tree = None
            stmts.append(Statement(sql=text, tree=tree, index=idx))
    finally:
        log.setLevel(previous)
    return stmts


def is_unparsed(stmt: Statement) -> bool:
    """sqlglot either failed or gave up and wrapped the text in an opaque Command."""
    return stmt.tree is None or isinstance(stmt.tree, exp.Command)


@dataclass(frozen=True)
class Touch:
    table: str
    column: str | None
    op: str  # add_column | drop_column | rename_column | rename_table | alter_type | set_not_null | add_constraint | drop_table | create_table | create_index | update | delete | truncate | other


@dataclass(frozen=True)
class AlterAction:
    """One comma-separated action of an ALTER TABLE, with its raw (whitespace-normalised) text."""

    touch: Touch
    text: str


def touches(stmts: list[Statement]) -> list[Touch]:
    """Best-effort list of (table, column, op) a migration touches.

    Regex-based on normalised statement text: DDL coverage in SQL parsers is
    uneven across dialects, and a regex that misses is easier to reason about
    than a parser that mis-parses. Callers must treat this as a hint.
    """
    found: list[Touch] = []
    for s in stmts:
        found.extend(_regex_touches(s.sql))
    # de-duplicate, keep order
    seen, out = set(), []
    for t in found:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


_IDENT = r'"?([A-Za-z_][\w$]*)"?'
_QUAL = rf'(?:{_IDENT}\.)?{_IDENT}'
# Postgres allows IF EXISTS and ONLY in that order; accept either order.
_ALTER_TABLE = re.compile(rf"ALTER TABLE (?:(?:IF EXISTS|ONLY) )*{_QUAL} (.*)$", re.I)
_CREATE_TABLE = re.compile(
    rf"CREATE (?:OR REPLACE )?(?:(?:GLOBAL|LOCAL) )?(?:(?:TEMP|TEMPORARY|UNLOGGED) )?TABLE (?:IF NOT EXISTS )?{_QUAL}", re.I
)
_TABLE_LIST_END = re.compile(r"\s+(?:CASCADE|RESTRICT|RESTART IDENTITY|CONTINUE IDENTITY)\b.*$", re.I)
# Words that end a column's type in a column definition.
# A type name: letters then letters, digits or spaces (float8, double precision, int4).
_TYPE = r"[A-Za-z][A-Za-z0-9 ]*?"
_TYPE_END = r"(?:\(|\[|\s+(?:NOT|NULL|DEFAULT|CHECK|REFERENCES|CONSTRAINT|UNIQUE|PRIMARY|GENERATED|COLLATE|USING)\b|,|\)|$)"
_TABLE_CONSTRAINT_WORDS = {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "EXCLUDE", "LIKE"}


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def alter_table_actions(sql: str) -> list[AlterAction]:
    """Split an ALTER TABLE into its actions; empty for any other statement."""
    m = _ALTER_TABLE.match(_norm(sql))
    if not m:
        return []
    table = m.group(2)
    return [AlterAction(_action_touch(table, a.strip()), a.strip()) for a in _split_actions(m.group(3)) if a.strip()]


def _action_touch(table: str, a: str) -> Touch:
    if mm := re.match(rf"ADD (?:COLUMN )?(?:IF NOT EXISTS )?{_IDENT}", a, re.I):
        if mm.group(1).upper() not in _TABLE_CONSTRAINT_WORDS:
            return Touch(table, mm.group(1), "add_column")
    if mm := re.match(rf"DROP (?:COLUMN )?(?:IF EXISTS )?{_IDENT}", a, re.I):
        if mm.group(1).upper() != "CONSTRAINT":
            return Touch(table, mm.group(1), "drop_column")
    if re.match(rf"RENAME TO {_IDENT}", a, re.I):
        return Touch(table, None, "rename_table")
    if mm := re.match(rf"RENAME (?:COLUMN )?{_IDENT} TO {_IDENT}", a, re.I):
        return Touch(table, mm.group(1), "rename_column")
    if mm := re.match(rf"ALTER (?:COLUMN )?{_IDENT} (?:SET DATA )?TYPE ", a, re.I):
        return Touch(table, mm.group(1), "alter_type")
    if mm := re.match(rf"ALTER (?:COLUMN )?{_IDENT} SET NOT NULL", a, re.I):
        return Touch(table, mm.group(1), "set_not_null")
    if re.match(rf"ADD (?:CONSTRAINT {_IDENT} )?(?:CHECK|UNIQUE|FOREIGN KEY|EXCLUDE)\b", a, re.I):
        return Touch(table, None, "add_constraint")  # restricts what every existing writer may write
    return Touch(table, None, "other")


def _table_list(rest: str) -> list[str]:
    """`a, public.b CASCADE` -> ['a', 'b']."""
    out = []
    for part in _TABLE_LIST_END.sub("", rest).split(","):
        if m := re.match(rf"\s*(?:ONLY )?{_QUAL}", part, re.I):
            out.append(m.group(2))
    return out


def _regex_touches(sql: str) -> list[Touch]:
    s = _norm(sql)
    if _ALTER_TABLE.match(s):
        return [a.touch for a in alter_table_actions(s)]
    if m := re.match(r"DROP TABLE (?:IF EXISTS )?(.*)$", s, re.I):
        return [Touch(t, None, "drop_table") for t in _table_list(m.group(1))]
    if m := _CREATE_TABLE.match(s):
        return [Touch(m.group(2), None, "create_table")]
    if m := re.match(rf"CREATE (?:UNIQUE )?INDEX (?:CONCURRENTLY )?(?:IF NOT EXISTS )?(?:{_IDENT} )?ON (?:ONLY )?{_QUAL}", s, re.I):
        return [Touch(m.group(3), None, "create_index")]
    if m := re.match(r"TRUNCATE (?:TABLE )?(.*)$", s, re.I):
        return [Touch(t, None, "truncate") for t in _table_list(m.group(1))]
    if m := re.match(rf"UPDATE (?:ONLY )?{_QUAL}", s, re.I):
        return [Touch(m.group(2), None, "update")]
    if m := re.match(rf"DELETE FROM (?:ONLY )?{_QUAL}", s, re.I):
        return [Touch(m.group(2), None, "delete")]
    return []


def _split_actions(actions: str) -> list[str]:
    """Split 'ADD COLUMN a int, DROP COLUMN b' on top-level commas."""
    out, depth, buf = [], 0, []
    for ch in actions:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def column_type(sql: str, column: str) -> str | None:
    """Declared type of `column` in an ADD COLUMN / ALTER TYPE / CREATE TABLE statement."""
    s = _norm(sql)
    col = re.escape(column)
    for pat in (
        rf'ADD (?:COLUMN )?(?:IF NOT EXISTS )?"?{col}"? ({_TYPE}){_TYPE_END}',
        rf'ALTER (?:COLUMN )?"?{col}"? (?:SET DATA )?TYPE ({_TYPE}){_TYPE_END}',
        rf'[(,]\s*"?{col}"? ({_TYPE}){_TYPE_END}',
    ):
        if m := re.search(pat, s, re.I):
            return m.group(1).strip().lower()
    return None


def create_table_columns(sql: str) -> tuple[str, list[tuple[str, str | None]]] | None:
    """(table, [(column, type)]) for a CREATE TABLE with a column list; None otherwise."""
    s = _norm(sql)
    m = _CREATE_TABLE.match(s)
    if not m:
        return None
    rest = s[m.end():].lstrip()
    if not rest.startswith("("):
        return (m.group(2), [])  # CREATE TABLE ... AS SELECT / PARTITION OF
    body, depth = [], 0
    for ch in rest:
        depth += ch == "("
        depth -= ch == ")"
        body.append(ch)
        if depth == 0:
            break
    cols: list[tuple[str, str | None]] = []
    for element in _split_actions("".join(body)[1:-1]):
        e = element.strip()
        name = re.match(_IDENT, e)
        if not name or name.group(1).upper() in _TABLE_CONSTRAINT_WORDS:
            continue
        typ = re.match(rf'{_IDENT} ({_TYPE}){_TYPE_END}', e, re.I)
        cols.append((name.group(1), typ.group(2).strip().lower() if typ else None))
    return (m.group(2), cols)


# --- UPDATE / DELETE scope ----------------------------------------------------

def where_clause(stmt: Statement) -> str | None:
    """Text of the top-level WHERE of an UPDATE/DELETE, or None if there is none."""
    if isinstance(stmt.tree, (exp.Update, exp.Delete)):
        where = stmt.tree.args.get("where")
        return where.this.sql() if where is not None else None
    s = _norm(stmt.sql)
    start = _top_level_keyword(s, "WHERE")
    if start is None:
        return None
    body = s[start + len("WHERE"):]
    end = _top_level_keyword(body, "RETURNING")
    return (body[:end] if end is not None else body).strip()


def _top_level_keyword(s: str, keyword: str) -> int | None:
    """Offset of `keyword` outside parentheses and quotes, or None."""
    depth, quote = 0, None
    pat = re.compile(rf"\b{keyword}\b", re.I)
    for i, ch in enumerate(s):
        if quote:
            quote = None if ch == quote else quote
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and pat.match(s, i) and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] == "_")):
            return i
    return None


_LOWER = re.compile(r"(?<![<\-=])>=?")  # >, >= (not <>, ->, =>)
_UPPER = re.compile(r"<(?![>@])=?")  # <, <= (not <>, <@)
_KEY_EQ = re.compile(r"(?<![\w.])(?:\w+\.)?\"?id\"?\s*(?:=\s*[\w'$:%]|IN\s*\((?!\s*SELECT))", re.I)


def is_bounded(stmt: Statement) -> bool:
    """Does this UPDATE/DELETE touch a bounded batch (key range, key list or LIMIT)?

    Heuristic: LIMIT anywhere (e.g. `WHERE id IN (SELECT id ... LIMIT 10000)`),
    BETWEEN, a lower plus an upper comparison, or equality/IN-list on `id`.
    """
    if re.search(r"\bLIMIT\s+\d+", stmt.sql, re.I):
        return True
    where = where_clause(stmt)
    if not where:
        return False
    if re.search(r"\bBETWEEN\b", where, re.I):
        return True
    if _LOWER.search(where) and _UPPER.search(where):
        return True
    return bool(_KEY_EQ.search(where))


# --- helpers shared by rules --------------------------------------------------

def bare(table: str) -> str:
    """Case-folded table name without schema or quotes (for comparisons)."""
    return table.split(".")[-1].strip('"').lower()


def new_tables(stmts: list[Statement]) -> set[str]:
    """Tables this migration creates (their first touch is CREATE TABLE).

    A table dropped and re-created in the same migration is *not* new: its
    first touch is the DROP, so the rebuild is still judged.
    """
    first: dict[str, str] = {}
    for t in touches(stmts):
        first.setdefault(bare(t.table), t.op)
    return {name for name, op in first.items() if op == "create_table"}


def large_or_unknown(policy: "Policy", table: str) -> bool:
    """Hot, or of unknown size and not declared scratch: assume a long lock hurts."""
    if policy.is_hot(table):
        return True
    return policy.est_rows(table) is None and policy.table_class(table) != "scratch"


def evidence(migration: "Migration", stmt: Statement) -> str:
    return f"{migration.path.name}: {_norm(stmt.sql)[:120]}"


def finding(rule: "Rule", natural: Status, message: str, evidence: str = "", fix: str = "") -> Finding:
    """A finding from `rule` whose unconfigured severity is `natural`.

    A `severity:` override in the rule's config sets the rule's headline
    outcome and caps every other tier (so `severity: warn` can never be
    out-ranked by the same rule's secondary findings).
    """
    if "severity" not in rule.config:
        severity = natural
    elif natural is rule.default_severity:
        severity = rule.severity
    else:
        severity = min(natural, rule.severity, key=lambda s: s.rank)
    return Finding(rule=rule.id, severity=severity, message=message, evidence=evidence, fix=fix)


__all__ = [
    "split", "parse", "is_unparsed", "touches", "Touch", "AlterAction", "alter_table_actions",
    "column_type", "create_table_columns", "where_clause", "is_bounded", "bare", "new_tables",
    "large_or_unknown", "evidence", "finding", "exp",
]
