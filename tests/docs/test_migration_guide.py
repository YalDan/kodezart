"""The migration guide says what a v0.2 operation file gets (KOD-903).

Derived from the loader's own tables, so a default added there and not written
down here reddens this rather than reaching an operator unannounced.
"""

from pathlib import Path

from kodezart.adapters.toml_operation_config import (
    V02_IGNORED_TABLES,
    V02_MARKER_PREFIXES,
)

GUIDE = Path(__file__).resolve().parents[2] / "docs" / "migration-v0.2-to-v0.3.md"


def test_the_guide_names_every_v02_default_and_the_ignored_table() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    named = [
        *(f"`{purpose}`" for purpose in V02_MARKER_PREFIXES),
        *(f"`{marker}`" for marker in V02_MARKER_PREFIXES.values()),
        *(f"[[{table}]]" for table in V02_IGNORED_TABLES),
        "records.fire",
        "operation_file_v02_accepted",
    ]
    assert [name for name in named if name not in text] == []
