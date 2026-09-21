"""The configured comment representation of a pinned fire-time ruling."""

import json
from collections import Counter
from collections.abc import Collection, Mapping, Sequence

from pydantic import ValidationError

from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import RulingUnrecordedError
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.types.domain.agent import (
    Ruling,
    RulingAnswer,
    RulingAuthor,
    RulingId,
)
from kodezart.types.domain.escalation import DeliverableEscalation

#: What a session is shown when the tracker carries no answer yet. Stated
#: as an absence it can read rather than an empty string it could mistake
#: for a rendering failure.
EMPTY_REGISTRY = "Confirmed empty ruling registry."


def owed_rulings(
    *,
    subject: str,
    answers: Sequence[RulingAnswer],
    addressable: frozenset[str],
    recorded: Collection[RulingId],
) -> tuple[Ruling, ...]:
    """The records this pass still owes the tracker, in identity order.

    Arithmetic, not judgement: the identity of each answer is minted from
    the exact pair it names, machine authorship is stamped here rather than
    answered, and an identity the tracker already carries is dropped — which
    is what makes a second pass over the same fire owe nothing.

    An answer addressed outside the fire, two answers to one question, or an
    answer no valid record can be built from is refused rather than written,
    because each of the three would put text on the tracker that its reader
    could not address back to the issue whose text raised the question.

    An answer whose question restates an earlier pinned one names that
    earlier question's exact words, and the identity it replaces is minted
    from them here: a restatement is a different question, so it is a new
    record that names the one it replaces rather than an edit of it. Naming
    its own question, or a question the tracker carries no answer for, is
    refused for the same reason the three above are.
    """
    owed: dict[RulingId, Ruling] = {}
    seen: set[tuple[str, str]] = set()
    for answer in answers:
        if answer.issue_ref not in addressable:
            raise RulingUnrecordedError(
                issue_key=subject,
                reason=f"{answer.issue_ref!r} is not a member of this fire",
            )
        address = (answer.issue_ref, answer.question)
        if address in seen:
            raise RulingUnrecordedError(
                issue_key=subject,
                reason=f"one question on {answer.issue_ref!r} was answered twice",
            )
        seen.add(address)
        identity = mint_ruling_id(issue_ref=answer.issue_ref, question=answer.question)
        if identity in recorded:
            continue
        superseded: RulingId | None = None
        if answer.supersedes_question is not None:
            superseded = mint_ruling_id(
                issue_ref=answer.issue_ref, question=answer.supersedes_question
            )
            if superseded == identity:
                raise RulingUnrecordedError(
                    issue_key=subject,
                    reason=(
                        f"an answer on {answer.issue_ref!r} supersedes its own question"
                    ),
                )
            if superseded not in recorded:
                raise RulingUnrecordedError(
                    issue_key=subject,
                    reason=(
                        f"an answer on {answer.issue_ref!r} supersedes a question the "
                        "tracker carries no answer for"
                    ),
                )
        try:
            ruling = Ruling.model_validate(
                {
                    **answer.model_dump(exclude={"supersedes_question", "deliverable"}),
                    "ruling_id": identity,
                    "authored_by": RulingAuthor.MACHINE,
                    "protected_tests": None,
                    "supersedes": superseded,
                }
            )
        except ValidationError as exc:
            raise RulingUnrecordedError(
                issue_key=subject,
                reason=f"an answer on {answer.issue_ref!r} is not a valid record",
            ) from exc
        owed[identity] = ruling
    return tuple(owed[identity] for identity in sorted(owed))


def excess_answers(
    *, answers: Sequence[RulingAnswer], stated: Sequence[str]
) -> tuple[RulingAnswer, ...]:
    """The answers naming work the subject's stated deliverables do not cover.

    Arithmetic: exact membership of the item the answer names in the items the
    section states. An answer naming nothing adds nothing to build and is
    never excess; an answer naming an item no section states is excess, which
    is the same answer for a subject with no such section at all.

    Membership, never containment: a substring test would let one stated item
    cover every item whose words happen to occur inside it.
    """
    covered = {item.strip() for item in stated}
    return tuple(
        answer
        for answer in answers
        if answer.deliverable is not None and answer.deliverable.strip() not in covered
    )


