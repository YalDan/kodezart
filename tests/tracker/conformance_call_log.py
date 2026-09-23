"""The Linear arm's MCP call log per conformance case, recorded before the split.

A non-test module the tracker ``conftest`` and its companion case share, so
what a case's call log is, and how it is reduced to the digest the golden
holds, is stated once. A case's log is every ``call_tool`` the fixture
workspace answered while the case ran, in order, as the tool name and its
arguments with their values. Only what varies from run to run is erased
before the digest is taken: nonces, uuids and timestamps. Everything else a
value carries, a limit, a flag, a body, a label, is in the digest.

The golden, ``conformance_call_log.json``, maps each conformance node id on
the adapter arm to the digest of its log. It was recorded over the adapter
as it stood before it was split into one class per role, so a case whose
log moves across the split reddens by its own node id.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from functools import cache
from pathlib import Path

#: The conformance module, by the node id prefix its cases carry.
CONFORMANCE_PATH = Path(__file__).with_name("test_tracker_conformance.py")
REPOSITORY = Path(__file__).parents[2]
CONFORMANCE_MODULE = CONFORMANCE_PATH.relative_to(REPOSITORY).as_posix()

#: The committed golden: node id to the digest of that case's call log.
GOLDEN_PATH = Path(__file__).with_name("conformance_call_log.json")

#: What varies from one run to the next, and what each is replaced with.
#: A nonce is a bare 32-hex token; a uuid is dashed; a timestamp is ISO.
VARYING: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
        "<uuid>",
    ),
    (re.compile(r"\b[0-9a-f]{32}\b"), "<nonce>"),
    (
        re.compile(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
        ),
        "<time>",
    ),
)


def recorded_case(nodeid: str, *, arms: Iterable[str]) -> bool:
    """Whether *nodeid* is a conformance case run over one of the adapter *arms*."""
    module, _, rest = nodeid.partition("::")
    parameters = rest.partition("[")[2]
    return module == CONFORMANCE_MODULE and any(arm in parameters for arm in arms)


def logged_call(name: str, arguments: Mapping[str, object]) -> str:
    """One call as the log holds it: tool and arguments, what varies erased.

    Taken at the moment of the call, so an argument mutated after it is
    sent does not change what was sent.
    """
    text = json.dumps([name, arguments], sort_keys=True, default=str)
    for pattern, replacement in VARYING:
        text = pattern.sub(replacement, text)
    return text


def call_log_digest(calls: Iterable[str]) -> str:
    """The digest of an ordered call log."""
    return hashlib.sha256("\n".join(calls).encode()).hexdigest()


@cache
def recorded_digests() -> dict[str, str]:
    """The golden, as committed."""
    loaded: dict[str, str] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return loaded
