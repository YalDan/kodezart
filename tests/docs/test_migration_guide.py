"""The migration guide says what a v0.2 operation file gets (KOD-903).

Scans section 4b of the guide, the one that says it: each purpose beside its
marker on one table row, the condition under which those markers are supplied,
each ignored table, the boot event, and the fire-log bullet naming
``records.fire`` with each structure field a ``RecordDestination`` declares.
The purposes, markers, tables and fields are read from the loader and the
model, never listed here.
"""

import re
from pathlib import Path

from kodezart.adapters.toml_operation_config import (
    V02_IGNORED_TABLES,
    V02_MARKER_PREFIXES,
)
from kodezart.types.domain.operation import RecordDestination

GUIDE = Path(__file__).resolve().parents[2] / "docs" / "migration-v0.2-to-v0.3.md"

#: The fields a record destination may leave out and declares its structure
#: in: absent both, a fire log is written in the v0.2 shape.
STRUCTURE_FIELDS = tuple(
    name
    for name, field in RecordDestination.model_fields.items()
    if not field.is_required() and field.default is None
)


def _section_4b() -> str:
    text = GUIDE.read_text(encoding="utf-8")
    return text.split("\n## 4b.", 1)[1].split("\n## 4c.", 1)[0]


def _bullets(section: str) -> list[str]:
    """Each top-level bullet, with its indented continuation lines."""
    return [
        match.group(0)
        for match in re.finditer(r"^- .*?(?=^- |^\S|\Z)", section, re.M | re.S)
    ]


def test_the_guide_names_every_v02_default_and_the_ignored_table() -> None:
    section = _section_4b()

    assert "declares no `[marker_prefixes]` table" in section
    for purpose, marker in V02_MARKER_PREFIXES.items():
        assert f"| `{purpose}` | `{marker}` |" in section, purpose
    for table in V02_IGNORED_TABLES:
        assert f"[[{table}]]" in section, table
    assert "operation_file_v02_accepted" in section


def test_the_guide_says_a_fire_log_with_no_structure_gets_the_v02_row() -> None:
    assert STRUCTURE_FIELDS, "no optional structure field, so this guards nothing"

    fire_log = [
        bullet
        for bullet in _bullets(_section_4b())
        if "`records.fire`" in bullet
        and all(f"`{field}`" in bullet for field in STRUCTURE_FIELDS)
    ]

    assert len(fire_log) == 1, fire_log