def escalation_marker(
    *, ruling_id: RulingId, lane_key: str, marker_prefixes: Mapping[str, str]
) -> str:
    """Address one refused answer under the operation's escalation prefix.

    The occurrence key is the question's own identity, so a second pass over
    the same open question addresses the same comment instead of raising the
    question twice, and a reader can address the raise back to what caused it.
    """
    return compose_comment_marker(
        prefixes=marker_prefixes,
        purpose="escalation",
        lane=lane_key,
        occurrence_key=ruling_id,
    )


def render_deliverable_escalation(
    *,
    escalation: DeliverableEscalation,
    ruling_id: RulingId,
    lane_key: str,
    marker_prefixes: Mapping[str, str],
) -> str:
    """The same marker/fenced-JSON framing a pinned answer is rendered in."""
    return marked_comment_body(
        marker=escalation_marker(
            ruling_id=ruling_id,
            lane_key=lane_key,
            marker_prefixes=marker_prefixes,
        ),
        body="```json\n"
        + escalation.model_dump_json(by_alias=True, indent=2)
        + "\n```",
    )


def pinned_registry(rulings: Sequence[Ruling]) -> str:
    """The text a session is shown for the answers already pinned."""
    return "\n".join(ruling.model_dump_json() for ruling in rulings) or EMPTY_REGISTRY


def repeated_designations(records: Sequence[Ruling]) -> tuple[tuple[str, str], ...]:
    """Designated-test addresses more than one pinned record claims, in order.

    Arithmetic, not judgement. A change to a designated protected test is
    claimed against the pinned record that designates it, so each address must
    resolve to exactly one claimable identity. Within one record the record's
    own validator already forbids a repeated address; across records nothing
    does, and an address two records claim has no single addressee.
    """
    counts: Counter[tuple[str, str]] = Counter(
        (reference.path, reference.qualified_name)
        for record in records
        for reference in record.protected_tests or ()
    )
    return tuple(sorted(address for address, count in counts.items() if count >= 2))


def ruling_marker(
    *, ruling_id: RulingId, lane_key: str, marker_prefixes: Mapping[str, str]
) -> str:
    """Use the question identity as the occurrence key within its lane."""
    return compose_comment_marker(
        prefixes=marker_prefixes,
        purpose="ruling",
        lane=lane_key,
        occurrence_key=ruling_id,
    )


def render_ruling(
    *, ruling: Ruling, lane_key: str, marker_prefixes: Mapping[str, str]
) -> str:
    """Render every required field, including authorship, in the pinned text.

    A field whose value is the absence itself is left out rather than
    written as a null, which is what keeps the bytes of a record that
    designates nothing and replaces nothing exactly what they were before
    either field existed.
    """
    _require_identity(ruling)
    marker = ruling_marker(
        ruling_id=ruling.ruling_id,
        lane_key=lane_key,
        marker_prefixes=marker_prefixes,
    )
    absent = {
        name
        for name in ("protected_tests", "supersedes")
        if getattr(ruling, name) is None
    }
    return marked_comment_body(
        marker=marker,
        body="```json\n"
        + ruling.model_dump_json(by_alias=True, indent=2, exclude=absent)
        + "\n```",
    )


def parse_ruling(
    *, body: str, lane_key: str, marker_prefixes: Mapping[str, str]
) -> Ruling:
    """Decode the complete record without guessing authorship from prose."""
    marker, separator, payload = body.partition("\n```json\n")
    if not separator or not payload.endswith("\n```"):
        raise ValueError("the ruling comment framing is invalid")
    payload = payload[: -len("\n```")]
    json.loads(payload, object_pairs_hook=_unique_object)
    ruling = Ruling.model_validate_json(payload, strict=True)
    _require_identity(ruling)
    if marker != ruling_marker(
        ruling_id=ruling.ruling_id,
        lane_key=lane_key,
        marker_prefixes=marker_prefixes,
    ):
        raise ValueError("the ruling marker does not match its lane and identity")
    return ruling


def _require_identity(ruling: Ruling) -> None:
    if ruling.ruling_id != mint_ruling_id(
        issue_ref=ruling.issue_ref, question=ruling.question
    ):
        raise ValueError("the ruling identity does not address its exact question")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError(f"duplicate ruling field {key!r}")
        fields[key] = value
    return fields
