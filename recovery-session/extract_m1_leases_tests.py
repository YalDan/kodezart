from pathlib import Path
exec(Path('/private/tmp/kodezart-recovery-session/extract_m1_leases.py').read_text().split('\nfor path in ')[0])
MAP = json.loads(Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage2.json').read_text())

p='src/kodezart/adapters/linear_mcp_tracker.py';remove(p,['_TOOL_GET_USER']);replace(p,'_TOOL_LIST_USERS = "list_users"','_TOOL_LIST_USERS = "list_users"\n_TOOL_GET_USER = "get_user"','constant dependency order')

for path in ['tests/tracker/marker_config.py','tests/tracker/lease_fixtures.py','tests/tracker/test_ownership_arbitration.py','tests/tracker/test_marker_configuration.py','tests/core/test_surface_lease_config.py','tests/core/test_tracker_settings.py','tests/domain/test_comment_markers.py','tests/domain/test_surface_lease_error.py','tests/domain/test_self_write_retention.py','tests/services/test_run_surface_lease.py','tests/adapters/test_self_write_replay.py','tests/adapters/test_tracker_self_writes.py']:
    whole(path)
for path in ['tests/tracker/test_comment_expected.py','tests/tracker/test_comment_expected_independent.py','tests/tracker/test_protected_comment_retry.py']:
    whole(path,CURRENT)
# Only the fixture-source helper; other retired subsystem assertions belong with their settings.
transplant('tests/core/test_retired_config.py',['_from_source'])

p='tests/tracker/conftest.py'
transplant(p,['FixtureClock','_frozen_now','AGENT_IDENTITY','fixture_server','linear_over_fake_mcp','_snapshot','fake_port_over_fixture','TRACKER_ADAPTERS','TRACKER_DOUBLES','TRACKER_IMPLEMENTATIONS','server','clock','tracker','adapter'])
text=(ROOT/p).read_text()
# The whole fixture is shared by M1's existing workspace, but native-fire classifications are later consumers.
a=text.index('        issue_labels={');b=text.index('        caller=server,',a);text=text[:a]+text[b:]
text=text.replace('    scope_labels: Mapping[str, str] | None = None,\n','')
text=text.replace('        marker_prefixes=MARKER_PREFIXES,\n        assets=', '        assets=')
a=text.index('    port.issue_state_changes = {');b=text.index('    # A credential refused',a);text=text[:a]+text[b:]
a=text.index('    port.criteria_stage_label_key =');b=text.index('    return port',a);text=text[:a]+text[b:]
put(p,text)
# Donor module dependencies must precede the functions that evaluate their defaults/decorators.
text=(ROOT/p).read_text(); names=['FixtureClock','_frozen_now','AGENT_IDENTITY','TRACKER_ADAPTERS','TRACKER_DOUBLES','TRACKER_IMPLEMENTATIONS']
for name in names[:3]:
    n=nodes(text)[name];chunk=code(text,n);remove(p,[name]);text=(ROOT/p).read_text();pos=span(nodes(text)['fixture_server'])[0];lines=text.splitlines(keepends=True);lines[pos:pos]=[chunk+'\n\n'];put(p,''.join(lines));text=(ROOT/p).read_text()
imports(p,donor(p))

p='tests/tracker/test_linear_mcp_tracker.py';transplant(p,['tracker_over'])
text=(ROOT/p).read_text();a=text.index('        "issue_labels": {');b=text.index('        "queue_state_labels":',a);text=text[:a]+text[b:];put(p,text)
p='tests/tracker/test_tracker_conformance.py'
transplant(p,['TestAtomicClaim','TestRenewingAClaim','TestSurfaceLease','TestWriterIdentity'])
# Imported top-level fixture vocabulary required by the lease conformance classes.
source=donor(p); used={n.id for n in ast.walk(ast.parse((ROOT/p).read_text())) if isinstance(n,ast.Name)}
for name,node in nodes(source).items():
    if name in used and name not in nodes((ROOT/p).read_text()) and not isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
        transplant(p,[name])

# Constructor fixture migration only, keeping actual pre-existing queue API and request model.
p='tests/services/test_run_surface_lease.py'
replace(p,'build_job_queue(settings=config.queue,','build_job_queue(config=config,','current M1 queue constructor')
replace(p,'from kodezart.types.domain.workflow import WorkflowSubmission','from kodezart.types.requests.workflow import WorkflowRequest','existing M1 request type')
replace(p,'WorkflowSubmission(','WorkflowRequest(','existing M1 request construction')
for old in ['                implied_base=None,\n','                scope=None,\n']:
    replace(p,old,'','exclude later native/scope submission fields')
for p in ['tests/adapters/test_tracker_self_writes.py']:
    for line in ['        issue_labels={"criterion": "acceptance-condition"},\n','        scope_labels={},\n','        criteria_stage_label_key=None,\n']:
        replace(p,line,'','exclude later adapter constructor arguments')

# Existing production-composition fixtures acquire the same required operation prefix.
for p in ['tests/services/test_dispatch_pass.py']:
    text=(ROOT/p).read_text(); n=nodes(text)['operation_config']; part=code(text,n);part=part.replace('OperationConfig(', 'OperationConfig(marker_prefixes={"run_outcome": "fixture-outcome"},',1)
    lines=text.splitlines(keepends=True);a,b=span(n);lines[a:b]=[part];put(p,''.join(lines));MAP.append({'file':p,'symbol':'operation_config.marker_prefixes','source':D2})

Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage3.json').write_text(json.dumps(MAP,indent=2)+'\n')
