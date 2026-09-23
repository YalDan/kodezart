"""The recorded per-case call log covers the adapter arm exactly (KOD-837).

The golden in ``conformance_call_log.json`` is compared case by case by an
autouse fixture of the tracker ``conftest``, so a golden that lost a case,
or carried one the suite no longer has, would compare less than it says.
This holds its keys to the conformance cases collected on the adapter arm,
and holds the digest to what it is meant to see: the values a call sends,
with only what varies between runs erased.
"""

import subprocess
import sys

from tests.tracker.conformance_call_log import (
    CONFORMANCE_MODULE,
    REPOSITORY,
    call_log_digest,
    logged_call,
    recorded_case,
    recorded_digests,
)
from tests.tracker.conftest import TRACKER_ADAPTERS


def collected_conformance_cases() -> frozenset[str]:
    """Every node id the conformance module collects, read off a collection run."""
    listing = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            CONFORMANCE_MODULE,
        ],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return frozenset(line for line in listing.splitlines() if "::" in line)


def test_the_golden_names_exactly_the_conformance_cases_on_the_adapter_arm():
    collected = collected_conformance_cases()
    on_the_arm = {
        nodeid for nodeid in collected if recorded_case(nodeid, arms=TRACKER_ADAPTERS)
    }

    assert on_the_arm
    assert on_the_arm < collected
    assert set(recorded_digests()) == on_the_arm


def test_the_digest_sees_a_value_and_not_what_varies_between_runs():
    sent = logged_call(
        "save_comment", {"issueId": "FIX-1", "body": "nonce: " + "a" * 32}
    )
    again = logged_call(
        "save_comment", {"issueId": "FIX-1", "body": "nonce: " + "b" * 32}
    )
    other = logged_call("save_comment", {"issueId": "FIX-1", "body": "nonce: x"})
    stamped = logged_call("save_issue", {"at": "2026-03-01T12:00:00+00:00"})
    restamped = logged_call("save_issue", {"at": "2026-03-02T09:30:00.5Z"})

    assert call_log_digest([sent]) == call_log_digest([again])
    assert call_log_digest([sent]) != call_log_digest([other])
    assert call_log_digest([stamped]) == call_log_digest([restamped])
    assert call_log_digest([sent, other]) != call_log_digest([other, sent])
