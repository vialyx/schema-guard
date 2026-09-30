"""Run the service's own test suite (the command comes from schema-guard.yaml)."""

from __future__ import annotations

import shlex
import subprocess

from sg.checks.base import Check
from sg.core.models import CheckResult, Context, Finding, Status
from sg.core.pyenv import service_python


class TestsCheck(Check):
    name = "tests"
    order = 60

    def run(self, ctx: Context) -> CheckResult:
        cfg = ctx.policy.check_config(self.name)
        command = getattr(ctx.service, "tests", None)
        if not command:
            if cfg.get("required"):
                return CheckResult.skipped(self.name, "no test command configured for this service")
            return CheckResult(self.name, Status.WARN, "no test command configured for this service")
        argv = shlex.split(command)
        if argv[0] in ("python", "python3"):  # the service's Python, not the engine's
            argv = service_python(ctx.service_root, ctx.repo_root, getattr(ctx.service, "python", None)) + argv[1:]
        try:
            proc = subprocess.run(argv, cwd=ctx.service_root, capture_output=True, text=True,
                                  timeout=int(cfg.get("timeout_seconds", 300)))
        except subprocess.TimeoutExpired:
            return CheckResult(self.name, Status.ASK, "test suite timed out")
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
        if proc.returncode == 0:
            return CheckResult(self.name, Status.PASS, tail[0][:140])
        return CheckResult(self.name, Status.ASK, f"tests failed: {tail[0][:120]}", [Finding(
            "tests-failed", Status.ASK, "the service's test suite fails with this change",
            evidence="\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])[-800:],
            fix="fix the code or the tests before shipping",
        )])
