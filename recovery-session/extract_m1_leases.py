"""Selective, pinned-source M1 extraction; every transplant is recorded."""
import ast
import json
import subprocess
from pathlib import Path

ROOT = Path('/private/tmp/kodezart-v03-m1-lease-extraction')
D2 = 'd2c6fceab762191d4e40b23c8cd349ef476e4b12'
CURRENT = '8fc655d2ddca93357f9fc9475b41839d62652037'
MAP = []

def donor(path, rev=D2):
    return subprocess.check_output(['git', 'show', f'{rev}:{path}'], cwd=ROOT, text=True)

def nodes(text, parent=None):
    tree = ast.parse(text)
    if parent:
        tree = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == parent)
    result = {}
    for n in tree.body:
        key = getattr(n, 'name', None)
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            key = n.target.id
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            key = n.targets[0].id
        if key:
            result[key] = n
    return result

def span(n):
    return min([n.lineno, *[x.lineno for x in getattr(n, 'decorator_list', [])]]) - 1, n.end_lineno

def code(text, n):
    start, end = span(n)
    return ''.join(text.splitlines(keepends=True)[start:end])

def put(path, text):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)

def imports(path, source):
    text = (ROOT / path).read_text()
    used = {n.id for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Name)}
    bound = set()
    for n in ast.parse(text).body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            bound.update(a.asname or (a.name.split('.')[0] if isinstance(n, ast.Import) else a.name) for a in n.names)
    additions = []
    for n in ast.parse(source).body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            aliases = [a for a in n.names if (a.asname or (a.name.split('.')[0] if isinstance(n, ast.Import) else a.name)) in used - bound]
            if aliases:
                bound.update(a.asname or (a.name.split('.')[0] if isinstance(n, ast.Import) else a.name) for a in aliases)
                n.names = aliases
                additions.append(ast.unparse(n))
    if additions:
        tree = ast.parse(text)
        at = tree.body[0].end_lineno if isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) else 0
        lines = text.splitlines(keepends=True)
        lines[at:at] = ['\n' + '\n'.join(additions) + '\n']
        put(path, ''.join(lines))

def transplant(path, names, *, parent=None, rev=D2):
    source = donor(path, rev)
    text = (ROOT / path).read_text() if (ROOT/path).exists() else '"""Extracted shared tracker rules."""\n'
    source_nodes = nodes(source, parent)
    for name in names:
        existing = nodes(text, parent)
        n = source_nodes[name]
        lines = text.splitlines(keepends=True)
        if name in existing:
            a, b = span(existing[name])
            lines[a:b] = [code(source, n)]
        elif parent:
            cls = nodes(text)[parent]
            lines[cls.end_lineno:cls.end_lineno] = ['\n' + code(source, n)]
        else:
            lines += ['\n\n' + code(source, n)]
        text = ''.join(lines)
        MAP.append({'file':path,'symbol':f'{parent+"." if parent else ""}{name}','source':rev,'lines':[n.lineno,n.end_lineno]})
    put(path, text)
    imports(path, source)

def remove(path, names, parent=None):
    text = (ROOT/path).read_text()
    for name in names:
        ns = nodes(text,parent)
        if name in ns:
            lines=text.splitlines(keepends=True); a,b=span(ns[name]); del lines[a:b];text=''.join(lines)
    put(path,text)

def whole(path, rev=D2):
    put(path, donor(path,rev)); MAP.append({'file':path,'symbol':'*','source':rev})

def replace(path, old, new, note):
    text=(ROOT/path).read_text()
    assert old in text,(path,old)
    put(path,text.replace(old,new));MAP.append({'file':path,'symbol':note,'source':'pinned donor hunk normalization','old':old,'new':new})

for path in ['core/backoff.py','core/owned_tasks.py','core/tracker_settings.py','domain/comment_markers.py','domain/self_writes.py','types/domain/self_writes.py','services/run_surface_lease.py','services/pass_gate.py']:
    whole('src/kodezart/'+path)

# Only the currently composed marker vocabulary. Work-ref landing policy belongs to M4.
p='src/kodezart/adapters/linear_markers.py';whole(p)
replace(p, "            r'(?:\\s+landing=\"(?P<landing>[^\"]*)\")?\\s*-->',", "            r'\\s*-->',", 'exclude M4 landing reader')
replace(p, 'f\'branch="{ref.branch}"{sha} landing="{ref.landing.value}" -->\'', 'f\'branch="{ref.branch}"{sha} -->\'', 'exclude M4 landing writer')

p='src/kodezart/domain/errors.py'
transplant(p,['SurfaceLeaseError','SurfaceLeaseLostError','SurfaceWriteAttributionError','DuplicateCommentMarkerError','StaleCommentWriteError'],rev=CURRENT)
p='src/kodezart/domain/tracker_writes.py'
transplant(p,['marked_comment_body','comment_under_marker','require_expected_comment'],rev=CURRENT)
p='src/kodezart/core/errors.py';transplant(p,['TrackerWriterAttributionError'])
p='src/kodezart/types/domain/tracker.py';transplant(p,['ClaimStatus','ClaimResult','TrackerComment'])
p='src/kodezart/types/domain/dispatch.py';transplant(p,['_SELF_WRITE_RECEIPT_LIMIT','SelfWriteLedger'])
p='src/kodezart/adapters/linear_mcp_types.py';transplant(p,['LinearCommentWire','LinearCommentEntryWire','LinearCommentListWire','LinearPlanningIssueWire'],rev=CURRENT)

