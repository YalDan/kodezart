"""KOD-133 — a probe failure is legible after the fact.

The A/B smoke used to reduce every exception to the first line of its
message at the moment of capture: for a validation error that is the
header, and the field path and offending value on the following lines
were destroyed with the traceback.  The capture now records all three
alongside the unchanged one-line summary, and this module pins each
verification bullet without a live engine.
"""

import pytest
from pydantic import BaseModel, ValidationError

from tests.probes.test_ab_smoke import failure_diagnostics


class _Sample(BaseModel):
    slug: str


def _validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as excinfo:
        _Sample.model_validate({"slug": 7})
    return excinfo.value


def test_a_validation_error_records_the_field_and_the_value() -> None:
    """The lines the header omits — field path and offending value — survive."""
    diagnostics = failure_diagnostics(_validation_error())
    assert diagnostics["failure"].startswith(
        "ValidationError: 1 validation error for _Sample"
    )
    assert "\n" not in diagnostics["failure"]
    assert "slug" in diagnostics["exception"].splitlines()[1]
    assert "input_value=7" in diagnostics["exception"]


def test_the_one_line_summary_is_unchanged_in_shape() -> None:
    """Exactly the string the paired record consumed before: line one."""
    raised = _validation_error()
    diagnostics = failure_diagnostics(raised)
    assert (
        diagnostics["failure"] == f"{type(raised).__name__}: {raised}".splitlines()[0]
    )


def test_a_non_validation_failure_records_its_traceback() -> None:
    """The frames survive capture, so a later reader has a traceback to open."""

    def _raise() -> None:
        msg = "the arm died outside validation"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError) as excinfo:
        _raise()

    diagnostics = failure_diagnostics(excinfo.value)
    assert diagnostics["failure"] == "RuntimeError: the arm died outside validation"
    assert diagnostics["traceback"].startswith("Traceback (most recent call last)")
    assert "_raise" in diagnostics["traceback"]
