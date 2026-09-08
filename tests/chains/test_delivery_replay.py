"""A replay edits the same PR, gates its content and creates no copy."""

import json

import httpx
import pytest

from kodezart.domain.errors import (
    DeliveryContextError,
    ForgeAPIError,
    OutboundContentBlockedError,
    PRContentConflictError,
    PRTrackerIdentityError,
)
from kodezart.types.domain.gating import GateDecision, GateVerdict, OutboundDestination
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.pr_content import PRContent
from tests.adapters.test_github_api import _completed_run, _make_client
from tests.chains.test_delivery_runtime import (
    BASE,
    HEAD,
    REPOSITORY,
    context,
    deliver,
    setup,
)
from tests.fakes import (
    FakeCIMonitor,
    FakeForgeQuery,
    FakePRContentEditor,
    PassThroughGate,
)

EXISTING_URL = "https://github.com/example/project/pull/72"


def existing_fixture(*, base=BASE, gate=None):
    record = PRContent(
        url=EXISTING_URL,
        number=72,
        head_branch=HEAD,
        base_branch=base,
        title="Old title",
        body="Old body",
    )
    return setup(
        query=FakeForgeQuery(open_prs={(REPOSITORY, HEAD): (EXISTING_URL, 72)}),
        editor=FakePRContentEditor(records={(REPOSITORY, 72): record}),
        gate=gate,
    )


async def test_existing_pr_is_edited_and_identical_replay_has_no_second_write():
    fixture = existing_fixture()
    first = await deliver(fixture.coordinator)
    second = await deliver(fixture.coordinator)
    assert first == second
    assert first.pr.url == EXISTING_URL and first.pr.number == 72
    assert first.pr.state == "open"
    assert fixture.forge.calls == []
    writes = [call for call in fixture.editor.calls if call["method"] == "edit_pr"]
    assert len(writes) == 1
    saved = fixture.editor.records[(REPOSITORY, 72)]
    assert saved.title == "Authored title"
    assert saved.body.endswith("Tracker issue: subject/42")
    assert "Recorded caveat" in saved.body
    assert fixture.gate.calls[1][0] == saved.body
    assert fixture.monitor.calls == [{"repo_url": REPOSITORY, "ref": HEAD}] * 2


class DestinationGate(PassThroughGate):
    def __init__(self, destination, *, rewrite=False):
        super().__init__()
        self.destination = destination
        self.rewrite = rewrite

    async def gate(self, **kwargs):
        result = await super().gate(**kwargs)
        if kwargs["destination"] is self.destination:
            return GateDecision(
                verdict=GateVerdict.CLEAN if self.rewrite else GateVerdict.BLOCKED,
                content="rewritten base" if self.rewrite else "",
            )
        return result


@pytest.mark.parametrize(
    "destination",
    [
        OutboundDestination.PR_TITLE,
        OutboundDestination.PR_BODY,
        OutboundDestination.BRANCH_NAME,
    ],
)
async def test_each_changed_outbound_field_can_refuse_the_update(destination):
    fixture = existing_fixture(base="old-base", gate=DestinationGate(destination))
    with pytest.raises(OutboundContentBlockedError):
        await deliver(fixture.coordinator)
    assert not [call for call in fixture.editor.calls if call["method"] == "edit_pr"]
    assert fixture.forge.calls == fixture.monitor.calls == []


async def test_base_retargeting_uses_only_the_gated_dispatch_base():
    fixture = existing_fixture(base="old-base")
    result = await deliver(fixture.coordinator)
    assert (
        fixture.editor.records[(REPOSITORY, 72)].base_branch
        == result.base_branch
        == BASE
    )
    assert fixture.gate.destinations[-1] is OutboundDestination.BRANCH_NAME
    assert fixture.gate.calls[-1][0] == BASE


async def test_gate_cannot_replace_the_dispatch_base_with_another_reference():
    fixture = existing_fixture(
        base="old-base",
        gate=DestinationGate(OutboundDestination.BRANCH_NAME, rewrite=True),
    )
    with pytest.raises(
        DeliveryContextError, match="changed the dispatch-resolved base"
    ):
        await deliver(fixture.coordinator)
    assert not [call for call in fixture.editor.calls if call["method"] == "edit_pr"]


async def test_intervening_edit_during_authoring_is_preserved_and_refused():
    fixture = existing_fixture()

    class ConcurrentEditGate(PassThroughGate):
        async def gate(self, **kwargs):
            current = fixture.editor.records[(REPOSITORY, 72)]
            fixture.editor.records[(REPOSITORY, 72)] = current.model_copy(
                update={"title": "An intervening author's title"}
            )
            return await super().gate(**kwargs)

    active = setup(
        query=fixture.query, editor=fixture.editor, gate=ConcurrentEditGate()
    )
    with pytest.raises(PRContentConflictError, match="changed"):
        await deliver(active.coordinator)
    assert (
        active.editor.records[(REPOSITORY, 72)].title == "An intervening author's title"
    )
    assert not [call for call in active.editor.calls if call["method"] == "edit_pr"]
    assert active.forge.calls == active.monitor.calls == []