p='src/kodezart/adapters/linear_mcp_tracker.py'
remove(p,['_CLAIM_MARKER','_ClaimMarker','_claim_marker_body','_RETRY_BACKOFF_BASE','_WORK_REF_MARKER','_BASE_SPEC_MARKER','_REPO_MARKER','_base_spec_marker','_work_ref_marker'])
remove(p,['_append_claim_marker','_unexpired_claim_markers','_wrote_by_reading'],parent='LinearMcpTracker')
grant=['_GrantKind','_GRANT_KIND_BY_VALUE','_GrantState','_GRANT_STATE_BY_VALUE','_COMMENT_PARENT_BY_SCOPE_KIND','_Target','_WrittenMarker','_GrantMarker','_Granted','_OwnGrant','_own_grants','_Conflict','_Refused','_Addressing','_surface_line','_surface_target','_CLAIM_ADDRESSING','_LEASE_ADDRESSING','_retraction','_may_resend','_TOOL_GET_USER','_CURRENT_USER_QUERY','_BODY_SAVE_ARGUMENTS','_STATE_SAVE_ARGUMENT','refuse_combined_issue_write']
transplant(p,grant,rev=CURRENT)
methods=['claim_issue','renew_claim','release_claim','active_claim','acquire_surfaces','renew_surfaces','release_surfaces','_grant','_withdraw_into','_refuse_bid','_own','_extend','_withdraw','_conflict','_grant_body','_void_body','_markers_on','_markers_from_wires','_parsed_marker','_parse_seconds','_malformed_marker','_write_marker','_edit_marker','_delete_markers','_stand_down','_comment_wires','_call','_send','_retry_call','upsert_comment','_upsert_comment','_upsert_comment_once','_require_surface_holder','_assert_surface_holder','_comment_written','_delete_own_comment','writer_identity','read_issue_movement','_movement_comments','post_comment','list_comments','_to_comment']
transplant(p,methods,parent='LinearMcpTracker',rev=CURRENT)
replace(p,'        max_retries: int,\n        retry_backoff_factor: float,','        marker_prefixes: Mapping[str, str],\n        retry: RetryPolicy,','constructor parameters')
replace(p,'        self._max_retries: int = max_retries\n        self._retry_backoff_factor: float = retry_backoff_factor','        self._markers = LinearMarkers(marker_prefixes)\n        self._retry = retry','constructor owned dependencies')
replace(p,'        _TOOL_LIST_USERS,','        _TOOL_LIST_USERS,\n        _TOOL_GET_USER,','read-only attribution tool')
for old,new in [('_work_ref_marker(ref)','self._markers.work_ref_body(ref)'),('_WORK_REF_MARKER.search','self._markers.work_ref_pattern.search'),('_base_spec_marker(spec)','self._markers.base_spec_body(spec)'),('_BASE_SPEC_MARKER.search','self._markers.base_spec_pattern.search'),('_REPO_MARKER.search','self._markers.repository_pattern.search')]:
    replace(p,old,new,'configured existing marker carrier')
replace(p,'        self._validate(LinearCommentWire, payload, _TOOL_SAVE_COMMENT)\n        await self._wrote_by_reading(ref.issue_id)','        self._comment_written(issue_key=ref.issue_id, payload=payload, created=True)','work-ref explicit comment receipt')
replace(p,'        self._validate(LinearCommentWire, payload, _TOOL_SAVE_COMMENT)\n        await self._wrote_by_reading(issue_key)','        self._comment_written(issue_key=issue_key, payload=payload, created=True)','base-spec explicit comment receipt')
imports(p,donor(p,CURRENT))

p='src/kodezart/core/protocols.py'
transplant(p,['acquire_surfaces','renew_surfaces','release_surfaces','claim_issue','renew_claim','release_claim','active_claim','writer_identity','read_issue_movement','upsert_comment'],parent='TrackerPort',rev=CURRENT)

p='src/kodezart/types/domain/operation.py'
transplant(p,['marker_prefixes'],parent='OperationConfig')
text=(ROOT/p).read_text();source=donor(p)
a=source.index('        prefixes = list(self.marker_prefixes.values())');b=source.index('        if self.documents',a)
text=text.replace('        if self.documents',source[a:b]+'        if self.documents',1)
text=text.replace('    "workflow_states": ConfigOwnership.EXTERNAL,','    "workflow_states": ConfigOwnership.EXTERNAL,\n    "marker_prefixes": ConfigOwnership.LOCAL,')
put(p,text);MAP.append({'file':p,'symbol':'marker_prefixes validation/ownership','source':D2})

p='src/kodezart/services/tracker_lifecycle.py'
transplant(p,['__init__','on_terminal_outcome'],parent='TrackerLifecycleWriter')
p='src/kodezart/composition/passes.py'
replace(p,'writer=TrackerLifecycleWriter(tracker=tracker, gate=gate),','writer=TrackerLifecycleWriter(\n            tracker=tracker, gate=gate, marker_prefixes=operation.marker_prefixes,\n            surface_lease_seconds=config.tracker.surface_lease_seconds,\n        ),','production lease injection')

Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage1.json').write_text(json.dumps(MAP,indent=2)+'\n')
