"""docs/howto/index.md lists every how-to, each with a working link and a description."""

import re
from pathlib import Path

HOWTO = Path(__file__).resolve().parent.parent / "docs" / "howto"


def _rows() -> dict[str, str]:
    rows = {}
    for line in (HOWTO / "index.md").read_text().splitlines():
        m = re.match(r"\| \[([^\]]+)\]\(([^)]+\.md)\) \| (.+) \|$", line)
        if m:
            rows[m.group(2)] = m.group(3).strip()
    return rows


def test_every_howto_is_in_the_index():
    rows = _rows()
    files = {p.name for p in HOWTO.glob("*.md")} - {"index.md"}
    assert files - rows.keys() == set(), "add a row to docs/howto/index.md"
    assert rows.keys() - files == set(), "docs/howto/index.md lists a how-to that does not exist"
    assert all(rows.values())
