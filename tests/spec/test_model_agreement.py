"""Spec backend: queried bodies resolve explicit deliverable/criterion pointers.

A named model value resolves to the one member deliverable that defines it,
so one value never carries two names and an attribution cycle never stands in
for a definition.
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
from tests.fakes import FakeMcpIssue
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
DEFINE = "Define"
CONSUME = "Consume"
_VALUE_VERB = re.compile(
    rf"^(?P<verb>{DEFINE}|{CONSUME})\s+(?P<name>\S.*)$", re.IGNORECASE
)


@dataclass(frozen=True)
class Pointer:
    source: str
    target: str
    number: str | None
    spelling: str
    comment_target: bool = False
    native_id: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class ValueSite:
    """One member deliverable that defines or consumes a named model value."""

    source: str
    number: str
    verb: str
    name: str
    attribution: Pointer | None

    @property
    def address(self) -> tuple[str, str]:
        return self.source, self.number

    def __str__(self) -> str:
        return f"{self.source} D{self.number}"


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


def _heading_number(text: str) -> str | None:
    """The deliverable number a declared heading opens, never a bare mention."""
    heading = _HEADING.match(text)
    if heading and (
        heading["format"]
        or re.match(r"\s*[\u2014\u2013:-]", text[heading.end("number") :])
    ):
        return heading["number"]
    return None


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
        number = _heading_number(text)
        if number is not None:
            numbers[number] += 1
        normalized = text.strip("#* :").casefold()
        if normalized.startswith(("deliverables", "deliverable sketch")):
            numbered_list = True
        elif text.startswith("#") or (text.startswith("**") and heading is None):
            numbered_list = False
        ordinal = _ORDINAL.match(text) if numbered_list else None
        if ordinal:
            numbers[ordinal["number"]] += 1
    return numbers


def _line_pointers(issue_key: str, line: str) -> list[Pointer]:
    """Read one visible line; a numbered range refuses rather than narrows."""
    pointers = []
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
                f"{issue_key}: numbered reference range is not expanded: "
                f"{line[match.start() :]}"
            )
        if re.search(r"D\d+\s*[-\u2013\u2014]\s*D?\d", declared):
            raise AssertionError(
                f"{issue_key}: numbered reference range is not expanded: {declared}"
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
        native_id = None
        if match.re is _ISSUE_LINK:
            identity = re.search(r"""\bid\s*=\s*["']([^"']*)["']""", match["attrs"])
            native_id = unescape(identity[1]) if identity else None
        if not numbers and " " in target:
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
                    issue_key,
                    target,
                    number,
                    line[match.start() : end],
                    comment or bool(anchor and anchor.casefold() != f"d{number}"),
                    native_id=native_id,
                    url=unescape(fragment).strip("<>") or None,
                )
            )
    return pointers


def explicit_pointers(issue: TrackerIssue) -> tuple[Pointer, ...]:
    """Read native issue mentions and Markdown references with D-n surfaces.

    An explicit native mention ID is resolved by the port and compared with
    its displayed canonical key; URL path layouts are never parsed as IDs.
    Ordinary issue citations
    have no numbered target; the reader recognizes a native criterion target
    from its actual membership, not from an identifier-shaped title.
    """
    return tuple(
        pointer
        for line in _visible_lines(issue.body)
        for pointer in _line_pointers(issue.issue_key, line)
    )


def _value_heading(text: str) -> tuple[str, str, str] | None:
    """A declared heading that names a model value, with its verb."""
    heading = _HEADING.match(text)
    number = _heading_number(text)
    if heading is None or number is None:
        return None
    remainder = text[heading.end("number") :].lstrip(" \u2014\u2013:-").rstrip()
    remainder = remainder.removesuffix("**").rstrip().rstrip(".").rstrip()
    named = _VALUE_VERB.match(remainder)
    if named is None:
        return None
    return number, named["verb"].capitalize(), named["name"]


def value_sites(issue: TrackerIssue) -> tuple[ValueSite, ...]:
    """Read the named value each deliverable of a body defines or consumes.

    A consuming deliverable attributes the definition to the one numbered
    pointer inside it; none and several are both unresolved attributions.
    """
    sections: list[tuple[str, str, str, list[Pointer]]] = []
    section: list[Pointer] | None = None
    for line in _visible_lines(issue.body):
        text = line.strip()
        named = _value_heading(text)
        if named is not None:
            number, verb, name = named
            section = []
            sections.append((number, verb, name, section))
        elif _heading_number(text) is not None:
            section = None
        elif section is not None:
            section.extend(
                pointer
                for pointer in _line_pointers(issue.issue_key, line)
                if pointer.number is not None
            )
    return tuple(
        ValueSite(
            issue.issue_key,
            number,
            verb,
            name,
            attributions[0] if len(attributions) == 1 else None,
        )
        for number, verb, name, attributions in sections
    )


def _resolve_value(site: ValueSite, sites: dict[tuple[str, str], ValueSite]):
    """Follow attributions to the one definition, or say what it is not."""
    visited = [site.address]
    current = site
    while True:
        if current.attribution is None:
            return [
                f"{site}: {site.name!r} attributes its definition to no single "
                "numbered pointer"
            ]
        address = (current.attribution.target, current.attribution.number)
        if address in visited:
            trail = " -> ".join(
                f"{source} D{number}" for source, number in (*visited, address)
            )
            return [
                f"{site}: {site.name!r} definition cycle {trail} resolves to no "
                "definition"
            ]
        visited.append(address)
        defining = sites.get(address)
        if defining is None:
            # An ordinary numbered deliverable declares no model value; the
            # pointer invariant owns whether that target exists at all.
            return []
        if defining.verb == DEFINE:
            if defining.name.casefold() != site.name.casefold():
                return [
                    f"{site} names {defining} {site.name!r}; {defining.source} "
                    f"defines it as {defining.name!r}"
                ]
            return []
        current = defining


def value_agreement(documents: dict[str, TrackerIssue]) -> tuple[str, ...]:
    """One name per model value, resolved to the one member that defines it."""
    failures = []
    sites: dict[tuple[str, str], ValueSite] = {}
    for key in sorted(documents):
        for site in value_sites(documents[key]):
            if site.address in sites:
                failures.append(f"{site}: the deliverable names a second value")
                continue
            sites[site.address] = site
    definitions: dict[str, ValueSite] = {}
    for address in sorted(sites):
        site = sites[address]
        if site.verb != DEFINE:
            continue
        owner = definitions.setdefault(site.name.casefold(), site)
        if owner is not site:
            failures.append(f"{site} and {owner} both define {site.name!r}")
    for address in sorted(sites):
        site = sites[address]
        if site.verb == CONSUME:
            failures.extend(_resolve_value(site, sites))
    return tuple(failures)


def _document_projection(document):
    return {
        "body": document.body,
        "parent": document.parent_key,
        "labels": sorted(document.issue_labels),
        "url": document.url,
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
    failures = list(value_agreement(documents))
    consulted_criteria = None
    native_mentions = {}
    pointers = tuple(
        pointer
        for document in documents.values()
        for pointer in explicit_pointers(document)
    )
    for pointer in pointers:
        if pointer.native_id is not None:
            try:
                native_target = await tracker.read_planning_issue(
                    issue_key=pointer.native_id
                )
            except Exception as exc:
                raise AssertionError(
                    f"{pointer.source}: {pointer.spelling} -> {pointer.target}: "
                    "native target could not be read"
                ) from exc
            if native_target.issue_key != pointer.target:
                raise AssertionError(
                    f"{pointer.source}: {pointer.spelling}: native mention identity "
                    f"differs from displayed key {pointer.target}"
                )
            if pointer.target in documents and _document_projection(
                documents[pointer.target]
            ) != _document_projection(native_target):
                raise AssertionError(f"model snapshot drift: target {pointer.target}")
            documents[pointer.target] = native_target
            native_mentions[pointer.native_id] = native_target
        if pointer.comment_target and pointer.number is not None:
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
            if pointer.comment_target:
                # A comment link's prose label is not an issue-key grammar.
                # Consult actual criterion identities only when needed to
                # distinguish an external native criterion from that prose.
                if consulted_criteria is None:
                    consulted_criteria = {
                        issue.issue_key: issue
                        for issue in await tracker.read_labeled_issues(
                            classification="criterion"
                        )
                    }
                if pointer.target not in consulted_criteria:
                    continue
                documents[pointer.target] = consulted_criteria[pointer.target]
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
        if (
            (pointer.number is not None or "criterion" in target.issue_labels)
            and not pointer.comment_target
            and pointer.url is not None
            and urlsplit(pointer.url)._replace(fragment="").geturl() != target.url
        ):
            raise AssertionError(
                f"{pointer.source}: {pointer.spelling}: pointer URL differs from "
                f"the reported URL of {pointer.target}"
            )
        if pointer.comment_target:
            if "criterion" not in target.issue_labels:
                continue
            failures.append(
                f"{pointer.source}: {pointer.spelling} -> {pointer.target}: "
                "comment or unnumbered fragment is not a criterion"
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
    if consulted_criteria is not None:
        current_criteria = await tracker.read_labeled_issues(classification="criterion")
        if sorted(issue.issue_key for issue in current_criteria) != sorted(
            consulted_criteria
        ):
            raise AssertionError("model snapshot drift: consulted criterion census")
    for native_id, captured in native_mentions.items():
        current = await tracker.read_planning_issue(issue_key=native_id)
        if current.issue_key != captured.issue_key or _document_projection(
            current
        ) != _document_projection(captured):
            raise AssertionError(f"model snapshot drift: native mention {native_id}")
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
    url = (await workspace.tracker.read_issue(issue_key="member/beta")).url
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": f"[member/beta D2]({url}#comment-snapshot)",
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
    url = (await workspace.tracker.read_issue(issue_key="member/beta")).url
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": (
                    '<issue id="member/beta" '
                    f'href="{url}#comment-old">'
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
        ]
    )
    url = (await workspace.tracker.read_issue(issue_key="outside/criterion")).url
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": f"[outside/criterion]({url})",
                "labels": [CLASSIFICATION],
            }
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
        "[member/beta D2]({url})",
        "[member/beta]({url}) D2",
        '<issue id="member/beta">member/beta</issue> D2',
    ],
)
async def test_reference_forms_retain_exact_source_span(workspace, reference):
    reference = reference.format(
        url=(await workspace.tracker.read_issue(issue_key="member/beta")).url
    )
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
    url = (await workspace.tracker.read_issue(issue_key="member/beta")).url
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": f"[member/beta D2]({url}#{anchor})",
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


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize(
    "reference",
    [
        "[{key}]({url}#comment-old)",
        "[{key}]({url}?commentId=old)",
        '<issue id="{key}" href="{url}#section">{key}</issue>',
    ],
)
async def test_unnumbered_criterion_comment_redirect_refuses(
    workspace, external, reference
):
    key = "outside/criterion" if external else "criterion/alpha"
    if external:
        await workspace.seed(
            [
                {"key": "outside/parent", "body": "Owner", "labels": []},
                {
                    "key": key,
                    "body": "**Check:** External condition",
                    "parent": "outside/parent",
                    "labels": ["criterion"],
                },
            ]
        )
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": reference.format(
                    key=key, url=(await workspace.tracker.read_issue(issue_key=key)).url
                ),
                "labels": [CLASSIFICATION],
            }
        ]
    )
    failures, snapshot = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 1
    assert all(part in failures[0] for part in ("member/new", key, "comment"))
    assert key in snapshot["criteria"]["outside/parent" if external else "member/alpha"]
    workspace.read_only()


async def test_contextual_comment_label_is_not_guessed_to_be_a_native_key(workspace):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": "[ruling](https://tracker.invalid/issue/owner#comment-old)",
                "labels": [CLASSIFICATION],
            }
        ]
    )
    assert (await model_agreement(workspace.tracker, classification=CLASSIFICATION))[
        0
    ] == ()
    workspace.read_only()


async def test_consulted_criterion_census_cannot_change_during_resolution(
    workspace, monkeypatch
):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": "[ruling](https://tracker.invalid/issue/owner#comment-old)",
                "labels": [CLASSIFICATION],
            },
            {"key": "outside/parent", "body": "Unmarked owner", "labels": []},
        ]
    )
    original = workspace.tracker.read_labeled_issues
    changed = False

    async def changing(*, classification):
        nonlocal changed
        result = await original(classification=classification)
        if classification == "criterion" and not changed:
            changed = True
            await workspace.seed(
                [
                    {
                        "key": "ruling",
                        "body": "**Check:** Actually a criterion",
                        "parent": "outside/parent",
                        "labels": ["criterion"],
                    }
                ]
            )
        return result

    monkeypatch.setattr(workspace.tracker, "read_labeled_issues", changing)
    with pytest.raises(AssertionError, match="model snapshot drift"):
        await model_agreement(workspace.tracker, classification=CLASSIFICATION)


@pytest.mark.parametrize("cancel", [False, True])
async def test_criterion_lookup_failure_is_not_contextual_prose(
    workspace, monkeypatch, cancel
):
    import asyncio

    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": "[ruling](https://tracker.invalid/issue/owner#comment-old)",
                "labels": [CLASSIFICATION],
            }
        ]
    )
    original = workspace.tracker.read_labeled_issues

    async def failed(*, classification):
        if classification == "criterion":
            if cancel:
                raise asyncio.CancelledError
            raise RuntimeError("native criterion census unavailable")
        return await original(classification=classification)

    monkeypatch.setattr(workspace.tracker, "read_labeled_issues", failed)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await model_agreement(workspace.tracker, classification=CLASSIFICATION)


async def test_native_mention_cannot_redirect_a_different_issue(workspace):
    await workspace.seed(
        [
            {"key": "wrong/target", "body": "No definition here", "labels": []},
            {
                "key": "member/new",
                "body": '<issue id="wrong/target">member/beta</issue> D2',
                "labels": [CLASSIFICATION],
            },
        ]
    )
    with pytest.raises(AssertionError, match=r"native mention identity.*member/beta"):
        await model_agreement(workspace.tracker, classification=CLASSIFICATION)
    workspace.read_only()


@pytest.mark.parametrize("remap", [False, True])
async def test_native_alias_is_resolved_to_its_canonical_key_and_rechecked(
    workspace, monkeypatch, remap
):
    await workspace.seed(
        [
            {"key": "wrong/target", "body": "## D2 — Another owner", "labels": []},
            {
                "key": "member/new",
                "body": '<issue id="opaque-native-id">member/beta</issue> D2',
                "labels": [CLASSIFICATION],
            },
        ]
    )
    original = workspace.tracker.read_planning_issue
    aliases = 0

    async def native_alias(*, issue_key):
        nonlocal aliases
        if issue_key == "opaque-native-id":
            aliases += 1
            issue_key = "wrong/target" if remap and aliases > 1 else "member/beta"
        return await original(issue_key=issue_key)

    monkeypatch.setattr(workspace.tracker, "read_planning_issue", native_alias)
    if remap:
        with pytest.raises(
            AssertionError, match="model snapshot drift: native mention"
        ):
            await model_agreement(workspace.tracker, classification=CLASSIFICATION)
    else:
        failures, _ = await model_agreement(
            workspace.tracker, classification=CLASSIFICATION
        )
        assert failures == ()
    assert aliases == 2
    workspace.read_only()


@pytest.mark.parametrize("native", [False, True])
async def test_hyperlink_cannot_redirect_the_displayed_target(workspace, native):
    await workspace.seed(
        [{"key": "wrong/target", "body": "No definition", "labels": []}]
    )
    url = (await workspace.tracker.read_issue(issue_key="wrong/target")).url
    body = (
        f'<issue id="member/beta" href="{url}">member/beta</issue> D2'
        if native
        else f"[member/beta D2]({url})"
    )
    await workspace.seed(
        [{"key": "member/new", "body": body, "labels": [CLASSIFICATION]}]
    )
    with pytest.raises(AssertionError, match=r"pointer URL differs.*member/beta"):
        await model_agreement(workspace.tracker, classification=CLASSIFICATION)
    workspace.read_only()


async def test_target_url_change_refuses_the_retained_pointer(workspace, monkeypatch):
    await workspace.seed(
        [{"key": "external", "body": "## D2 — Original target", "labels": []}]
    )
    url = (await workspace.tracker.read_issue(issue_key="external")).url
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": f"[external D2]({url})",
                "labels": [CLASSIFICATION],
            }
        ]
    )
    original = workspace.tracker.read_planning_issue
    reads = 0

    async def redirecting(*, issue_key):
        nonlocal reads
        issue = await original(issue_key=issue_key)
        if issue_key == "external":
            reads += 1
            if reads > 1:
                return issue.model_copy(
                    update={"url": "https://tracker.invalid/changed"}
                )
        return issue

    monkeypatch.setattr(workspace.tracker, "read_planning_issue", redirecting)
    with pytest.raises(AssertionError, match="model snapshot drift: target external"):
        await model_agreement(workspace.tracker, classification=CLASSIFICATION)


@pytest.mark.parametrize("target_kind", ["context", "numbered", "criterion"])
async def test_short_parent_citation_is_context_only_for_an_ordinary_target(
    workspace, target_kind
):
    # Actual 644 amendment cites ordinary parent 744 with this short native URL.
    # The measured issue response reports the longer canonical slug URL.
    await workspace.put(
        FakeMcpIssue(
            id="KOD-744",
            description="## D2 — Architecture acceptance",
            labels=["acceptance-condition"] if target_kind == "criterion" else [],
            parent_id="member/beta" if target_kind == "criterion" else None,
            url="https://linear.app/duckburg/issue/KOD-744/architecture-review",
        )
    )
    label = "KOD-744 D2" if target_kind == "numbered" else "KOD-744"
    await workspace.seed(
        [
            {
                "key": "KOD-644",
                "body": f"Source amendment: [{label}](<https://linear.app/duckburg/issue/KOD-744>).",
                "labels": [CLASSIFICATION],
            }
        ]
    )
    if target_kind == "context":
        failures, _ = await model_agreement(
            workspace.tracker, classification=CLASSIFICATION
        )
        assert failures == ()
    else:
        with pytest.raises(AssertionError, match=r"pointer URL differs.*KOD-744"):
            await model_agreement(workspace.tracker, classification=CLASSIFICATION)
    workspace.read_only()


async def test_committed_fixture_resolves_each_name_to_its_one_definition(workspace):
    _, documents, _ = await read_model(workspace.tracker, classification=CLASSIFICATION)
    sites = {
        site.address: site
        for document in documents.values()
        for site in value_sites(document)
    }
    use = sites[("member/alpha", "1")]
    definition = sites[("member/beta", "2")]
    assert use.verb == CONSUME
    assert use.attribution is not None
    assert (use.attribution.target, use.attribution.number) == definition.address
    assert definition.verb == DEFINE
    assert use.name.casefold() == definition.name.casefold()
    assert value_agreement(documents) == ()
    workspace.read_only()


async def test_two_bodies_naming_one_value_differently_fail(workspace):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": (
                    "## D3 \u2014 Consume the criterion address\n\n"
                    "The defining contract is "
                    '<issue id="member/beta">member/beta</issue> D2.'
                ),
                "labels": [CLASSIFICATION],
            }
        ]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 1
    assert all(
        part in failures[0]
        for part in (
            "member/new D3",
            "member/beta D2",
            "the criterion address",
            "the shared identity",
        )
    )
    workspace.read_only()


async def test_two_bodies_attributing_to_each_other_fail_as_an_unresolvable_cycle(
    workspace,
):
    await workspace.seed(
        [
            {
                "key": "member/one",
                "body": (
                    "## D1 \u2014 Consume the shared address\n\n"
                    "The defining contract is "
                    '<issue id="member/two">member/two</issue> D2.'
                ),
                "labels": [CLASSIFICATION],
            },
            {
                "key": "member/two",
                "body": (
                    "## D2 \u2014 Consume the shared address\n\n"
                    "The defining contract is "
                    '<issue id="member/one">member/one</issue> D1.'
                ),
                "labels": [CLASSIFICATION],
            },
        ]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 2
    assert all(
        "definition cycle" in failure and "resolves to no definition" in failure
        for failure in failures
    )
    assert "member/one D1" in failures[0] and "member/two D2" in failures[0]
    workspace.read_only()


async def test_one_name_defined_by_two_members_names_both_definitions(workspace):
    await workspace.seed(
        [
            {
                "key": "member/new",
                "body": "## D3 \u2014 Define the shared identity",
                "labels": [CLASSIFICATION],
            }
        ]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 1
    assert "member/new D3" in failures[0] and "member/beta D2" in failures[0]
    workspace.read_only()


async def test_a_name_resolves_through_an_intermediate_attribution(workspace):
    await workspace.seed(
        [
            {
                "key": "member/middle",
                "body": (
                    "## D3 \u2014 Consume the shared identity\n\n"
                    "The defining contract is "
                    '<issue id="member/beta">member/beta</issue> D2.'
                ),
                "labels": [CLASSIFICATION],
            },
            {
                "key": "member/new",
                "body": (
                    "## D4 \u2014 Consume the shared identity\n\n"
                    "The defining contract is "
                    '<issue id="member/middle">member/middle</issue> D3.'
                ),
                "labels": [CLASSIFICATION],
            },
        ]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert failures == ()
    workspace.read_only()


@pytest.mark.parametrize(
    "section",
    [
        "## D3 \u2014 Consume the shared identity",
        "## D3 \u2014 Consume the shared identity\n\nEither "
        '<issue id="member/beta">member/beta</issue> D2 or '
        '<issue id="member/alpha">member/alpha</issue> D1.',
    ],
)
async def test_a_consumed_name_without_one_attribution_resolves_to_nothing(
    workspace, section
):
    await workspace.seed(
        [{"key": "member/new", "body": section, "labels": [CLASSIFICATION]}]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert len(failures) == 1
    assert (
        "member/new D3" in failures[0] and "no single numbered pointer" in failures[0]
    )
    workspace.read_only()


@pytest.mark.parametrize(
    "heading",
    [
        "## D3 \u2014 Define the shared address",
        "**D3 \u2014 Define the shared address.**",
    ],
)
async def test_both_declared_heading_formats_name_the_same_value(workspace, heading):
    await workspace.seed(
        [
            {"key": "member/new", "body": heading, "labels": [CLASSIFICATION]},
            {
                "key": "member/other",
                "body": (
                    "## D4 \u2014 Consume the shared address\n\n"
                    "The defining contract is "
                    '<issue id="member/new">member/new</issue> D3.'
                ),
                "labels": [CLASSIFICATION],
            },
        ]
    )
    failures, _ = await model_agreement(
        workspace.tracker, classification=CLASSIFICATION
    )
    assert failures == ()
    workspace.read_only()


@pytest.mark.live
async def test_live_workspace_equals_supplied_snapshot(
    live_model_tracker, live_model_snapshot
):
    failures, current = await model_agreement(
        live_model_tracker, classification=CLASSIFICATION
    )
    compare_snapshot(live_model_snapshot, current)
    assert failures == (), "\n".join(failures)
