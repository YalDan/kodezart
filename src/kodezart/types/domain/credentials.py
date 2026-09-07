"""The credential shapes this build can hold, and what replaces each one.

One table, read by both surfaces that stand between a credential and the
outside world: the shipped ``credentials`` deny patterns the outbound gate
scans with (:mod:`kodezart.core.config`), and the scrubber applied at wire
egress (:mod:`kodezart.core.error_egress`).  Held as two tables, covering a
vendor meant editing both, and the vendor whose credential this deployment
actually dials was the one that fell through the gap.

A shape is registered for every vendor whose credential this process can
hold — the forge token that clones, the knowledge credential, the tracker
credential, and the engine key the SDK subprocess is handed.  Neither
consumer asks whether a deployment configured that vendor: a credential
leaving the process is never acceptable regardless of deployment, and a
shape costs nothing while its credential is absent.

Bodies are bounded below at documented lengths so short-suffix prose
("ghp_abc", "the secret_key operators supply") is not scrubbed, and above
by nothing — the literal prefix anchors are what prevent runaway
backtracking.  A sunset never removes a shape, because historical logs
still carry historical tokens.

``pattern`` may carry capture groups and ``replacement`` is the template
the scrubber substitutes, which is what lets the tokenized-URL shape keep
the scheme and host either side of the secret it replaces.  The gate reads
the same ``pattern`` and takes the whole match's span, which those groups
do not move.
"""

from dataclasses import dataclass
from typing import Final

#: What stands in for a value that must not leave the process.
REDACTION_SENTINEL: Final[str] = "***REDACTED***"


@dataclass(frozen=True)
class CredentialShape:
    """One credential taxonomy: what recognises it, and what replaces it."""

    vendor: str
    pattern: str
    replacement: str = REDACTION_SENTINEL


#: Every shape both surfaces know about. Covering the next vendor is one
#: entry here and no edit to either consumer.
CREDENTIAL_SHAPES: Final[tuple[CredentialShape, ...]] = (
    # github: the tokenized remote URL the forge auth adapter constructs,
    # then ghp_ classic PAT, gho_ OAuth, ghu_ user-to-server, ghs_
    # server-to-server, and github_pat_ fine-grained PAT.
    CredentialShape(
        vendor="github",
        pattern=r"(https?://x-access-token:)[^@\s/]+(@)",
        replacement=rf"\1{REDACTION_SENTINEL}\2",
    ),
    CredentialShape(vendor="github", pattern=r"\bgh[posu]_[A-Za-z0-9]{36,}"),
    CredentialShape(vendor="github", pattern=r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    # notion: ntn_ current integration and OAuth tokens, secret_ legacy
    # internal integration secrets. ``secret_`` is an ordinary English word
    # with an underscore, so the body lower bound carries the whole
    # anchoring load there: 40 alphanumerics is below both published
    # lengths and far above anything operator prose puts after that prefix.
    CredentialShape(vendor="notion", pattern=r"\b(?:ntn_|secret_)[A-Za-z0-9]{40,}"),
    # linear: lin_api_ personal API keys, lin_oauth_ OAuth access tokens.
    # The tracker credential this build dials its MCP server with is one of
    # these, and ``TrackerBackend`` has one member.
    CredentialShape(
        vendor="linear",
        pattern=r"\blin_(?:api|oauth)_[A-Za-z0-9]{40,}",
    ),
    # anthropic: sk-ant- API keys and OAuth tokens. The engine credential
    # the SDK subprocess is handed, which its stderr can echo back into an
    # ``AgentSDKError`` and from there onto the wire.
    CredentialShape(vendor="anthropic", pattern=r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
)
