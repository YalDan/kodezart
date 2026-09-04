"""What became of one tool call that did not answer.

A transport that loses a call knows more than "it failed", and what it
knows decides what may be done about it.  Three things can have happened,
and they are not told apart by how much went wrong but by WHERE the call
was when it went wrong:

* the session was gone before the request was written — nothing reached
  the server, and making the call again on a fresh session is safe;
* the request was written and no answer ever came — the server may have
  run it, and making it again could run it twice (KOD-305);
* the session stood and the call simply failed — the server said no, or
  answered something unusable, and a fresh session would repair nothing.

``kind`` discriminates.  Each member states for itself whether the
session under the call is dead, because that is the second decision a
host has to make and it is not the same as the first: a request that was
written and lost to a read timeout leaves the session standing, and one
lost to the server's own exit does not.
"""

from typing import Annotated, Literal

from pydantic import Field

from kodezart.types.base import CamelCaseModel


class CallFailure(CamelCaseModel):
    """Base of the union: ``kind`` discriminates, ``session_died`` is each
    member's own."""

    kind: str
    session_died: bool


class SessionGone(CallFailure):
    """The session was gone before the request was written.

    Nothing reached the server, so the call may be made again on a fresh
    session: this is the arm the measured 18:22 death took (KOD-286).
    """

    kind: Literal["session_gone"] = "session_gone"
    session_died: Literal[True] = True


class CallUnanswered(CallFailure):
    """The request was written and no answer came.

    Whether the server ran it is UNKNOWN, so the call is not made again:
    a write the server performed and then died before acknowledging would
    be performed twice (KOD-305).  The session may or may not have died
    with it — a read timeout leaves it standing, the server's exit does
    not — and the member says which.
    """

    kind: Literal["unanswered"] = "unanswered"
    session_died: bool


class CallFailed(CallFailure):
    """The session stands and this call did not succeed.

    A refusal the server composed, an answer that would not parse, a
    dropped exchange on a transport that is otherwise up: the server is
    there, and a fresh session would repair nothing.
    """

    kind: Literal["call_failed"] = "call_failed"
    session_died: Literal[False] = False


type AnyCallFailure = Annotated[
    SessionGone | CallUnanswered | CallFailed,
    Field(discriminator="kind"),
]
