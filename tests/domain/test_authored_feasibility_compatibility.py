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
            "45b59afffe78fa8254d442c598a0d96b2aec34cbcbe8e4068c91de5613f12ecf",
        ),
        (
            CriteriaValidationOutput,
            "d058c4f007684edd901118aee41a1af29715b3f2918d018aa159f894274e34c9",
        ),
    ],
)
def test_authored_wire_schemas_equal_the_captured_dispatch_base(model, digest):
    # Captured before extracting shared evidence at fbc4daa; moved twice, each
    # time because a description these schemas SHIP changed: once when the
    # criterion class was deleted and ForbiddenCriterionClass stopped
    # describing the downgrade, and once when CostClaim stopped telling the
    # refuter that a measured uneconomic cost survives as environment-side
    # evidence — a price no longer reaches the undemonstrable verdict.
    value = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(value.encode()).hexdigest() == digest
