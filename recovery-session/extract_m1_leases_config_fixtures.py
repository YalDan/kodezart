from pathlib import Path
exec(Path('/private/tmp/kodezart-recovery-session/extract_m1_leases.py').read_text().split('\nfor path in ')[0])
MAP = json.loads(Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage4.json').read_text())
p='tests/services/test_run_surface_lease.py';replace(p,'                issue_key=CLAIMED_ISSUE,\n','', 'current queue request has no native issue key; watcher receives it explicitly')
whole('tests/core/test_tracker_credential.py')
names={'TRACKER':'TRACKER__BACKEND','TRACKER_MCP_SERVER_NAME':'TRACKER__SERVER_NAME','TRACKER_MCP_SERVER_URL':'TRACKER__SERVER_URL','TRACKER_MCP_AUTH_HEADER':'TRACKER__AUTH_HEADER','TRACKER_MCP_AUTH_SCHEME':'TRACKER__AUTH_SCHEME','TRACKER_TOKEN':'TRACKER__TOKEN','TRACKER_TIMEOUT_SECONDS':'TRACKER__TIMEOUT_SECONDS','TRACKER_MCP_CALL_TIMEOUT_SECONDS':'TRACKER__CALL_TIMEOUT_SECONDS','TRACKER_MCP_SSE_READ_TIMEOUT_SECONDS':'TRACKER__SSE_READ_TIMEOUT_SECONDS','TRACKER_MCP_ERROR_DETAIL_LIMIT':'TRACKER__ERROR_DETAIL_LIMIT','TRACKER_MAX_RETRIES':'TRACKER__MAX_RETRIES','TRACKER_RETRY_BACKOFF_FACTOR':'TRACKER__RETRY_BACKOFF_FACTOR'}
import re
for path in [ROOT/'.env.example',ROOT/'README.md',*(ROOT/'docs').glob('*.md'),*(ROOT/'tests').rglob('*.py')]:
    rel=str(path.relative_to(ROOT))
    if rel in ['tests/core/test_tracker_settings.py','tests/core/test_retired_config.py']:continue
    text=path.read_text();new=re.sub(r'KODEZART_[A-Z0-9_]+',lambda m:'KODEZART_'+names.get(m[0][9:],m[0][9:]),text)
    if new!=text:put(rel,new);MAP.append({'file':rel,'symbol':'tracker environment paths','source':D2})
# Same actual factory interface, with tests continuing to replace only external transport.
for p in ['tests/tracker/test_tracker_boot.py','tests/tracker/test_tracker_boot_wiring.py']:
    text=(ROOT/p).read_text().replace('def factory(*, config: AppConfig, token:', 'def factory(*, settings: TrackerSettings, token:')
    text=text.replace('boot_tracker(\n                config=config,','boot_tracker(\n                settings=config.tracker,').replace('boot_tracker(\n            config=config,','boot_tracker(\n            settings=config.tracker,')
    put(p,text);imports(p,donor(p))
p='tests/tracker/test_tracker_boot.py'
text=(ROOT/p).read_text();node=nodes(text)['operation_config'];part=code(text,node).replace('OperationConfig(', 'OperationConfig(marker_prefixes=MARKER_PREFIXES, agent_identities=[AGENT_IDENTITY],',1)
lines=text.splitlines(keepends=True);a,b=span(node);lines[a:b]=[part];put(p,''.join(lines));transplant(p,['_managed_fixture_server'])
p='tests/tracker/test_tracker_boot_wiring.py'
replace(p,'agent_identities = []','agent_identities = ["{AGENT_IDENTITY}"]','declared native writer fixture')
replace(p,'workspace = "fixture-workspace"','workspace = "fixture-workspace"\nmarker_prefixes = {{run_outcome = "fixture-outcome", claim = "kodezart-claim", work_ref = "kodezart-workref", base_spec = "kodezart-basespec", repository = "kodezart-repo"}}','required operation marker fixture')
# The managed external double is the same native account the operation declares.
replace(p,'source = fixture_server()','source = fixture_server(actor=AGENT_IDENTITY)','native boot actor fixture')
imports(p,donor(p))
p='tests/test_composition_root.py';replace(p,'make_mcp_tool_caller(config=self._config(),','make_mcp_tool_caller(settings=self._config().tracker,','tracker factory consumer')

# Preserve both original stream-bound arms while moving only the tracker address.
p='tests/core/test_config.py';text=(ROOT/p).read_text().replace('"tracker_mcp_sse_read_timeout_seconds",','"tracker__sse_read_timeout_seconds",')
text=text.replace('getattr(AppConfig(), field)','getattr(AppConfig().tracker, field.split("__", 1)[1]) if "__" in field else getattr(AppConfig(), field)')
# Parenthesize the existing equality's left expression after the mechanical selector.
text=text.replace('assert getattr(AppConfig().tracker, field.split("__", 1)[1]) if "__" in field else getattr(AppConfig(), field) ==','assert (getattr(AppConfig().tracker, field.split("__", 1)[1]) if "__" in field else getattr(AppConfig(), field)) ==')
text=text.replace('assert field in str(excinfo.value)', 'assert field.split("__")[-1] in str(excinfo.value)')
put(p,text)

# Existing generic recursive census supports the newly nested tracker model.
transplant('tests/docs/configuration.py',['model_types','shipped_config_variables'])
p='tests/core/test_credential_shapes.py'
transplant(p,['_credential_fields','test_every_token_field_maps_into_the_table_or_names_its_exemption','test_every_shapeless_token_field_is_a_secret_that_never_serializes'])
replace(p,'"tracker_token": _TRACKER_TOKEN','"TrackerSettings.token": _TRACKER_TOKEN','nested credential census identity')

p='tests/docs/test_documented_surface.py'
remove(p,['_shipped_config_variables','_env_example_assignments'])
transplant(p,['test_env_example_values_load','test_every_env_example_value_is_the_fields_shipped_default'])
text=(ROOT/p).read_text();text=text.replace('from kodezart.core.config import AppConfig','from tests.docs.configuration import shipped_config_variables as _shipped_config_variables\nfrom kodezart.core.config import AppConfig')
text=text.replace('_attribute_reads_of("tracker_mcp_server_name")','_attribute_reads_of("server_name")')
put(p,text)

# Shipped example and reference describe every actual tracker setting, no new consumer prefix.
p='docs/operation.example.toml';text=(ROOT/p).read_text();position=text.index('[workflow_states]');marker='''# Stable comment identity prefixes; preserve these when adopting existing records.
[marker_prefixes]
claim = "kodezart-claim"
work_ref = "kodezart-workref"
base_spec = "kodezart-basespec"
repository = "kodezart-repo"
run_outcome = "run-outcome"

''';put(p,text[:position]+marker+text[position:])
p='.env.example';replace(p,'KODEZART_TRACKER__RETRY_BACKOFF_FACTOR=1.0','KODEZART_TRACKER__RETRY_BACKOFF_FACTOR=1.0\nKODEZART_TRACKER__SURFACE_LEASE_SECONDS=900','configured surface lease default')
p='docs/configuration.md';text=(ROOT/p).read_text();text+='''
### Tracker settings group

`KODEZART_TRACKER` accepts the tracker settings object. Its fields also load from
the `KODEZART_TRACKER__` nested environment prefix. The earlier flat tracker
connection settings are rejected; migrate each to the corresponding field below.
The tracker credential is excluded from configuration serialization.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KODEZART_TRACKER__SURFACE_LEASE_SECONDS` | `900` | Explicitly renewed write-surface lease duration, from 60 through 86400 seconds. |

Terminal outcome comments require `marker_prefixes.run_outcome` in the operation
configuration. The writer validates this purpose at construction, acquires the
marker surface under the actual queue job id, renews after content gating, and
releases after settlement. Fire-claim renewal remains separately configured.
''';put(p,text)
Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage5.json').write_text(json.dumps(MAP,indent=2)+'\n')
