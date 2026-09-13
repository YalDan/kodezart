from pathlib import Path
exec(Path('/private/tmp/kodezart-recovery-session/extract_m1_leases.py').read_text().split('\nfor path in ')[0])
MAP = json.loads(Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage3.json').read_text())

p='tests/tracker/test_linear_mcp_tracker.py'
remove(p,['claim_markers','holder_of','TestClaimMechanism','TestClaimMarkerVolume'])
replace(p,'from kodezart.adapters.linear_mcp_tracker import _CLAIM_MARKER, LinearMcpTracker','from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker','retired claim tests replaced by accepted arbitration corpus')
p='tests/tracker/test_tracker_conformance.py'
transplant(p,['CLAIMED_REF','APPROVED_REF','TestSubstitutability'])
text=(ROOT/p).read_text();pieces=[]
for name in ['CLAIMED_REF','APPROVED_REF']:
    pieces.append(code(text,nodes(text)[name]));remove(p,[name]);text=(ROOT/p).read_text()
pos=span(nodes(text)['CLAIMED_DESCRIPTION'])[0];lines=text.splitlines(keepends=True);lines[pos:pos]=['\n'.join(pieces)+'\n'];put(p,''.join(lines))
p='tests/tracker/conftest.py';transplant(p,['tracker_writes'])
text=(ROOT/p).read_text()
for line in ['            *tracker.issue_writes,\n','            *tracker.issue_creations,\n','            *tracker.classification_writes,\n']:text=text.replace(line,'')
put(p,text)

p='tests/services/test_run_surface_lease.py'
replace(p,'from kodezart.types.requests.workflow import WorkflowRequest','from kodezart.types.requests.agent import WorkflowRequest','existing request module')
replace(p,'from kodezart.types.domain.session import PermissionMode\n','','no later neutral permission model')
replace(p,'permission_mode=PermissionMode.INTERACTIVE','permission_mode="bypassPermissions"','existing request permission representation')

# Mechanical fixture constructor changes; leave every test assertion in place.
for path in (ROOT/'tests').rglob('*.py'):
    rel=str(path.relative_to(ROOT));text=path.read_text();tree=ast.parse(text);edits=[]
    lines=text.splitlines(keepends=True);starts=[0]
    for line in lines:starts.append(starts[-1]+len(line))
    def offset(line,col):return starts[line-1]+col
    need_retry=False;need_markers=False
    for call in ast.walk(tree):
        if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Name):continue
        keys={k.arg for k in call.keywords}
        if call.func.id=='TrackerLifecycleWriter' and 'marker_prefixes' not in keys:
            pos=offset(call.func.end_lineno,call.func.end_col_offset)+1
            edits.append((pos,pos,'marker_prefixes={"run_outcome": "fixture-outcome"}, surface_lease_seconds=321.5, '))
        if call.func.id=='LinearMcpTracker' and not any(k.arg is None for k in call.keywords):
            if 'marker_prefixes' not in keys:
                pos=offset(call.func.end_lineno,call.func.end_col_offset)+1;edits.append((pos,pos,'marker_prefixes=MARKER_PREFIXES, '));need_markers=True
            kw={k.arg:k for k in call.keywords}
            if 'max_retries' in kw:
                a,b=kw['max_retries'],kw['retry_backoff_factor'];need_retry=True
                value=ast.get_source_segment(text,a.value);delay=ast.get_source_segment(text,b.value)
                edits.append((offset(a.lineno,a.col_offset),offset(a.end_lineno,a.end_col_offset),f'retry=RetryPolicy(attempts=({value}) + 1, initial_delay={delay})'))
                start=offset(b.lineno,b.col_offset);end=offset(b.end_lineno,b.end_col_offset)
                if text[end:end+1]==',':end+=1
                edits.append((start,end,''))
        if call.func.id=='AppConfig':
            migrate={'tracker_token':'token','tracker_mcp_server_url':'server_url','tracker_mcp_sse_read_timeout_seconds':'sse_read_timeout_seconds'}
            kw=[k for k in call.keywords if k.arg in migrate]
            if kw:
                parts=[repr(migrate[k.arg])+': '+ast.get_source_segment(text,k.value) for k in kw]
                for i,k in enumerate(kw):
                    a=offset(k.lineno,k.col_offset);b=offset(k.end_lineno,k.end_col_offset)
                    if i==0:edits.append((a,b,'tracker={'+', '.join(parts)+'}'))
                    else:
                        if text[b:b+1]==',':b+=1
                        edits.append((a,b,''))
    for a,b,v in sorted(edits,reverse=True):text=text[:a]+v+text[b:]
    if need_markers:text='from tests.tracker.marker_config import MARKER_PREFIXES\n'+text
    if need_retry:text='from kodezart.core.backoff import RetryPolicy\n'+text
    if text!=path.read_text():put(rel,text);MAP.append({'file':rel,'symbol':'fixture constructor calls','source':'accepted constructor migration; assertions unchanged'})

Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage4.json').write_text(json.dumps(MAP,indent=2)+'\n')
