"""A configuration error names the field it rejected, never the value.

Measured, not hypothetical: a validation error printed a live key. Pydantic
renders ``input_value=`` into every error by default, so the one place
guaranteed to receive a credential — the loader that rejects a misspelled
credential variable — was also the one place guaranteed to print it, into
whatever collects boot output.

``hide_input_in_errors`` removes the value and keeps the diagnosis: the
offending field is still named, which is the whole reason an operator reads
the error. Both models that ingest operator-supplied text carry the setting,
including the operation config, whose loader chains the raw ``ValidationError``
and would otherwise echo the entire submitted document through the traceback.

One residual, recorded because it bounds the claim: the setting governs the
RENDERED forms (``str`` and ``repr``), not the structured ``errors()`` list,
which still carries a raw ``input`` entry. That is safe here only because
every ``ValidationError`` consumer in ``src`` either renders the exception or
reads ``loc``/``msg`` alone — and the last test is what keeps the only
structured consumer honest about that.
"""

import pytest
from pydantic import ValidationError

from kodezart.adapters.toml_operation_config import _flatten
from kodezart.core.config import AppConfig
from kodezart.types.domain.operation import OperationConfig

#: Shaped like the thing that actually leaked, and distinctive enough that
#: a substring search for it cannot match anything incidental.
SECRET_VALUE = "sk-ant-api03-lIvEkEy-must-never-be-rendered"

#: A misspelling of a real knob is how a secret reaches an unknown field:
#: the operator sets it, ``extra="forbid"`` rejects it, and the rejection is
#: what used to print it.
UNKNOWN_FIELD = "github_tokenn"


def test_an_unknown_config_key_is_named_without_its_value() -> None:
    """The rejection says which key is wrong and refuses to quote it."""
    with pytest.raises(ValidationError) as caught:
        AppConfig(**{UNKNOWN_FIELD: SECRET_VALUE})  # type: ignore[arg-type]

    rendered = str(caught.value)
    assert SECRET_VALUE not in rendered
    # Still diagnosable: the operator learns exactly what to fix.
    assert UNKNOWN_FIELD in rendered
    assert "Extra inputs are not permitted" in rendered


def test_neither_rendering_of_a_config_error_carries_the_value() -> None:
    """``str`` is not the only way an exception reaches a log or a console."""
    with pytest.raises(ValidationError) as caught:
        AppConfig(**{UNKNOWN_FIELD: SECRET_VALUE})  # type: ignore[arg-type]

    assert SECRET_VALUE not in str(caught.value)
    assert SECRET_VALUE not in repr(caught.value)


def test_a_stray_secret_in_the_operation_config_is_not_echoed_back() -> None:
    """The operation config holds no secret — so a stray one must not print.

    ``extra="forbid"`` is what turns a stray token key into a load failure,
    and that failure rendered the whole submitted document on every
    missing-field error, not merely on the offending one.
    """
    with pytest.raises(ValidationError) as caught:
        OperationConfig.model_validate({"stray_token": SECRET_VALUE})

    assert SECRET_VALUE not in str(caught.value)
    assert SECRET_VALUE not in repr(caught.value)
    # The structural complaint survives: required fields are still named.
    assert "operation_name" in str(caught.value)


def test_the_operation_loader_reports_failures_without_the_submitted_values() -> None:
    """The one structured consumer must keep reading ``loc``/``msg`` only.

    ``_flatten`` walks ``errors()``, which ``hide_input_in_errors`` does NOT
    redact.  It is safe because it never touches the ``input`` entry — and a
    future edit that starts including it would leak the document straight
    into a typed error's ``failures`` list, so the guard belongs here.
    """
    with pytest.raises(ValidationError) as caught:
        OperationConfig.model_validate({"stray_token": SECRET_VALUE})

    failures = _flatten(caught.value)

    assert failures  # the failure is reported at all
    assert all(SECRET_VALUE not in line for line in failures)
    assert any("stray_token" in line for line in failures)
