"""Check the type gate's exit status and report."""

import json
from subprocess import CompletedProcess

import pytest

from tools import mypy_check


@pytest.mark.parametrize(("status", "output"), [
    (0, "Success: no issues found\n"),
    (1, "rpgmaker/settings.py:10: error: Fixture failure\n"),
])
def test_json_report_from_one_check(monkeypatch, capsys, status, output):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return CompletedProcess(argv, status, output, "")

    monkeypatch.setattr(mypy_check.proctools, "run", run)

    assert mypy_check.main(["--json"]) == status
    assert len(calls) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is (status == 0)
    assert report["errors"] == status
    assert report["strict_problems"] == output.splitlines() * status


def test_checker_failure_is_reported(monkeypatch, capsys):
    def fail(argv, **_kwargs):
        return CompletedProcess(argv, 2, "", "invalid mypy configuration")

    monkeypatch.setattr(mypy_check.proctools, "run", fail)
    assert mypy_check.main([]) == 1
    assert "invalid mypy configuration" in capsys.readouterr().err
