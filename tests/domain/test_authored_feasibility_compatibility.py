"""Retained authored wire compatibility through execution-stack retirement."""

import hashlib
import json

import pytest

from kodezart.types.domain.criteria import (
    CriteriaValidationOutput,
    CriterionFinding,
)


@pytest.mark.parametrize(
    "model,digest",
    [
        (
            CriterionFinding,
            "5afe903e14f6c46793fb5b5a5e2ce796f1c69ba4df859ed4d580316dd75069d4",
        ),
        (
            CriteriaValidationOutput,
            "0da41e65788f8ed11a5a101bc32938299a0ce7c5322394300381ea54883fd8b6",
        ),
    ],
)
def test_authored_wire_schemas_equal_the_captured_dispatch_base(model, digest):
    # Captured before extracting shared evidence at fbc4daa; never re-baselined.
    value = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(value.encode()).hexdigest() == digest
