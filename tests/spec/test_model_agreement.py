"""Spec backend: queried bodies resolve explicit deliverable/criterion pointers.

This module checks reference structure, not the meaning of model-value prose.
The committed synthetic workspace is a regression fixture, never a board spec.
``compare_snapshot`` is the same comparison used for a separately supplied live
baseline; no live workspace or complete invariant dispatch is claimed by CI.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from tests.model_members import CLASSIFICATION, model_workspace

from kodezart.core.protocols import TrackerPort
from kodezart.domain.fire_spec import _without_comments
from kodezart.types.domain.tracker import TrackerIssue

BACKEND = "spec"
FIXTURE = Path(__file__).with_name("fixtures") / "model_members.json"
_ISSUE_LINK = re.compile(r"<issue\b(?P<attrs>[^>]*)>(?P<label>[^<]+)</issue>")
_MARKDOWN_LINK = re.compile(r"\[(?P<label>[^\]\n]+)\]\((?P<url><[^>\n]+>|[^)\n]+)\)")
_QUOTED_KEY = re.compile(r"`(?P<label>[^`\s<>]+)`(?=\s+D[1-9])")
_INLINE_CODE = re.compile(r"(`+)(.*?)\1")
_DELIVERABLE = re.compile(r"\bD(?P<number>[1-9]\d*(?:\.\d+)*[a-z]?)\b")
_HEADING = re.compile(
    r"^(?P<format>#{1,6}\s+|\*\*)?D(?P<number>[1-9]\d*(?:\.\d+)*[a-z]?)"
    r"(?P<after>\*\*|\s|[\u2014\u2013:-]|$)"
)
_ORDINAL = re.compile(r"^(?P<number>[1-9]\d*)\.\s+\S")


@dataclass(frozen=True)
class Pointer:
    source: str
    target: str
    number: str | None
    spelling: str
    comment_target: bool = False


def _visible_lines(body: str) -> list[str]:
    """Discard block quotations, fenced examples and HTML comments."""
    lines = []
    fence = None
    comment = False
    for line in body.splitlines():
        stripped = line.strip()
        delimiter = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
        if fence is not None:
            if (
                delimiter
                and delimiter[1][0] == fence[0]
                and len(delimiter[1]) >= fence[1]
                and not delimiter[2].strip()
            ):
                fence = None
            continue
        line, comment = _without_comments(line, comment=comment)
        stripped = line.strip()
        delimiter = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
        if delimiter:
            fence = delimiter[1][0], len(delimiter[1])
        elif not stripped.startswith(">") and not line.startswith(("    ", "\t")):
            lines.append(line)
    return lines


def numbered_deliverables(body: str) -> Counter[str]:
    """Only explicit D headings or a declared deliverables numbered list."""
    numbers: Counter[str] = Counter()
    numbered_list = False
    for line in _visible_lines(body):
        # An indented example or a condensed Fix bullet is not a target.
        if line.startswith(("    ", "\t")):
            continue
        text = line.strip()
        heading = _HEADING.match(text)
        if heading and (
            heading["format"]
            or re.match(r"\s*[\u2014\u2013:-]", text[heading.end("number") :])
        ):
            numbers[heading["number"]] += 1
        normalized = text.strip("#* :").casefold()
        if normalized.startswith(("deliverables", "deliverable sketch")):
            numbered_list = True
        elif text.startswith("#") or (text.startswith("**") and heading is None):
            numbered_list = False
        ordinal = _ORDINAL.match(text) if numbered_list else None
        if ordinal:
            numbers[ordinal["number"]] += 1
    return numbers


def explicit_pointers(issue: TrackerIssue) -> tuple[Pointer, ...]:
    """Read native issue mentions and Markdown references with D-n surfaces.

    The displayed issue key is the public port address; internal IDs and URL
    path layouts never become an identifier parser. Ordinary issue citations
    have no numbered target; the reader recognizes a native criterion target
    from its actual membership, not from an identifier-shaped title.
    """
    pointers = []
    for line in _visible_lines(issue.body):
        matches = sorted(
            [
                *_ISSUE_LINK.finditer(line),
                *_MARKDOWN_LINK.finditer(line),
                *_QUOTED_KEY.finditer(line),
            ],
            key=lambda match: match.start(),
        )
        code_spans = [match.span() for match in _INLINE_CODE.finditer(line)]
        previous_end = -1
        for match in matches:
            if match.start() < previous_end:
                continue
            if match.re is not _QUOTED_KEY and any(
                start <= match.start() < end for start, end in code_spans
            ):
                continue
            previous_end = match.end()
            label = unescape(match["label"]).strip().strip("`")
            inner = re.search(r"(?<=\s)D(?P<number>[1-9]\d*(?:\.\d+)*[a-z]?)$", label)
            target = label[: inner.start()].strip() if inner else label
            suffix = line[match.end() :]
            parenthetical = re.match(r"\s*\((D[^)\n]*)\)", suffix)
            adjacent = re.match(
                r"\s+(D[1-9]\d*(?:\.\d+)*[a-z]?(?:\s*[/,]\s*D[1-9]\d*(?:\.\d+)*[a-z]?)*)",
                suffix,
            )
            declared = (
                inner[0]
                if inner
                else parenthetical[1]
                if parenthetical
                else adjacent[1]
                if adjacent
                else ""
            )
            if adjacent and re.match(
                r"\s*[-\u2013\u2014]\s*D?\d", suffix[adjacent.end() :]
            ):
                raise AssertionError(
                    f"{issue.issue_key}: numbered reference range is not expanded: "
                    f"{line[match.start() :]}"
                )
            if re.search(r"D\d+\s*[-\u2013\u2014]\s*D?\d", declared):
                raise AssertionError(
                    f"{issue.issue_key}: numbered reference range is not expanded: "
                    f"{declared}"
                )
            numbers = [item["number"] for item in _DELIVERABLE.finditer(declared)]
            # Non-key prose such as [ruling](...) is contextual unless it
            # explicitly declares a numbered target, in which case it refuses.
            fragment = match.groupdict().get("url", "")
            if match.re is _ISSUE_LINK:
                href = re.search(r"""\bhref\s*=\s*["']([^"']*)["']""", match["attrs"])
                fragment = href[1] if href else ""
            anchor = urlsplit(unescape(fragment).strip("<>")).fragment
            comment = "commentId=" in fragment
            if not numbers and (" " in target or anchor or comment):
                continue
            for number in numbers or [None]:
                end = match.end() + (
                    parenthetical.end()
                    if parenthetical and not inner
                    else adjacent.end()
                    if adjacent and not inner
                    else 0
                )
                pointers.append(
                    Pointer(
                        issue.issue_key,
                        target,
                        number,
                        line[match.start() : end],
                        comment or bool(anchor and anchor.casefold() != f"d{number}"),
                    )
                )
    return tuple(pointers)


def _document_projection(document):
    return {
        "body": document.body,
        "parent": document.parent_key,
        "labels": sorted(document.issue_labels),
    }


def _projection(members, documents, criteria):
    return {
        "members": list(members),
        "documents": {
            key: _document_projection(document)
            for key, document in sorted(documents.items())
        },
        "criteria": {key: sorted(value) for key, value in sorted(criteria.items())},
    }


async def read_model(tracker: TrackerPort, *, classification: str):
    members = tuple(await tracker.read_labeled_issues(classification=classification))
    documents = {member.issue_key: member for member in members}
    if len(documents) != len(members):
        raise AssertionError("duplicate model member identity")
    criteria = {}
    for member in members:
        children = tuple(await tracker.read_criteria(issue_key=member.issue_key))
        keys = tuple(child.issue_key for child in children)
        if len(set(keys)) != len(keys):
            raise AssertionError(f"duplicate criterion below {member.issue_key}")
        criteria[member.issue_key] = keys
        for child in children:
            if (
                child.parent_key != member.issue_key
                or "criterion" not in child.issue_labels
            ):
                raise AssertionError(
                    f"criterion membership differs for {child.issue_key}"
                )
            if child.issue_key in documents and documents[child.issue_key] != child:
                raise AssertionError(
                    f"conflicting document reads for {child.issue_key}"
                )
            documents[child.issue_key] = child
    return tuple(sorted(member.issue_key for member in members)), documents, criteria


async def model_agreement(tracker: TrackerPort, *, classification: str):
    members, documents, criteria = await read_model(
        tracker, classification=classification
    )
    captured_model = _projection(members, documents, criteria)
    failures = []
    pointers = tuple(
        pointer
        for document in documents.values()
        for pointer in explicit_pointers(document)
    )
    for pointer in pointers:
        if pointer.comment_target:
            failures.append(
                f"{pointer.source}: {pointer.spelling} -> {pointer.target}: "
                "comment or unnumbered fragment is not a deliverable"
            )
            continue
        if not pointer.target:
            failures.append(
                f"{pointer.source}: {pointer.spelling}: target issue key is absent"
            )
            continue
        if pointer.target not in documents:
            try:
                documents[pointer.target] = await tracker.read_planning_issue(
                    issue_key=pointer.target
                )
            except Exception as exc:
                raise AssertionError(
                    f"{pointer.source}: {pointer.spelling} -> {pointer.target}: "
                    "target could not be read"
                ) from exc
        target = documents[pointer.target]
        if target.issue_key != pointer.target:
            raise AssertionError(
                f"pointer target identity differs for {pointer.target}"
            )
        if pointer.number is not None:
            count = numbered_deliverables(target.body)[pointer.number]
            if count != 1:
                failures.append(
                    f"{pointer.source}: {pointer.spelling} -> "
                    f"{pointer.target} D{pointer.number}: "
                    f"expected one numbered deliverable, found {count}"
                )
        elif "criterion" in target.issue_labels:
            if target.parent_key is None:
                failures.append(
                    f"{pointer.source}: criterion {pointer.target} has no parent"
                )
                continue
            if target.parent_key not in criteria:
                children = tuple(
                    await tracker.read_criteria(issue_key=target.parent_key)
                )
                criteria[target.parent_key] = tuple(
                    child.issue_key for child in children
                )
                documents.update((child.issue_key, child) for child in children)
            if criteria[target.parent_key].count(target.issue_key) != 1:
                failures.append(
                    f"{pointer.source}: criterion {pointer.target} "
                    "is not a unique native child"
                )
    compare_snapshot(
        captured_model,
        _projection(*await read_model(tracker, classification=classification)),
    )
    for key in sorted(set(documents) - set(captured_model["documents"])):
        current = await tracker.read_planning_issue(issue_key=key)
        if current.issue_key != key or _document_projection(
            current
        ) != _document_projection(documents[key]):
            raise AssertionError(f"model snapshot drift: target {key}")
    for parent in sorted(set(criteria) - set(members)):
        current_children = await tracker.read_criteria(issue_key=parent)
        if sorted(child.issue_key for child in current_children) != sorted(
            criteria[parent]
        ):
            raise AssertionError(f"model snapshot drift: criterion family {parent}")
    return tuple(failures), _projection(members, documents, criteria)


def compare_snapshot(expected, current):
    """Compare read facts, never declare a stale CI snapshot current."""
    differences = [
        key
        for key in sorted(set(expected) | set(current))
        if expected.get(key) != current.get(key)
    ]
    if differences:
        raise AssertionError(f"model snapshot drift: {', '.join(differences)}")


@pytest.fixture(params=["native", "fake"])
async def workspace(request):
    fixture = await model_workspace(request.param)
    await fixture.seed(json.loads(FIXTURE.read_text()))
    return fixture


async def test_committed_fixture_runs_actual_query_and_pointer_resolution(workspace):
    failures, snapshot = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert failures == ()
    assert snapshot["members"] == ["member/alpha", "member/beta"]
    assert "criterion/alpha" in snapshot["documents"]
    assert "unmarked/outsider" not in snapshot["documents"]
    workspace.read_only()


async def test_new_member_without_body_edit_is_checked_by_real_pointer_invariant(
    workspace,
):
    before = (await workspace.tracker.read_issue(issue_key="member/alpha")).body
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": '<issue id="member/beta">member/beta</issue> D9.',
                "labels": [CLASSIFICATION],
            }
        ]
    )
    failures, snapshot = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 1
    assert "member/new" in failures[0] and "member/beta D9" in failures[0]
    assert "member/new" in snapshot["members"]
    assert (await workspace.tracker.read_issue(issue_key="member/alpha")).body == before
    workspace.read_only()


@pytest.mark.parametrize(
    "replacement",
    [
        "No deliverable here.",
        "* D2 — a condensed Fix bullet",
        "## Unnumbered definition\n\nThe implementation exists.",
        "<!-- **D2 — hidden** -->",
        "> **D2 — quoted**",
        "```\n**D2 — example**\n```",
        "**D2 — one**\n\n## D2 — another",
    ],
)
async def test_absent_unnumbered_quoted_and_ambiguous_targets_fail(
    workspace, replacement
):
    await workspace.seed(
        [{"key": "member/beta", "body": replacement, "labels": [CLASSIFICATION]}]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 2
    assert all("member/beta D2" in failure for failure in failures)
    await workspace.seed(
        [{"key": "member/beta", "body": "## D2 — restored", "labels": [CLASSIFICATION]}]
    )
    assert (await model_agreement(workspace.tracker, classification=CLASSIFICATION))[
        0
    ] == ()


async def test_comment_pointer_fails_naming_its_source_and_target(workspace):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": "[member/beta D2](https://tracker.invalid/issue/beta#comment-snapshot)",
                "labels": [CLASSIFICATION],
            }
        ]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 1
    assert all(
        value in failures[0] for value in ("member/new", "member/beta", "comment")
    )


async def test_native_mention_cannot_hide_a_comment_target(workspace):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": (
                    '<issue id="member/beta" '
                    'href="https://tracker.invalid/issue/beta#comment-old">'
                    "member/beta</issue> D2"
                ),
                "labels": [CLASSIFICATION],
            }
        ]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 1 and "comment or unnumbered fragment" in failures[0]


async def test_native_criterion_pointer_reads_its_actual_family(workspace):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": '<issue id="criterion/alpha">criterion/alpha</issue>',
                "labels": [CLASSIFICATION],
            }
        ]
    )
    failures, snapshot = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert failures == ()
    assert snapshot["criteria"]["member/alpha"] == ["criterion/alpha"]


@pytest.mark.parametrize(
    "mutation",
    [
        "member_body",
        "criterion_body",
        "member_added",
        "member_removed",
        "criterion_reparented",
    ],
)
async def test_current_port_snapshot_fails_drift_instead_of_passing_old_fixture(
    workspace, mutation
):
    _, baseline = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    _, unchanged = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    compare_snapshot(baseline, unchanged)
    changes = {
        "member_body": {
            "key": "member/beta",
            "body": "## D2 — changed definition",
            "labels": [CLASSIFICATION],
        },
        "criterion_body": {
            "key": "criterion/alpha",
            "body": "**Check:** changed",
            "parent": "member/alpha",
            "labels": ["criterion"],
        },
        "member_added": {
            "key": "new",
            "body": "new member",
            "labels": [CLASSIFICATION],
        },
        "member_removed": {
            "key": "member/beta",
            "body": "## D2 — Define the shared identity.",
            "labels": [],
        },
        "criterion_reparented": {
            "key": "criterion/alpha",
            "body": "**Check:** changed parent",
            "parent": "member/beta",
            "labels": ["criterion"],
        },
    }
    await workspace.seed([changes[mutation]])
    _, changed = await model_agreement(workspace.tracker, classification=CLASSIFICATION)
    with pytest.raises(AssertionError, match="model snapshot drift"):
        compare_snapshot(baseline, changed)
    workspace.read_only()


def test_explicit_deliverable_numbering_and_parenthesized_multiple_references():
    assert numbered_deliverables(
        "## Deliverables\n\n1. First\n2. Second\n\n## Evidence\n\n3. Not a deliverable"
    ) == Counter({"1": 1, "2": 1})


async def test_parenthesized_targets_each_reach_the_actual_checker(workspace):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": (
                    '<issue id="member/beta">member/beta</issue> (D1 owner, D2 rule)'
                ),
                "labels": [CLASSIFICATION],
            }
        ]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 1 and "member/beta D1" in failures[0]


async def test_external_native_criterion_is_resolved_by_its_own_parent(workspace):
    await workspace.seed(
        [
            {"key": "outside/parent", "body": "An unmarked owner", "labels": []},
            {
                "key": "outside/criterion",
                "body": "**Check:** External condition",
                "parent": "outside/parent",
                "labels": ["criterion"],
            },
            {
                "key": "member/new",
                "body": "[outside/criterion](https://tracker.invalid/condition)",
                "labels": [CLASSIFICATION],
            },
        ]
    )
    failures, snapshot = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert failures == ()
    assert snapshot["criteria"]["outside/parent"] == ["outside/criterion"]
    assert "outside/parent" not in snapshot["members"]


@pytest.mark.parametrize(
    "reference",
    [
        "`member/beta` D2",
        "[member/beta D2](https://tracker.invalid/issue/beta)",
        "[member/beta](https://tracker.invalid/issue/beta) D2",
        '<issue id="member/beta">member/beta</issue> D2',
    ],
)
async def test_reference_forms_retain_exact_source_span(workspace, reference):
    await workspace.seed(
        [{"key": "member/new", "body": reference, "labels": [CLASSIFICATION]}]
    )
    source = await workspace.tracker.read_issue(issue_key="member/new")
    assert explicit_pointers(source)[0].spelling == reference
    assert (await model_agreement(workspace.tracker, classification=CLASSIFICATION))[
        0
    ] == ()


@pytest.mark.parametrize(
    "body",
    [
        '<issue id="member/beta">member/beta</issue> D1\u2013D3',
        '<issue id="member/beta">member/beta</issue> (D1-D3)',
    ],
)
async def test_ranges_refuse_instead_of_checking_only_endpoints(workspace, body):
    await workspace.seed(
        [{"key": "member/new", "body": body, "labels": [CLASSIFICATION]}]
    )
    with pytest.raises(AssertionError, match="numbered reference range"):
        await model_agreement(workspace.tracker, classification=CLASSIFICATION)


async def test_literal_inline_markup_is_not_a_reference(workspace):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": '`<issue id="missing">missing</issue>` D9',
                "labels": [CLASSIFICATION],
            }
        ]
    )
    assert (await model_agreement(workspace.tracker, classification=CLASSIFICATION))[
        0
    ] == ()


async def test_opaque_criterion_key_is_not_parsed_as_a_deliverable_number(workspace):
    await workspace.seed(
        [
            {
                "key": "D2",
                "body": "**Check:** This is a native identity",
                "parent": "member/alpha",
                "labels": ["criterion"],
            },
            {
                "key": "member/new",
                "body": '<issue id="D2">D2</issue>',
                "labels": [CLASSIFICATION],
            },
        ]
    )
    failures, snapshot = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert failures == ()
    assert "D2" in snapshot["criteria"]["member/alpha"]


@pytest.mark.parametrize("anchor,valid", [("unnumbered-section", False), ("D2", True)])
async def test_link_anchor_cannot_redirect_a_numbered_reference(
    workspace, anchor, valid
):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": f"[member/beta D2](https://tracker.invalid/issue/beta#{anchor})",
                "labels": [CLASSIFICATION],
            }
        ]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert bool(failures) is not valid


async def test_unreadable_target_names_the_pointer_and_preserves_cause(workspace):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": '<issue id="missing/target">missing/target</issue> D9',
                "labels": [CLASSIFICATION],
            }
        ]
    )
    with pytest.raises(
        AssertionError, match=r"member/new.*missing/target.*could not be read"
    ) as raised:
        await model_agreement(workspace.tracker, classification=CLASSIFICATION)
    assert raised.value.__cause__ is not None


async def test_changes_during_reference_reads_refuse_a_mixed_snapshot(
    workspace, monkeypatch
):
    await workspace.seed(
        [
            {"key": "external", "body": "## D1 — External definition", "labels": []},
            {
                "key": "member/new",
                "body": '<issue id="external">external</issue> D1',
                "labels": [CLASSIFICATION],
            },
        ]
    )
    original = workspace.tracker.read_planning_issue
    changed = False

    async def editing(*, issue_key):
        nonlocal changed
        result = await original(issue_key=issue_key)
        if issue_key == "external" and not changed:
            changed = True
            await workspace.seed(
                [
                    {
                        "key": "member/beta",
                        "body": "## D2 — Changed during pass",
                        "labels": [CLASSIFICATION],
                    }
                ]
            )
        return result

    monkeypatch.setattr(workspace.tracker, "read_planning_issue", editing)
    with pytest.raises(AssertionError, match="model snapshot drift"):
        await model_agreement(workspace.tracker, classification=CLASSIFICATION)


def test_fenced_comment_opener_does_not_consume_later_real_definitions():
    assert numbered_deliverables(
        "```\n<!-- an example\n```\n## D2 — Actual definition"
    ) == Counter({"2": 1})


@pytest.mark.live
async def test_live_workspace_equals_supplied_snapshot(
    live_model_tracker, live_model_snapshot
):
    failures, current = await model_agreement(
        live_model_tracker, classification=CLASSIFICATION
    )
    compare_snapshot(live_model_snapshot, current)
    assert failures == (), "\n".join(failures)
