import sys,ast,json
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
from extract_m2 import *
for p in ['domain/criterion_amendment.py','core/write_back_settings.py']:
 whole('src/kodezart/'+p)
for p,names in {'types/domain/surface.py':['DescriptionWriteAuthority'],'adapters/linear_mcp_types.py':['LinearWorkflowStateWire','LINEAR_WORKFLOW_STATES']}.items():
 for name in names:symbol('src/kodezart/'+p,name)
symbol('src/kodezart/core/config.py','write_back','AppConfig')
for name in ['TRACKER_TITLE','TRACKER_DESCRIPTION']:
 symbol('src/kodezart/types/domain/gating.py',name,'OutboundDestination')
p='src/kodezart/adapters/linear_mcp_tracker.py';text=(T/p).read_text().replace('        issue_labels: Mapping[str, str],','        issue_labels: Mapping[str, str],\n        scope_labels: Mapping[str, str] | None = None,').replace('        self._issue_labels = dict(issue_labels)','        self._issue_labels = dict(issue_labels)\n        self._scope_labels = dict(scope_labels or {})\n        self._issue_identity = LinearIssueIdentityCarrier(marker_prefixes)');(T/p).write_text(text)
p='src/kodezart/composition/organize.py';text=(T/p).read_text().replace('remote=config.git.remote','remote=config.git_remote');(T/p).write_text(text)
p='src/kodezart/adapters/_mcp_mapping.py';text=(T/p).read_text().replace('            | SessionType.SCHEDULED_PASS','            | SessionType.SCHEDULED_PASS\n            | SessionType.ORGANIZE_PASS');(T/p).write_text(text)
# Native scope reader milestone listing and its minimal wire/helper closure.
symbol('src/kodezart/adapters/linear_scope_reader.py','project_milestones','LinearScopeReader')
f=pathlib.Path('/private/tmp/kodezart-recovery-session/m2-extracted-hunks.json');f.write_text(json.dumps(json.loads(f.read_text())+records,indent=2)+'\n')
