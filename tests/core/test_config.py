"""Configuration criteria — the fan-out cap, and the two flipped defaults.

KOD-89-AC-5: every numeric constant in this project is an ``AppConfig``
field with the ``KODEZART_`` prefix, so the cap is asserted the same way —
it reads from the environment, it refuses values outside its declared range
at construction, and the range itself is the one the fire-time ruling
recorded.

KOD-93-AC-1: the two defaults the flip moved, read with a clean environment.
A default is only a default when nothing else is speaking, which is what
the pristine fixture is for: a developer's exported variable would otherwise
make this suite agree with whatever is already configured.
"""

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.types.domain.ticket_review import TicketReviewMode

ENV_NAME = "KODEZART_INVESTIGATION_CAP"
#: Floor and ceiling per the KOD-89 fire-time ruling FR-3: the floor keeps
#: the rendered spec coherent, the ceiling is twice the measured width of
#: the prose protocol the set replaces.
CAP_FLOOR = 1
CAP_CEILING = 10


@pytest.mark.usefixtures("_pristine_environment")
def test_the_cap_defaults_to_the_width_the_replaced_protocol_ran_at() -> None:
    """Five: the count the prose dispatch protocol actually instructed."""
    assert AppConfig().investigation_cap == 5


@pytest.mark.usefixtures("_pristine_environment")
def test_the_cap_is_read_from_the_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The knob is addressable the way every other knob is."""
    monkeypatch.setenv(ENV_NAME, "7")
    assert AppConfig().investigation_cap == 7


@pytest.mark.usefixtures("_pristine_environment")
@pytest.mark.parametrize("value", [CAP_FLOOR - 1, CAP_CEILING + 1, -3])
def test_an_out_of_range_cap_raises_at_construction(
    monkeypatch: pytest.MonkeyPatch,
    value: int,
) -> None:
    """Out of range fails at boot, never silently clamps to an edge."""
    monkeypatch.setenv(ENV_NAME, str(value))
    with pytest.raises(ValidationError) as excinfo:
        AppConfig()
    assert "investigation_cap" in str(excinfo.value)


@pytest.mark.usefixtures("_pristine_environment")
@pytest.mark.parametrize("value", [CAP_FLOOR, CAP_CEILING])
def test_both_bounds_are_themselves_accepted(
    monkeypatch: pytest.MonkeyPatch,
    value: int,
) -> None:
    """Non-vacuity: the range is inclusive, so the edges are not failures."""
    monkeypatch.setenv(ENV_NAME, str(value))
    assert AppConfig().investigation_cap == value


# ---------------------------------------------------------------------------
# KOD-93-AC-1 — the two defaults the flip moved
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_pristine_environment")
def test_default_prompt_set_is_anthropic_v5() -> None:
    """The corpus a deployment gets when it configures nothing."""
    assert AppConfig().prompt_set == "anthropic_v5"


@pytest.mark.usefixtures("_pristine_environment")
def test_default_ticket_review_mode_is_create_only() -> None:
    """The ticket shape a deployment gets when it configures nothing."""
    assert AppConfig().ticket_review_mode is TicketReviewMode.CREATE_ONLY


@pytest.mark.usefixtures("_pristine_environment")
def test_neither_flipped_default_counts_as_an_operator_decision() -> None:
    """Non-vacuity for the pair, and the property the review budget rests on.

    A field sitting at its shipped default expresses no decision — which is
    exactly what lets ``create_only`` ship as a default while an EXPLICIT
    review budget under it is a boot failure. If the flip had been written
    as a supplied value rather than a declared default, that refusal would
    fire on every untouched deployment.
    """
    config = AppConfig()
    assert "prompt_set" not in config.model_fields_set
    assert "ticket_review_mode" not in config.model_fields_set
    assert config.explicit_max_reviews() is None