@pytest.mark.parametrize("change", ["closed", "base", "url", "footer", "duplicate"])
async def test_check_watch_cannot_return_a_stale_open_pr_claim(change):
    fixture = existing_fixture()

    class ChangingMonitor(FakeCIMonitor):
        async def wait_for_checks(self, *, repo_url, ref):
            record = fixture.editor.records[(REPOSITORY, 72)]
            if change == "closed":
                del fixture.editor.records[(REPOSITORY, 72)]
            elif change == "duplicate":
                fixture.editor.records[(REPOSITORY, 73)] = record.model_copy(
                    update={"number": 73, "url": f"{EXISTING_URL}3"}
                )
            else:
                update = {
                    "base": {"base_branch": "other-base"},
                    "url": {"url": f"{EXISTING_URL}3"},
                    "footer": {"body": "A changed body without its identity"},
                }[change]
                fixture.editor.records[(REPOSITORY, 72)] = record.model_copy(
                    update=update
                )
            return await super().wait_for_checks(repo_url=repo_url, ref=ref)

    active = setup(
        query=fixture.query, editor=fixture.editor, monitor=ChangingMonitor()
    )
    expected = PRTrackerIdentityError if change == "footer" else PRContentConflictError
    with pytest.raises(expected):
        await deliver(active.coordinator)
    assert active.forge.calls == []
    assert len(active.monitor.calls) == 1


@pytest.mark.parametrize("mismatch", ["missing", "kind", "issue"])
async def test_invalid_fire_identity_refuses_even_the_forge_read(mismatch):
    original = context()
    identity = original.execution.run_identity
    if mismatch == "missing":
        identity = None
    elif mismatch == "kind":
        identity = identity.model_copy(update={"kind": RunKind.FIRE_PREP})
    else:
        identity = identity.model_copy(update={"name": "other/99"})
    execution = original.execution.model_copy(update={"run_identity": identity})
    fixture = setup()
    with pytest.raises(DeliveryContextError):
        await deliver(
            fixture.coordinator,
            facts=original.model_copy(update={"execution": execution}),
        )
    assert fixture.query.calls == fixture.editor.calls == []
    assert fixture.runner.calls == fixture.forge.calls == []


@pytest.mark.parametrize("lookup", ["refusal", "malformed", "duplicate"])
async def test_native_unreadable_or_ambiguous_lookup_never_becomes_creation(lookup):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/repos/example/project/pulls"
        assert request.url.params["state"] == "open"
        assert request.url.params["head"] == f"example:{HEAD}"
        if lookup == "refusal":
            return httpx.Response(403)
        if lookup == "malformed":
            return httpx.Response(200, json=[{"number": 72}])
        return httpx.Response(200, json=[{"html_url": EXISTING_URL, "number": 72}] * 2)

    client = _make_client(handler)
    fixture = setup(forge=client, query=client, editor=client, monitor=client)
    expected = PRContentConflictError if lookup == "duplicate" else ForgeAPIError
    try:
        with pytest.raises(expected):
            await deliver(fixture.coordinator)
    finally:
        await client.close()
    assert len(requests) == 1
    assert fixture.runner.calls == fixture.gate.calls == []


@pytest.mark.parametrize("preexisting", [False, True])
async def test_native_replay_retains_one_pr_and_updates_only_changed_fields(
    preexisting,
):
    requests = []
    rows = (
        [
            {
                "html_url": EXISTING_URL,
                "number": 72,
                "head": {"ref": HEAD},
                "base": {"ref": BASE},
                "title": "Old title",
                "body": "Old body",
            }
        ]
        if preexisting
        else []
    )

    def handler(request):
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/pulls"):
            assert request.url.params["state"] == "open"
            return httpx.Response(200, json=rows)
        if request.method == "GET" and request.url.path.endswith("/check-runs"):
            return _completed_run()
        payload = json.loads(request.content)
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            assert rows == []
            rows.append(
                {
                    "html_url": EXISTING_URL,
                    "number": 72,
                    "head": {"ref": payload["head"]},
                    "base": {"ref": payload["base"]},
                    "title": payload["title"],
                    "body": payload["body"],
                }
            )
            return httpx.Response(201, json=rows[0])
        assert request.method == "PATCH"
        assert request.url.path == "/repos/example/project/pulls/72"
        assert set(payload) == {"title", "body"}
        rows[0].update(payload)
        return httpx.Response(200, json=rows[0])

    client = _make_client(handler)
    fixture = setup(forge=client, query=client, editor=client, monitor=client)
    try:
        first = await deliver(fixture.coordinator)
        writes_before = [request for request in requests if request.method != "GET"]
        original_row = dict(rows[0])
        second = await deliver(fixture.coordinator)
    finally:
        await client.close()
    assert first == second
    assert first.pr.url == EXISTING_URL and first.pr.number == 72
    assert rows == [original_row]
    writes = [request for request in requests if request.method != "GET"]
    assert writes == writes_before
    assert [request.method for request in writes] == [
        "PATCH" if preexisting else "POST"
    ]
    assert (
        len([request for request in requests if "check-runs" in request.url.path]) == 2
    )
    assert rows[0]["body"].endswith("Tracker issue: subject/42")
