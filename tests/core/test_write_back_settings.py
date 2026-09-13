"""A configured write-back budget is independent of admission convergence."""

import json
import re

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.types.domain.operation import OperationMemberAbsentError
from tests.chains.test_organize_owner import factory, result, run_owner


def test_undeclared_write_back_budget_is_absent():
    assert AppConfig(_env_file=None).write_back is None


@pytest.mark.parametrize("value", [0, 11, 1.5])
def test_invalid_write_back_budget_refuses_at_settings_boundary(value):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, write_back={"max_verify_rounds": value})


def test_declared_write_back_budget_has_no_default():
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, write_back={})


def test_configured_organize_requires_the_write_back_budget():
    with pytest.raises(OperationMemberAbsentError, match="write_back"):
        factory(
            settings=AppConfig(
                _env_file=None,
                organize={
                    "max_admission_rounds": 7,
                    "max_convergence_rounds": 7,
                },
            )
        )


@pytest.mark.parametrize("rounds", [1, 3])
async def test_environment_budget_drives_actual_fresh_write_back_rounds(
    monkeypatch, rounds
):
    monkeypatch.setenv("KODEZART_WRITE_BACK__MAX_VERIFY_ROUNDS", str(rounds))
    config = AppConfig(
        _env_file=None,
        organize={
            "max_admission_rounds": 7,
            "max_convergence_rounds": 7,
        },
    )
    owner, _board, executor = factory(settings=config)
    original = executor.stream
    seen = []

    async def refute_body(**kwargs):
        async for event in original(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "WriteBackFinding":
                artifact = json.loads(
                    re.search(
                        r"<written_artifact>\s*(.*?)\s*</written_artifact>",
                        kwargs["prompt"],
                        re.S,
                    )[1]
                )
                if artifact["surface"]["kind"] == "issue_description":
                    seen.append(kwargs["session_id"])
                    event = result(
                        structured_output={
                            "verdict": "refuted",
                            "evidence": (
                                "The landed body does not demonstrate the cited source."
                            ),
                            "cited_refs": ["src/missing.py:1"],
                        }
                    )
            yield event

    monkeypatch.setattr(executor, "stream", refute_body)
    report = await run_owner(owner)
    assert report.halt is not None
    assert report.halt.cause == "admission_exhausted"
    assert report.halt.bound.loop == "write_back"
    assert len(report.halt.write_back_results[0].rounds) == rounds
    assert seen == [None] * rounds
    assert report.completed_phases == ()


@pytest.mark.parametrize("value", [1, 10])
def test_valid_budget_boundaries_and_json_serialization(value):
    config = AppConfig(_env_file=None, write_back={"max_verify_rounds": value})
    assert config.write_back.model_dump() == {"max_verify_rounds": value}


def test_retired_flat_environment_name_refuses(monkeypatch):
    monkeypatch.setenv("KODEZART_WRITE_BACK_MAX_VERIFY_ROUNDS", "3")
    with pytest.raises(ValidationError, match="write_back_max_verify_rounds"):
        AppConfig(_env_file=None)
