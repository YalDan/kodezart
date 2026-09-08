"""One content-editor contract at the fake and actual GitHub REST boundaries."""

import json

import httpx
import pytest
from pydantic import ValidationError

from kodezart.core.protocols import PRContentEditor
from kodezart.domain.errors import (
    ForgeAPIError,
    PRContentConflictError,
    TransientAPIError,
)
from kodezart.types.domain.pr_content import PRContent
from tests.adapters.test_github_api import _make_client
from tests.fakes import FakePRContentEditor

REPO = "https://github.com/example/project"
HEAD = "lane/subject"


def content(**changes):
    return PRContent.model_validate(
        {
            "url": f"{REPO}/pull/7",
            "number": 7,
            "head_branch": HEAD,
            "base_branch": "recorded-base",
            "title": "Original title",
            "body": "Original body.",
            **changes,
        }
    )


def wire(record):
    return {
        "html_url": record.url,
        "number": record.number,
        "title": record.title,
        "body": record.body,
        "head": {"ref": record.head_branch},
        "base": {"ref": record.base_branch},
    }


@pytest.fixture(params=["fake", "github"])
async def boundary(request):
    fake = FakePRContentEditor(records={(REPO, 7): content()})
    calls = []
    if request.param == "fake":
        yield fake, fake.records, fake.calls
        return

    def handler(req):
        if req.method == "GET":
            assert req.url.path == "/repos/example/project/pulls"
            assert req.url.params["state"] == "open"
            assert req.url.params["per_page"] == "2"
            prefix, _, head = req.url.params["head"].partition(":")
            assert prefix == "example"
            return httpx.Response(
                200,
                json=[
                    wire(record)
                    for (repo, _), record in fake.records.items()
                    if repo == REPO and record.head_branch == head
                ],
            )
        assert req.method == "PATCH"
        assert req.url.path == "/repos/example/project/pulls/7"
        payload = json.loads(req.content)
        assert payload and set(payload) <= {"title", "body", "base"}
        calls.append({"method": "edit_pr", "payload": payload})
        update = {
            "base_branch" if key == "base" else key: value
            for key, value in payload.items()
        }
        fake.records[(REPO, 7)] = fake.records[(REPO, 7)].model_copy(update=update)
        return httpx.Response(200, json=wire(fake.records[(REPO, 7)]))

    adapter = _make_client(handler)
    try:
        yield adapter, fake.records, calls
    finally:
        await adapter.close()


async def test_exact_content_read(boundary):
    editor, _, _ = boundary
    assert isinstance(editor, PRContentEditor)
    assert await editor.read_open_pr(repo_url=REPO, head=HEAD, pr_number=7) == content()


async def test_unchanged_replay_issues_no_update(boundary):
    editor, _, calls = boundary
    expected = await editor.read_open_pr(repo_url=REPO, head=HEAD, pr_number=7)
    result = await editor.edit_pr(
        repo_url=REPO,
        expected=expected,
        title=expected.title,
        body=expected.body,
        base=expected.base_branch,
    )
    assert result == expected
    assert not [call for call in calls if call["method"] == "edit_pr"]


@pytest.mark.parametrize("field", ["title", "base"])
async def test_invalid_desired_content_refuses_before_any_update(boundary, field):
    editor, records, calls = boundary
    values = {"title": "Desired title", "body": "Desired body", "base": "target"}
    values[field] = ""
    with pytest.raises(ValidationError):
        await editor.edit_pr(repo_url=REPO, expected=content(), **values)
    assert records[(REPO, 7)] == content()
    assert not [call for call in calls if call["method"] == "edit_pr"]


