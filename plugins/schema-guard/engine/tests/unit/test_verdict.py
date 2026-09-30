"""The verdict rules are the trust boundary; test them exhaustively."""

from pathlib import Path

import pytest

from sg.core.models import CheckResult, Finding, Status, Verdict
from sg.core.report import card
from sg.core.verdict import aggregate, build, codeowners_for


def chk(status: Status, name: str = "c") -> CheckResult:
    return CheckResult(name, status, summary=status.value)


def test_all_pass_is_go():
    assert aggregate([chk(Status.PASS), chk(Status.WARN)], {})[0] is Verdict.GO


@pytest.mark.parametrize("status,expected", [
    (Status.SKIPPED, Verdict.NEEDS_HUMAN),
    (Status.ASK, Verdict.NEEDS_HUMAN),
    (Status.REFUSE, Verdict.REFUSED),
])
def test_worst_check_sets_floor(status, expected):
    assert aggregate([chk(Status.PASS), chk(status)], {})[0] is expected


def test_no_checks_is_never_go():
    assert aggregate([], {})[0] is Verdict.NEEDS_HUMAN


def test_open_questions_block_go():
    v, reasons = aggregate([chk(Status.PASS)], {"questions": ["kg or lb?"]})
    assert v is Verdict.NEEDS_HUMAN and "question" in reasons[0]


def test_llm_can_escalate():
    v, _ = aggregate([chk(Status.PASS)], {"escalate": "REFUSED", "escalation_reason": "x"})
    assert v is Verdict.REFUSED


@pytest.mark.parametrize("escalate", ["GO", "NEEDS-HUMAN"])
def test_llm_cannot_lower(escalate):
    v, _ = aggregate([chk(Status.REFUSE)], {"escalate": escalate})
    assert v is Verdict.REFUSED


def test_codeowners_last_match_wins(tmp_path: Path):
    (tmp_path / "CODEOWNERS").write_text(
        "* @acme/eng\n/services/api/migrations/ @acme/data\n/services/api/app/recon.py @acme/fin\n"
    )
    assert codeowners_for(tmp_path, ["services/api/migrations/versions/0005_x.py"]) == ["@acme/data"]
    assert codeowners_for(tmp_path, ["services/api/app/recon.py", "README.md"]) == ["@acme/fin", "@acme/eng"]


def test_build_validates_and_renders(tmp_path: Path):
    f = Finding("float-on-money", Status.REFUSE, "price is float", "0005: ADD COLUMN", "use BIGINT cents")
    doc = build([CheckResult("policy", Status.REFUSE, "1 refuse", [f])], {"intent": "add price"},
                repo_root=tmp_path, migrations=["m.py"], tables=[{"name": "orders", "class": "core", "est_rows": 10}],
                policy_sources=[])
    assert doc["verdict"] == "REFUSED"
    md = card(doc)
    assert "REFUSED" in md and "use BIGINT cents" in md
