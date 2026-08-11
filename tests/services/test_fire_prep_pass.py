"""The fire-prep pass path: prompt through the registry, gates it owns.

The three surfaces the criteria name, each against the shipped artifacts:
the prompt composes through the prompt port from the example operation
config (and refuses on an unbound placeholder), the hygiene gate reaches a
body through KOD-47's scanner entry point, and the check-failure report
consumes the root-versus-cascade classifier over the config's declared
chain — the caller whose absence kept AC-41 unclaimed.
"""

from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.passes import build_fire_prep_pass
from kodezart.core.config import AppConfig
from kodezart.core.errors import PromptRenderError
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.services.fire_prep_pass import FirePrepPass
from kodezart.services.hygiene_scan import HygieneScan
from kodezart.types.domain.gating import (
    HygieneCategory,
    OutboundDestination,
    ScanResult,
)
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
)
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_hygiene_scan import RecordingScanner

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"


def example_config() -> OperationConfig:
    return load_operation_config(EXAMPLE)


def shipped_pass() -> FirePrepPass:
    """The pass exactly as the composition wires it, over the example config."""
    config = example_config()
    return build_fire_prep_pass(
        config=AppConfig(),
        operation=config,
        prompts=load_registry(bindings=dict(bindings_for(config))),
    )


def test_the_prompt_composes_through_the_registry() -> None:
    """The pass prompt is the rendered template, with nothing left unbound."""
    rendered = shipped_pass().compose_prompt()

    assert rendered
    assert "{{" not in rendered
    assert example_config().operation_name in rendered


def test_an_unbound_placeholder_is_a_typed_refusal_not_a_prompt() -> None:
    """AC-17 at the pass path: no config value, no pass, named placeholder."""
    config = example_config()
    unbound = FirePrepPass(
        prompts=load_registry(),
        scan=HygieneScan(scanner=RecordingScanner(result=ScanResult())),
        operation=config,
    )

    with pytest.raises(PromptRenderError) as excinfo:
        unbound.compose_prompt()
    assert "operation_name" in excinfo.value.missing


async def test_the_gate_reaches_the_body_through_the_scanner_entry_point() -> None:
    """AC-18: the pass invokes ``ContentScanner.scan`` — no second code path."""
    scanner = RecordingScanner(result=ScanResult())
    config = example_config()
    pass_path = FirePrepPass(
        prompts=load_registry(bindings=dict(bindings_for(config))),
        scan=HygieneScan(scanner=scanner),
        operation=config,
    )

    report = await pass_path.gate_body(body="A candidate fire body.")

    assert scanner.calls == [
        ("A candidate fire body.", OutboundDestination.TRACKER_COMMENT),
    ]
    assert report.promotable


async def test_the_shipped_quality_set_refuses_an_unreadable_body() -> None:
    """The composition-built gate runs the quality set, end to end."""
    report = await shipped_pass().gate_body(
        body="Move the label to queue:approved once the work is understood.",
    )

    assert not report.promotable
    assert HygieneCategory.ORCHESTRATION_VOCABULARY in report.categories


def test_check_failures_report_one_root_and_its_cascades() -> None:
    """AC-41: the classification is consumed where check results are reported."""
    config = example_config()
    repo = config.repos[0]
    failed = [step.name for step in repo.checks]

    classified = shipped_pass().report_check_failures(
        repo_url=repo.url,
        failed=failed,
    )

    assert classified.roots == (repo.checks[0].name,)
    assert set(classified.cascades) == {step.name for step in repo.checks[1:]}


def test_an_undeclared_repository_is_a_typed_refusal() -> None:
    """No declared chain, no classification — named, never a silent flat list."""
    with pytest.raises(OperationMemberAbsentError) as excinfo:
        shipped_pass().report_check_failures(
            repo_url="https://example.invalid/not-declared",
            failed=["lint"],
        )
    assert "repos entry" in excinfo.value.missing


def test_the_pass_owns_no_write_path_and_no_scheduler() -> None:
    """The withdrawal's boundary, held structurally.

    The session composes and writes; this service renders and gates.  A
    tracker port, an outbound gate, an executor or the scheduler appearing
    in its imports is the withdrawn apparatus growing back.
    """
    source = (
        REPO_ROOT / "src" / "kodezart" / "services" / "fire_prep_pass.py"
    ).read_text(encoding="utf-8")

    for banned in (
        "TrackerPort",
        "OutboundContentGate",
        "AgentExecutor",
        "PassScheduler",
    ):
        assert banned not in source, banned
