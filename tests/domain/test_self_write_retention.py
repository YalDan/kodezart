"""Bounded receipt bodies never turn a missing prefix into own-write proof."""

import gc
import weakref

from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.self_writes import OwnMutation, field_values


def test_receipt_window_releases_old_bodies_across_issues() -> None:
    ledger = SelfWriteLedger()
    first = OwnMutation(fields=field_values({"body": "retained body" * 1000}))
    held = weakref.ref(first)
    ledger.record_mutation(issue_key="FIRST", mutation=first)
    del first
    for index in range(255):
        ledger.record_mutation(issue_key=f"OTHER-{index}", mutation=OwnMutation())
    assert held() is not None
    assert ledger.receipts(issue_key="FIRST", after=0)[1] is not None

    ledger.record_mutation(issue_key="LAST", mutation=OwnMutation())
    gc.collect()
    assert held() is None
    assert ledger.receipts(issue_key="FIRST", after=0) == (1, None)
    assert ledger.receipts(issue_key="FIRST", after=1) == (1, ())
    assert ledger.receipts(issue_key="UNKNOWN", after=0) == (0, ())


def test_only_the_issue_whose_prefix_expired_loses_replay() -> None:
    ledger = SelfWriteLedger()
    for _index in range(300):
        ledger.record_mutation(issue_key="BUSY", mutation=OwnMutation())
    cursor, old = ledger.receipts(issue_key="BUSY", after=0)
    assert cursor == 300
    assert old is None
    _, retained = ledger.receipts(issue_key="BUSY", after=44)
    assert retained is not None
    assert len(retained) == 256
    ledger.record_mutation(issue_key="QUIET", mutation=OwnMutation())
    assert ledger.receipts(issue_key="QUIET", after=0) == (1, (OwnMutation(),))
    assert ledger.receipts(issue_key="BUSY", after=300) == (300, ())
    assert ledger.receipts(issue_key="QUIET", after=2) == (1, None)