@pytest.mark.parametrize(
    "field,value",
    [("title", "New title"), ("body", "New body"), ("base_branch", "another-base")],
)
async def test_only_changed_field_is_updated_and_identity_is_retained(
    boundary, field, value
):
    editor, records, calls = boundary
    expected = content()
    desired = expected.model_copy(update={field: value})
    result = await editor.edit_pr(
        repo_url=REPO,
        expected=expected,
        title=desired.title,
        body=desired.body,
        base=desired.base_branch,
    )
    assert result == desired == records[(REPO, 7)]
    assert len(records) == 1
    writes = [call for call in calls if call["method"] == "edit_pr"]
    assert len(writes) == 1
    if "payload" in writes[0]:
        assert writes[0]["payload"] == {
            "base" if field == "base_branch" else field: value
        }
    again = await editor.edit_pr(
        repo_url=REPO,
        expected=result,
        title=desired.title,
        body=desired.body,
        base=desired.base_branch,
    )
    assert again == result
    assert len([call for call in calls if call["method"] == "edit_pr"]) == 1


@pytest.mark.parametrize("change", ["title", "body", "base_branch", "url"])
async def test_stale_content_refuses_before_update(boundary, change):
    editor, records, calls = boundary
    expected = content()
    records[(REPO, 7)] = expected.model_copy(update={change: "concurrent edit"})
    with pytest.raises(PRContentConflictError, match="changed after"):
        await editor.edit_pr(
            repo_url=REPO,
            expected=expected,
            title="Wanted",
            body="Wanted",
            base=expected.base_branch,
        )
    assert not [call for call in calls if call["method"] == "edit_pr"]


@pytest.mark.parametrize(
    "problem", ["absent", "duplicate", "wrong-number", "wrong-head"]
)
async def test_ambiguous_or_missing_open_identity_refuses(boundary, problem):
    editor, records, _ = boundary
    head, number = HEAD, 7
    if problem == "absent":
        records.clear()
    elif problem == "duplicate":
        records[(REPO, 8)] = content(number=8, url=f"{REPO}/pull/8")
    elif problem == "wrong-number":
        number = 8
    else:
        head = "other"
    with pytest.raises(PRContentConflictError):
        await editor.read_open_pr(repo_url=REPO, head=head, pr_number=number)


@pytest.mark.parametrize(
    "field", ["body", "title", "head", "base", "number", "html_url"]
)
async def test_native_full_read_requires_each_content_field(field):
    payload = wire(content())
    del payload[field]
    adapter = _make_client(lambda _: httpx.Response(200, json=[payload]))
    try:
        with pytest.raises(ForgeAPIError):
            await adapter.read_open_pr(repo_url=REPO, head=HEAD, pr_number=7)
    finally:
        await adapter.close()


async def test_native_null_body_is_explicitly_empty():
    payload = {**wire(content()), "body": None}
    adapter = _make_client(lambda _: httpx.Response(200, json=[payload]))
    try:
        result = await adapter.read_open_pr(repo_url=REPO, head=HEAD, pr_number=7)
        assert result.body == ""
    finally:
        await adapter.close()


@pytest.mark.parametrize("failure", ["refusal", "server", "transport", "bad-result"])
async def test_native_update_failure_is_typed_and_not_retried(failure):
    methods = []

    def handler(req):
        methods.append(req.method)
        if req.method == "GET":
            return httpx.Response(200, json=[wire(content())])
        if failure == "refusal":
            return httpx.Response(403)
        if failure == "server":
            return httpx.Response(503)
        if failure == "transport":
            raise httpx.ReadError("lost update response")
        return httpx.Response(200, json=wire(content(number=99)))

    adapter = _make_client(handler, max_retries=3)
    expected_error = (
        PRContentConflictError
        if failure == "bad-result"
        else (ForgeAPIError if failure == "refusal" else TransientAPIError)
    )
    try:
        with pytest.raises(expected_error):
            await adapter.edit_pr(
                repo_url=REPO,
                expected=content(),
                title="Wanted",
                body="Wanted",
                base="recorded-base",
            )
    finally:
        await adapter.close()
    assert methods == ["GET", "PATCH"]
