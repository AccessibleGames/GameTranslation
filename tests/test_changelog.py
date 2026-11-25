"""The changelog records the replacement for every retired tool."""

import re
from pathlib import Path

from rpgmaker import inventory


def test_deprecation_entries_match_the_inventory():
    changelog = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    section = re.search(r"^### 弃用\n(.*?)(?=^### |\Z)", changelog.read_text(encoding="utf-8"), re.M | re.S)
    assert section is not None
    entries = set(re.findall(r"\*\*`([^`]+)`\*\*\s*→\s*`[^`]+`", section[1]))
    recorded = {name.removesuffix(".py").replace("/", ".") for name in entries}
    retired = {record.module for record in inventory.MODULES if record.status in ("superseded", "dead")}
    assert recorded == retired
