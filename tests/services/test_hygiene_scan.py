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
