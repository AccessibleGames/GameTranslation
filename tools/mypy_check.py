"""Run the repository's mypy check once and report its result."""

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Annotated

from rpgmaker import cliutil, proctools

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("rpgmaker", "kirikiri", "tyrano", "wolfrpg", "unity", "translation", "tools")
DIAGNOSTIC = re.compile(r"^(?P<file>.+?):\d+(?::\d+)?: error:")


def cmd(as_json: cliutil.JsonOut = False,
        update: Annotated[bool, cliutil.Option(
            "--update", help="verify the zero-error target")] = False,
        verbose: cliutil.Verbose = False,
        quiet: cliutil.Quiet = False,
        log_file: cliutil.LogFile = None) -> int:
    """Run mypy across the project. --update verifies the zero-error target."""
    cliutil.setup_logging(verbose, quiet, log_file)
    cliutil.own_paths("type-check the repository", repo=ROOT)
    try:
        result = proctools.run([sys.executable, "-m", "mypy", *PACKAGES], cwd=ROOT, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return cliutil.fail(f"could not start mypy: {error}")
    if result.returncode not in (0, 1):
        return cliutil.fail(f"mypy could not check the project: {proctools.tail(result)}")
    if update and result.returncode == 0:
        return 0

    output = result.stdout + result.stderr
    if as_json:
        # Preserve the existing report fields using the diagnostics from this run.
        with (ROOT / "pyproject.toml").open("rb") as handle:
            strict = set(tomllib.load(handle)["tool"]["gametranslation"]["mypy-strict"]["modules"])
        errors = []
        strict_problems = []
        for line in output.splitlines():
            if match := DIAGNOSTIC.match(line):
                errors.append(line)
                if match["file"].replace("\\", "/") in strict:
                    strict_problems.append(line)
        print(json.dumps({
            "errors": len(errors),
            "budget": 0,
            "strict_modules": len(strict),
            "strict_problems": strict_problems,
            "ok": result.returncode == 0,
        }, ensure_ascii=False, indent=2))
    else:
        print(output, end="" if output.endswith("\n") else "\n")
    return result.returncode


app = cliutil.command_app(cmd)


def main(argv=None) -> int:
    return cliutil.run(app, argv, prog="mypy_check.py")


if __name__ == "__main__":
    raise SystemExit(main())
