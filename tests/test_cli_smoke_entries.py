"""Run CLI entry points with a CP1252 console so help must set up UTF-8 itself."""

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rpgmaker import inventory

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = "rpgmaker.doctor"


def _probe(module, option):
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="cp1252", GT_NO_PROBE="1")
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-m", module, option], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )


def test_live_entries_print_help_on_a_cp1252_console():
    entries = {}
    for record in inventory.MODULES:
        for spelling in record.entry.split("/"):
            words = spelling.split()
            if words[:2] == ["python", "-m"]:
                assert record.status in ("active", "maintenance"), record
                entries[record.module] = words[2]

    modules = [module for name, module in entries.items() if name != DOCTOR]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda module: _probe(module, "--help"), modules))

    failures = [
        f"{module}: exit {result.returncode}\n{result.stdout[-1000:]}\n{result.stderr[-1000:]}"
        for module, result in zip(modules, results, strict=True)
        if result.returncode or "usage" not in result.stdout.lower()
    ]
    assert not failures, "\n\n".join(failures)
    assert any(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", result.stdout) for result in results), (
        "The CP1252 coverage must include help containing Japanese or Chinese text"
    )


def test_doctor_returns_json_on_a_cp1252_console():
    result = _probe(DOCTOR, "--json")
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload["ok"], bool)
    assert payload["tools"] and payload["checks"]
    assert all({"label", "ok", "detail"} <= check.keys() for check in payload["checks"])
