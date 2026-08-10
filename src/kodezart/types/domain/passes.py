"""What a judgment pass session returns, and how it can fail to return it.

A pass session's product is DATA, never a write.  KOD-60 R8 part 1: the
session composes and the SERVICE gates and writes, so everything a session
produces crosses back into the process as one of the models here and is
validated at that boundary.  A field a session invented is rejected by the
model rather than posted.

Nothing here carries a verdict the session reached.  A build report names
which steps FAILED and at which sha — raw observation — and the root
versus cascade split is computed service-side from the declared check
chain (R8 part 3).  An instruction to report a cascade as a cascade is not
a guarantee; the arithmetic is.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class PassSessionFailure(StrEnum):
    """One member per way for a pass session to have NO answer.

    The same three-state discipline :class:`ScanFailureKind` holds, for the
    same reason: "returned nothing" and "returned an empty result" are two
    observable states, and only one of them is a pass that ran.  A pass
    that could not answer writes nothing at all — never a partial write
    over a partial answer.
    """

    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    EMPTY_RESPONSE = "empty_response"
    ERRORED = "errored"
    MALFORMED_OUTPUT = "malformed_output"
    NOT_RENDERABLE = "not_renderable"


class PassModel(CamelCaseModel):
    """Base for pass-session outputs: frozen, closed.

    ``extra="forbid"`` is the point rather than a habit — an unrecognised
    key in a session's answer means the session answered a question this
    model does not ask, and silently dropping it would let the shape drift
    without anything noticing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class PreparedFire(PassModel):
    """One frozen fire body the preparation session composed, and its issue.

    ``body`` is the whole replacement body for the issue.  It is frozen in
    the sense the hygiene rule means: nothing edits it after the session
    returns it, so what the scan inspected is byte-for-byte what the gate
    sees and what the tracker receives.
    """

    issue_key: str = Field(min_length=1)
    body: str = Field(min_length=1)


class FirePrepOutput(PassModel):
    """Every preparation one fire-prep session produced.

    An empty list is a real and ordinary answer — a window whose items are
    all still too vague to shape — and is not a failure.  The failure
    states are :class:`PassSessionFailure`.
    """

    preparations: tuple[PreparedFire, ...] = ()


class RepoVerification(PassModel):
    """What one grooming session observed running one repository's chain.

    ``failed_steps`` names steps by the name the operation config declares
    for them; a name the chain does not declare is not silently dropped by
    the classifier, so a session that invents one is visible rather than
    ignored.

    ``issue_keys`` are the issues the groom found blocked by this failure,
    chosen from the addressable set the SERVICE read off the board and
    rendered into the prompt.  A key outside that set is dropped by the
    service before any write (KOD-60 R13): the field is a selection from
    what the pass supplied, never an address the session may originate.
    Empty is legitimate and common: a red chain that blocks no groomed item
    produces no comment, because grooming that produces no finding produces
    no comment.
    """

    repo_url: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    failed_steps: tuple[str, ...] = ()
    issue_keys: tuple[str, ...] = ()


class GroomingOutput(PassModel):
    """Every repository verification one grooming session performed."""

    verifications: tuple[RepoVerification, ...] = ()


#: Structured-output schemas the sessions are dispatched with.  Derived
#: from the models, never hand-written: a schema and a model that can
#: disagree is a validation error waiting at the boundary.
FIRE_PREP_SCHEMA: dict[str, object] = FirePrepOutput.model_json_schema()
GROOMING_SCHEMA: dict[str, object] = GroomingOutput.model_json_schema()
