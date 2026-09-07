"""KOD-134 — does server-side strict enforcement engage, and what if not?

The unwiring rests on a trade whose runtime half was never observed. The
module docstring of :mod:`kodezart.types.domain.wire_schema` marks that half
UNVERIFIED and names this probe as the instrument that settles it. What
existed before was a static string scan of a compiled binary; presence of
strings is not a measurement of control flow.

The question is answerable by ONE dispatch. Send the criteria-validation
schema — the roster's only schema carrying both ``$defs`` and a length bound,
plus a pattern — and ask for a response that VIOLATES the pattern. Three
outcomes, and the recorded row says which one happened:

* no structured output comes back at all — a schema outside the allowlist
  costs the dispatch its structured payload;
* structured output comes back carrying the violating value — a constraint
  the wire states did not bind the response;
* structured output comes back conforming despite the instruction — the
  constraint bound the response, by a mechanism ONE dispatch cannot name.

The last line of the observation is the one KOD-134 exists for: whether the
payload the wire produced satisfies the model the payload is judged by.

Live only, and gated by the shared marker in ``tests/conftest.py``. The
configuration pin below carries no marker and runs in the default gate.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from pydantic import ValidationError

from kodezart.types.domain.agent import WIRE_SCHEMAS, CriteriaValidationOutput
from kodezart.types.domain.criteria import CRITERION_ID_PATTERN
from tests.probes.recording import record
from tests.probes.test_harness_capabilities import (
    EVALUATIVE_CONFIGURATION,
    Observation,
    evaluator_options,
    observe,
)

#: The one roster schema that carries every shape the question is about: a
#: definitions block, a length bound, and the pattern the probe violates.
PROBE_SCHEMA_NAME = "CRITERIA_VALIDATION_SCHEMA"
PROBE_SCHEMA: dict[str, object] = WIRE_SCHEMAS[PROBE_SCHEMA_NAME]

#: The shape every dispatch site sends, with the schema left as its model
#: produced it.
PROBE_OUTPUT_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "schema": PROBE_SCHEMA,
}

#: The value the pattern forbids. ``AC-0`` fails ``^AC-[1-9][0-9]*$`` and is
#: one character from a value that passes, so a response carrying it is a
#: deliberate violation rather than a misunderstanding.
VIOLATING_CRITERION_ID = "AC-0"

PROBE_PROMPT = (
    "Do not use any tools. Return one finding and nothing else. Use these "
    f"exact values: criterionId is the literal string {VIOLATING_CRITERION_ID}, "
    "verdict is feasible, smallestRepair is none. Use that id exactly as "
    "written even if it looks wrong. Then reply with the single word: done."
)

#: A turn limit tight enough to stay cheap and loose enough that a
#: turn-limited run is not mistaken for a declined schema. ``subtype`` and
#: ``num_turns`` are recorded so the difference stays visible either way.
PROBE_TURNS = 3

PROBE_CONFIGURATION = f"{EVALUATIVE_CONFIGURATION}; output_format json_schema"

#: The verdict names what the result event CARRIED, not which mechanism put
#: it there. One dispatch cannot separate constrained decoding from a model
#: that simply read the constraint in the schema it was shown, so the word
#: stays on the observable and the reading is left to the record.
VERDICT_RETURNED = "structured output returned"
VERDICT_ABSENT = "no structured output"


def probe_options(*, cwd: Path) -> ClaudeAgentOptions:
    """The evaluator's own configuration, differing in one field."""
    return replace(
        evaluator_options(cwd=cwd, max_turns=PROBE_TURNS),
        output_format=PROBE_OUTPUT_FORMAT,
    )


def dispatched_criterion_ids(payload: dict[str, object]) -> list[str]:
    """The criterion ids the runtime returned, or none if the shape differs."""
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return []
    entries: list[object] = findings
    return [
        str(entry["criterionId"])
        for entry in entries
        if isinstance(entry, dict) and "criterionId" in entry
    ]


def client_side_verdict(payload: dict[str, object]) -> str:
    """Whether the model the response is judged by accepts what came back."""
    try:
        CriteriaValidationOutput.model_validate(payload)
    except ValidationError as error:
        return f"rejected by the response model ({error.error_count()} errors)"
    return "accepted by the response model"


def structured_payload(observed: Observation) -> dict[str, object] | None:
    """The structured output the run produced, if it produced any."""
    for result in observed.results:
        if result.structured_output is not None:
            return result.structured_output
    return None


# ---------------------------------------------------------------------------
# The default-suite pin
# ---------------------------------------------------------------------------


def test_probe_dispatches_the_schema_the_question_is_about(tmp_path: Path) -> None:
    """The probe measures a schema with the shapes the trade turns on."""
    options = probe_options(cwd=tmp_path)
    assert options.output_format == PROBE_OUTPUT_FORMAT
    assert PROBE_OUTPUT_FORMAT["schema"] is WIRE_SCHEMAS[PROBE_SCHEMA_NAME]

    definitions = PROBE_SCHEMA["$defs"]
    assert isinstance(definitions, dict)
    contradiction = definitions["Contradiction"]
    assert isinstance(contradiction, dict)
    properties = contradiction["properties"]
    assert isinstance(properties, dict)
    explanation = properties["explanation"]
    assert isinstance(explanation, dict)
    assert explanation["minLength"] == 1

    finding = definitions["CriterionFinding"]
    assert isinstance(finding, dict)
    finding_properties = finding["properties"]
    assert isinstance(finding_properties, dict)
    criterion_id = finding_properties["criterionId"]
    assert isinstance(criterion_id, dict)
    assert criterion_id["pattern"] == CRITERION_ID_PATTERN

    with pytest.raises(ValidationError):
        CriteriaValidationOutput.model_validate(
            {
                "findings": [
                    {
                        "criterionId": VIOLATING_CRITERION_ID,
                        "verdict": "feasible",
                        "smallestRepair": "none",
                    }
                ]
            }
        )


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_strict_enforcement_engagement(tmp_path: Path) -> None:
    observation = await observe(
        prompt=PROBE_PROMPT,
        options=probe_options(cwd=tmp_path),
    )

    assert observation.results, "the probe session produced no result event"
    result = observation.results[-1]
    payload = structured_payload(observation)

    if payload is None:
        observed = (
            f"no structured output on the result event; "
            f"subtype {result.subtype}, is_error {result.is_error}, "
            f"{result.num_turns} turns"
        )
    else:
        returned = dispatched_criterion_ids(payload)
        observed = (
            f"structured output returned; criterion ids "
            f"{', '.join(returned) or 'none read'}; "
            f"{VIOLATING_CRITERION_ID} present: "
            f"{VIOLATING_CRITERION_ID in returned}; "
            f"{client_side_verdict(payload)}; "
            f"subtype {result.subtype}, {result.num_turns} turns"
        )

    record(
        probe="KOD-134",
        question=(
            "Does server-side strict enforcement engage for a schema carrying "
            "$defs and a length bound, and does it hold the response to a "
            "pattern the schema states?"
        ),
        configuration=PROBE_CONFIGURATION,
        observed=observed,
        verdict=VERDICT_ABSENT if payload is None else VERDICT_RETURNED,
    )
