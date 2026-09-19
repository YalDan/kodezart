"""A leased alarm record write names its holder before it reads anything."""

import pytest

from kodezart.domain.errors import SurfaceLeaseError
from kodezart.domain.run_alarm_record import require_alarm_holder
from kodezart.types.domain.scope import ScopeKind
from kodezart.types.domain.surface import SurfaceKind

ISSUE = "ISSUE-7"
MARKER = "[configured-alarm:lane%2Fone:tally_unmoved]"


@pytest.mark.parametrize("holder", [None, "", " ", "\t\n"])
def test_a_blank_or_absent_holder_refuses_with_the_addressed_surface(holder):
    with pytest.raises(SurfaceLeaseError) as refusal:
        require_alarm_holder(issue_key=ISSUE, marker=MARKER, holder=holder)
    assert refusal.value.surface_kind == SurfaceKind.MARKER_COMMENT.value
    assert refusal.value.scope_kind == ScopeKind.ISSUE.value
    assert refusal.value.scope_key == ISSUE
    assert refusal.value.marker == MARKER
    assert refusal.value.current_holder is None


@pytest.mark.parametrize("holder", ["job-1", " job-1 ", "0"])
def test_a_holder_with_any_content_at_all_is_admitted(holder):
    assert require_alarm_holder(issue_key=ISSUE, marker=MARKER, holder=holder) is None
