"""The pre-promotion hygiene scan does not ship.

It was written, and then nothing called it: no pass constructs it, no
``OutboundDestination`` member names a surface it could honestly be called
with, and the operator knob that configured its pattern set could not
change any observable behaviour.  A capability nothing reaches is not
delivered, so it was deleted rather than given a consumer built to make it
look reached.

This module holds the absence.  When a fire-prep writer that needs the scan
exists, the scan and this module come back together.
"""

from pathlib import Path

from kodezart.core.config import AppConfig
from kodezart.types.domain.gating import (
    HygieneCategory,
    RedactionCategory,
    ScanHit,
)

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "kodezart"


def test_no_hygiene_scan_ships() -> None:
    """Neither the service, nor the module, nor the knob that configured it."""
    mentions = sorted(
        path.relative_to(SRC_ROOT).as_posix()
        for path in SRC_ROOT.rglob("*.py")
        if "HygieneScan" in path.read_text(encoding="utf-8")
    )

    assert mentions == []
    assert not (SRC_ROOT / "services" / "hygiene_scan.py").exists()
    assert "hygiene_patterns" not in AppConfig.model_fields


def test_no_hygiene_category_collides_with_a_redaction_category() -> None:
    """``ScanCategory`` is a union; two enums sharing a value would be ambiguous."""
    hygiene = {member.value for member in HygieneCategory}
    redaction = {member.value for member in RedactionCategory}

    assert hygiene & redaction == set()


def test_a_hit_round_trips_under_either_half_of_the_union() -> None:
    """A serialized hit must come back as the category it went out as."""
    for member in (HygieneCategory.TRACKER_SHORTHAND, RedactionCategory.CREDENTIALS):
        restored = ScanHit.model_validate(ScanHit(category=member).model_dump())
        assert restored.category is member
