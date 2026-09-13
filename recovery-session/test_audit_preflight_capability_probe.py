"""Configured Audit must reject missing actual native classification capability."""
import pytest

from kodezart.composition.audit import verify_audit_configuration
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from tests.integration.test_audit_scheduler import dependencies
from tests.tracker.test_linear_mcp_tracker import tracker_over


@pytest.mark.parametrize('missing', ['operation_criterion', 'tracker_criterion', 'tracker_decision'])
def test_audit_preflight_checks_declared_and_actual_classification_reads(missing):
    config, operation, server, _, forge = dependencies()
    fields = operation.model_dump()
    fields['issue_labels']['criterion'] = 'acceptance-condition'
    if missing == 'operation_criterion':
        fields['issue_labels'].pop('criterion')
    operation = OperationConfig.model_validate(fields)
    labels = dict(operation.issue_labels)
    if missing.startswith('tracker_'):
        labels.pop(missing.removeprefix('tracker_'))
    tracker = tracker_over(server, issue_labels=labels, marker_prefixes=operation.marker_prefixes)
    with pytest.raises(OperationMemberAbsentError, match='issue_labels'):
        verify_audit_configuration(config=config, operation=operation, tracker=tracker, forge=forge)
