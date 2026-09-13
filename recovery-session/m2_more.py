import sys,ast,json
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
from extract_m2 import *
for p in ['types/domain/tracker_writes.py','domain/scope_approval.py']:
 whole('src/kodezart/'+p)
for p,names in {
'domain/tracker_writes.py':['description_replacement'],
'domain/errors.py':['StaleWriteError'],
'adapters/linear_scope_types.py':['LinearApprovalIssueWire'],
}.items():
 for name in names:symbol('src/kodezart/'+p,name)
for name in ['_scope_label_members','_read_scope_issue']:
 symbol('src/kodezart/adapters/linear_mcp_tracker.py',name,'LinearMcpTracker')
# Pull only native graph/split readback additions; the six existing M4 arms match donor.
symbol('src/kodezart/services/tracker_artifacts.py','_SUPPORTED')
symbol('src/kodezart/services/tracker_artifacts.py','read_tracker_artifact')
# The owner validator's M2-only statements are preserved byte-for-byte.
p='src/kodezart/types/domain/operation.py';src=read(p);n=node(src,'_check_structure','OperationConfig');chosen=[]
for s in n.body:
 if isinstance(s,ast.Try) and 'resolve_organize_mandates' in ast.unparse(s):chosen.append(s)
 elif isinstance(s,ast.If) and ast.unparse(s.test) in ['self.organize_scopes','self.scope_labels']:chosen.append(s)
text=(T/p).read_text();owner=node(text,'_check_structure','OperationConfig');lines=text.splitlines(keepends=True);pos=next(x.end_lineno for x in owner.body if isinstance(x,ast.AnnAssign) and key(x)=='failures');chunk='\n'+''.join(''.join(src.splitlines(keepends=True)[start(s):s.end_lineno])+'\n' for s in chosen);lines.insert(pos,chunk);(T/p).write_text(''.join(lines))
# Raw marker graph fields and approval labels are classified at the adapter constructor.
p='src/kodezart/adapters/linear_mcp_tracker.py';text=(T/p).read_text();text=text.replace('        issue_labels: Mapping[str, str] | None = None,','        issue_labels: Mapping[str, str] | None = None,\n        scope_labels: Mapping[str, str] | None = None,');text=text.replace('        self._issue_labels = dict(issue_labels or {})','        self._issue_labels = dict(issue_labels or {})\n        self._scope_labels = dict(scope_labels or {})\n        self._issue_identity = LinearIssueIdentityCarrier(marker_prefixes or {})');(T/p).write_text(text)
# Import the constructor-only carrier.
text=(T/p).read_text();text=text.replace('from kodezart.adapters.linear_markers import (','from kodezart.adapters.linear_issue_identity import LinearIssueIdentityCarrier\nfrom kodezart.adapters.linear_markers import (');(T/p).write_text(text)
# Keep only four new closed raise-site vocabulary members.
p='src/kodezart/types/domain/agent.py';text=(T/p).read_text();text=text.replace('RaiseSite = Literal[','RaiseSite = Literal[\n    "organize_assess",\n    "organize_verify",\n    "organize_author",\n    "organize_criteria_author",');(T/p).write_text(text)
# Append evidence map rather than discard preceding operations.
f=pathlib.Path('/private/tmp/kodezart-recovery-session/m2-extracted-hunks.json');f.write_text(json.dumps(json.loads(f.read_text())+records,indent=2)+'\n')
