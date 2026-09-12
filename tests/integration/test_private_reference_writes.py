"""Deployment reference facts reach the existing authored PR publication path."""

import pytest
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from kodezart.adapters.linear_references import linear_reference
from kodezart.adapters.reference_content_scanner import private_reference_category
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.gating import build_outbound_gate
from kodezart.composition.prompts import boot_prompts
from kodezart.core.config import AppConfig
from kodezart.core.errors import ContentScannerBootError
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.types.domain.agent import ErrorEvent, ResultEvent
from kodezart.types.domain.gating import (
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.privacy import PrivateSurface, WebReference
from tests.adapters.test_judgment_scanner import ScriptedAuditExecutor, audit_result
from tests.chains.test_outbound_gating import make_engine, run_engine
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakePRCreator,
    FakeVisibilityResolver,
    make_prompt_provider,
)
from tests.outbound import LiteralJudgment, make_admission

PUBLIC_URL = "https://linear.app/public-example/issue/PUB-7/details"
PRIVATE_URL = "https://linear.app/private-example/issue/EX-4/details"
DESCRIPTION = "Private customer identities and unreleased decisions remain private."


def operation_with_facts(**facts):
    return OperationConfig(
        operation_name="Example",
        workspace="Example",
        private_surface={"description": DESCRIPTION, **facts},
    )


class DescriptionExecutor(FakeAgentExecutor):
    def __init__(self, body):
        super().__init__(events=[])
        self.body = body

    async def stream(self, **kwargs):
        async for event in super().stream(**kwargs):
            if (
                isinstance(event, ResultEvent)
                and event.structured_output is not None
                and "description" in event.structured_output
            ):
                event = event.model_copy(
                    update={
                        "structured_output": {
                            **event.structured_output,
                            "description": self.body,
                        }
                    }
                )
            yield event


async def composed_gate(operation=None, config=None):
    return await build_outbound_gate(
        config=AppConfig() if config is None else config,
        operation=operation,
        executor=ScriptedAuditExecutor([audit_result([])]),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        log=get_logger(__name__),
    )


async def test_public_workspace_url_is_not_private_merely_because_of_its_host():
    gate = await composed_gate()
    content = "See [PUB-7](https://linear.app/public-example/issue/PUB-7/details)."
    decision = await gate.gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == content


@pytest.mark.parametrize("visibility", list(RepoVisibility))
@pytest.mark.parametrize(
    "workspace", ["private-example", "PRIVATE-EXAMPLE", "%70rivate-example"]
)
async def test_actual_authored_pr_writer_preserves_public_neighbor_and_issue_keys(
    visibility, workspace
):
    private = PRIVATE_URL.replace("private-example", workspace)
    body = f"See [EX-4]({private}) and [PUB-7]({PUBLIC_URL})."
    gate = await composed_gate(
        operation_with_facts(workspaces={"LINEAR.APP.": ["private-example"]})
    )
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=gate,
        visibility_resolver=FakeVisibilityResolver(visibility),
        executor=DescriptionExecutor(body),
    )
    events = await run_engine(engine)
    assert not [event for event in events if isinstance(event, ErrorEvent)]
    (created,) = creator.calls
    assert PUBLIC_URL in created["body"]
    assert "EX-4" in created["body"] and "PUB-7" in created["body"]
    if visibility is RepoVisibility.PRIVATE:
        assert private in created["body"]
    else:
        assert private not in created["body"]
        assert "[REDACTED:tracker_urls]" in created["body"]


@pytest.mark.parametrize("visibility", [RepoVisibility.PUBLIC, RepoVisibility.UNKNOWN])
@pytest.mark.parametrize(
    "leak", ["https://runner.private.invalid/internal", "ghp_" + "X" * 40]
)
async def test_actual_writer_refuses_private_host_and_credentials_before_create(
    visibility, leak
):
    gate = await composed_gate(operation_with_facts(hosts=["RUNNER.PRIVATE.INVALID."]))
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=gate,
        visibility_resolver=FakeVisibilityResolver(visibility),
        executor=DescriptionExecutor(leak),
    )
    with pytest.raises(OutboundContentBlockedError) as caught:
        await run_engine(engine)
    assert creator.calls == []
    assert caught.value.writer == OutboundDestination.PR_BODY.value


@pytest.mark.parametrize("description", [DESCRIPTION, "", "  preserved\nbytes  "])
def test_legacy_prose_migrates_without_losing_semantic_binding(description):
    operation = OperationConfig(
        operation_name="Example", workspace="Example", private_surface=description
    )
    assert operation.private_surface.description == description
    assert operation_bindings(operation)["private_surface"] == description
    restored = OperationConfig.model_validate_json(operation.model_dump_json())
    assert restored == operation


