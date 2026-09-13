from pathlib import Path
import ast,re,subprocess,json
root=Path('/private/tmp/kodezart-v03-m1-session-policy-extraction');donor='1f4296c8c36a7498625d2478f8ec7ae6ec187923';changes=[]
def source(p):return subprocess.check_output(['git','show',f'{donor}:{p}'],cwd=root,text=True)
def imp(s,names):
 line='from kodezart.types.domain.session import '+', '.join(names)+'\n';i=s.index('from kodezart.') if 'from kodezart.' in s else s.index('from tests.') if 'from tests.' in s else len(s.split('"""',2)[0]);return s[:i]+line+s[i:]
for p in (root/'tests').rglob('*.py'):
 s=p.read_text();tree=ast.parse(s);lines=s.splitlines(keepends=True);offsets=[0]
 for l in lines:offsets.append(offsets[-1]+len(l))
 replacements=[];modes={'plan':'PLAN','default':'INTERACTIVE','acceptEdits':'ACCEPT_EDITS','bypassPermissions':'UNATTENDED'}
 for n in ast.walk(tree):
  targets=[]
  if isinstance(n,ast.Call) and not (isinstance(n.func,ast.Name) and n.func.id in {'WorkflowRequest','QueryRequest','ClaudeAgentOptions'}):targets=[k.value for k in n.keywords if k.arg=='permission_mode']
  if isinstance(n,ast.Dict):targets=[v for k,v in zip(n.keys,n.values) if isinstance(k,ast.Constant) and k.value=='permission_mode']
  for v in targets:
   if isinstance(v,ast.Constant) and v.value in modes:replacements.append((offsets[v.lineno-1]+v.col_offset,offsets[v.end_lineno-1]+v.end_col_offset,'PermissionMode.'+modes[v.value]))
 for a,b,v in sorted(set(replacements),reverse=True):s=s[:a]+v+s[b:]
 if replacements:s=imp(s,['PermissionMode'])
 if s!=p.read_text():p.write_text(s);changes.append({'path':str(p.relative_to(root)),'adaptation':'Only application permission kwargs/config fixture literals to exact donor enum members; HTTP request and SDK option constructors excluded'})
p=root/'tests/fakes.py';s=p.read_text();s=s.replace('permission_mode: str','permission_mode: PermissionMode').replace('allowed_tools: list[str]','allowed_tools: AllowedTools').replace('permission_mode: PermissionMode = "default"','permission_mode: PermissionMode = PermissionMode.INTERACTIVE').replace('permission_mode: PermissionMode = "plan"','permission_mode: PermissionMode = PermissionMode.PLAN');s=imp(s,['AllowedTools']);s=s.replace('from kodezart.types.requests.agent import WorkflowRequest','from kodezart.types.domain.workflow import WorkflowSubmission').replace('WorkflowRequest','WorkflowSubmission');p.write_text(s)
# Exact donor tests, adapted only to currently present HTTP composition and domain fields.
for name in ['test_permission_boundary.py','test_tool_selection.py']:
 rel='tests/adapters/'+name;s=source(rel).replace('from kodezart.api import dependencies\n','').replace('from kodezart.core.job_queue_settings import JobQueueSettings','from kodezart.core.config import AppConfig');s=re.sub(r'    app.dependency_overrides.update\(\n        \{.*?\n        \}\n    \)','    app.state.agent_service = service\n    app.state.skills = SUPPRESS_ALL_SKILLS\n    app.state.job_queue = queue\n    app.state.config = AppConfig()',s,flags=re.S)
 if 'from kodezart.core.config import AppConfig' not in s:s=s.replace('from kodezart.main import create_app','from kodezart.core.config import AppConfig\nfrom kodezart.main import create_app')
 s=s.replace('fields.update(implied_base=None, scope=None)','fields.update(implied_base=None)').replace('{"implied_base": None, "scope": None}','{"implied_base": None}').replace('settings=JobQueueSettings()','config=AppConfig()');(root/rel).write_text(s);changes.append({'path':rel,'donor':donor,'adaptation':'Preserved all test assertions; app.state fixture instead of later Depends; current AppConfig queue binding; remove absent M3 scope fixture only'})
# Exact donor harness permission/tool migration, retain baseline flat config behavior.
p='tests/probes/test_harness_capabilities.py';s=source(p).replace('config.agent.skills','config.skills_selection()').replace('config.agent.setting_sources','config.setting_sources');(root/p).write_text(s);changes.append({'path':p,'donor':donor,'adaptation':'Exact donor harness; only two existing flat configuration consumers retained'})
# Existing native-list fixture is from exact baseline request schema, not newer native scope fields.
Path('/private/tmp/kodezart-recovery-session/session-policy-test-migrations.json').write_text(json.dumps(changes,indent=2)+'\n')
