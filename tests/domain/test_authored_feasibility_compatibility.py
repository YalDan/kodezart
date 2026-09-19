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
            "2ef057be09f5787863cb9274da5a811fa114401b526c007c1ba2d7073ad3fff0",
        ),
        (
            CriteriaValidationOutput,
            "1e49292eb7a9c7b364fb7f30db016c2c2ab9505cf51bfaa6b9b1e0f532f327bc",
        ),
    ],
)
def test_authored_wire_schemas_equal_the_captured_dispatch_base(model, digest):
    # Captured before extracting shared evidence at fbc4daa; moved once, when
    # the criterion class was deleted and ForbiddenCriterionClass stopped
    # describing the downgrade in the description these schemas ship.
    value = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(value.encode()).hexdigest() == digest