@pytest.mark.parametrize(
    "host",
    [
        "https://example.invalid",
        "example.invalid/path",
        "user@example.invalid",
        "example.invalid:443",
        " example.invalid",
        "example.invalid?x",
    ],
)
def test_host_facts_cannot_hide_url_components(host):
    with pytest.raises(ValidationError):
        PrivateSurface(hosts=[host])


@pytest.mark.parametrize("slug", ["", "a/b", "a\\b", "a?b", " a", "a%2fb"])
def test_workspace_facts_are_decoded_single_segments(slug):
    with pytest.raises(ValidationError):
        PrivateSurface(workspaces={"linear.app": [slug]})


async def test_unknown_workspace_parser_is_a_boot_refusal():
    with pytest.raises(ContentScannerBootError):
        await composed_gate(
            operation_with_facts(workspaces={"unknown.example": ["private"]})
        )


async def test_tracker_coordination_keeps_destination_judgment_distinct():
    gate = await composed_gate(
        operation_with_facts(workspaces={"linear.app": ["private-example"]})
    )
    decision = await gate.gate(
        content=PRIVATE_URL,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.TRACKER_COMMENT,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.CLEAN


async def test_gate_retains_its_private_facts_despite_mutable_loaded_mapping():
    operation = operation_with_facts(workspaces={"linear.app": ["private-example"]})
    gate = await composed_gate(operation)
    operation.private_surface.workspaces.clear()
    decision = await gate.gate(
        content=PRIVATE_URL,
        visibility=RepoVisibility.UNKNOWN,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.REDACTED


async def test_real_toml_loader_supplies_the_composed_reference_policy(tmp_path):
    file = tmp_path / "operation.toml"
    file.write_text(
        'operation_name = "Example"\nworkspace = "Example"\n'
        '[private_surface]\ndescription = "' + DESCRIPTION + '"\n'
        '[private_surface.workspaces]\n"LINEAR.APP." = ["Private-Example"]\n'
    )
    operation = load_operation_config(file)
    assert operation_bindings(operation)["private_surface"] == DESCRIPTION
    gate = await composed_gate(operation)
    decision = await gate.gate(
        content=PRIVATE_URL,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.DERIVED,
    )
    assert decision.verdict is GateVerdict.REDACTED


@pytest.mark.parametrize(
    "url",
    [
        "https://LINEAR.APP./private-example/issue/EX-4",
        "https://%6cinear.app/private-example/issue/EX-4",
        "https://linear.app/%70rivate-example/issue/EX-4",
    ],
)
def test_native_parser_returns_the_same_typed_address_used_by_text_classification(url):
    reference = linear_reference(TypeAdapter(AnyHttpUrl).validate_python(url))
    assert reference == WebReference(host="linear.app", workspace="private-example")
    assert (
        private_reference_category(
            reference,
            private_surface=PrivateSurface(
                workspaces={"linear.app": ["private-example"]}
            ),
            destination=OutboundDestination.PR_BODY,
        ).value
        == "tracker_urls"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://linear.app/private-example%2fissue/EX-4",
        "https://linear.app/private-example%5cissue/EX-4",
        "https://linear.app/%ff/issue/EX-4",
        "https://[invalid]/private-example",
    ],
)
async def test_unreadable_or_ambiguous_url_is_not_clean(url):
    gate = await composed_gate(
        operation_with_facts(workspaces={"linear.app": ["private-example"]})
    )
    decision = await gate.gate(
        content=url,
        visibility=RepoVisibility.UNKNOWN,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.failure is not None


def test_normalized_host_alias_cannot_silently_drop_another_workspace():
    with pytest.raises(ValidationError, match="Repeated normalized"):
        PrivateSurface(
            workspaces={"LINEAR.APP": ["secret-one"], "linear.app.": ["secret-two"]}
        )


def test_international_host_facts_use_the_same_http_normalization():
    assert PrivateSurface(hosts=["BÜCHER.example"]).hosts == ("xn--bcher-kva.example",)


@pytest.mark.parametrize(
    "content",
    [
        "https://linear.app.evil.invalid/private-example/issue/EX-4",
        "https://linear.app@evil.invalid/private-example/issue/EX-4",
        "https://linear.app/private-example-other/issue/EX-4",
    ],
)
async def test_host_and_workspace_matches_are_exact(content):
    gate = await composed_gate(
        operation_with_facts(workspaces={"linear.app": ["private-example"]})
    )
    decision = await gate.gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == content


@pytest.mark.parametrize("content_class", list(ContentClass))
async def test_composed_judgment_retains_description_and_authored_provenance(
    tmp_path, content_class
):
    operation = operation_with_facts(workspaces={"linear.app": ["private-example"]})
    config = AppConfig(
        agentic_content_scanner_enabled=True, content_audit_working_dir=str(tmp_path)
    )
    executor = ScriptedAuditExecutor(
        [
            audit_result(
                [
                    {
                        "start": None,
                        "end": None,
                        "rationale": "An unpublished decision is disclosed",
                    }
                ]
            )
        ]
    )
    log = get_logger(__name__)
    prompts = await boot_prompts(config=config, operation=operation, log=log)
    gate = await build_outbound_gate(
        config=config,
        operation=operation,
        executor=executor,
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
        log=log,
    )
    content = "The unnamed customer's unreleased pricing decision changes next week."
    decision = await gate.gate(
        content=content,
        visibility=RepoVisibility.UNKNOWN,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=content_class,
    )
    if content_class is ContentClass.AUTHORED:
        assert decision.verdict is GateVerdict.BLOCKED
        assert len(executor.calls) == 1
        assert DESCRIPTION in executor.calls[0]["prompt"]
        assert executor.calls[0]["allowed_tools"] == []
        assert executor.calls[0]["session_id"] is None
    else:
        assert decision.verdict is GateVerdict.CLEAN
        assert executor.calls == []


async def test_credentials_still_block_without_starting_the_configured_judge(tmp_path):
    operation = operation_with_facts()
    config = AppConfig(
        agentic_content_scanner_enabled=True, content_audit_working_dir=str(tmp_path)
    )
    executor = ScriptedAuditExecutor([audit_result([])])
    log = get_logger(__name__)
    prompts = await boot_prompts(config=config, operation=operation, log=log)
    gate = await build_outbound_gate(
        config=config,
        operation=operation,
        executor=executor,
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
        log=log,
    )
    decision = await gate.gate(
        content="ghp_" + "X" * 40,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert executor.calls == []


@pytest.mark.parametrize(
    "earlier_pattern", ["https://", "See https://", "private-example"]
)
async def test_actual_pr_never_publishes_the_tail_of_an_overlapping_private_url(
    earlier_pattern,
):
    gate = make_admission(
        LiteralJudgment({RedactionCategory.ORG_PRIVATE: [earlier_pattern]}),
        private_surface=PrivateSurface(workspaces={"linear.app": ["private-example"]}),
    )
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=gate,
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.PUBLIC),
        executor=DescriptionExecutor(f"See {PRIVATE_URL} for EX-4."),
    )
    await run_engine(engine)
    (created,) = creator.calls
    assert "private-example" not in created["body"]
    assert "linear.app" not in created["body"]
    assert "EX-4" in created["body"]


@pytest.mark.parametrize(
    "private",
    [
        "https://linear.app/private-example/issue/EX-4",
        "https://linear.app/private&#45;example/issue/EX-4",
        "https://linear.app/private&#x2d;example/issue/EX-4",
        "https://linear&period;app/private-example/issue/EX-4",
        "https&colon;&sol;&sol;linear.app/private-example/issue/EX-4",
        "//linear.app/private-example/issue/EX-4",
        "&sol;&sol;linear.app/private&#45;example/issue/EX-4",
        r"https\://linear.app/private\-example/issue/EX-4",
    ],
)
async def test_actual_writer_classifies_rendered_markdown_with_original_spans(private):
    public = "https://linear.app/public&#45;example/issue/PUB-7"
    body = f"Prefix &NotEqualTilde; [EX-4]({private}) and [PUB-7]({public})."
    gate = await composed_gate(
        operation_with_facts(workspaces={"linear.app": ["private-example"]})
    )
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=gate,
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.PUBLIC),
        executor=DescriptionExecutor(body),
    )
    await run_engine(engine)
    (created,) = creator.calls
    assert body.replace(private, "[REDACTED:tracker_urls]") in created["body"]
    assert public in created["body"]


@pytest.mark.parametrize(
    "private",
    [
        "//runner.private.invalid/work",
        "https&colon;&sol;&sol;runner.private.invalid/work",
    ],
)
async def test_actual_writer_blocks_rendered_private_authority_before_create(private):
    gate = await composed_gate(operation_with_facts(hosts=["runner.private.invalid"]))
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=gate,
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.UNKNOWN),
        executor=DescriptionExecutor(f"See [internal]({private})."),
    )
    with pytest.raises(OutboundContentBlockedError):
        await run_engine(engine)
    assert creator.calls == []
