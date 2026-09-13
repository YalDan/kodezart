from pathlib import Path
import ast,re
root=Path('/private/tmp/kodezart-v03-m1-session-policy-extraction')
paths=['tests/api/v1/test_jobs.py','tests/services/test_lifecycle_watcher.py','tests/test_composition_root.py','tests/core/test_logging_chain.py','tests/services/test_run_surface_lease.py','tests/probes/test_ab_smoke.py']
for rel in paths:
 p=root/rel;s=p.read_text();tree=ast.parse(s);lines=s.splitlines(keepends=True);offs=[0]
 for l in lines:offs.append(offs[-1]+len(l))
 edits=[]
 for n in ast.walk(tree):
  if not isinstance(n,ast.Call) or not isinstance(n.func,ast.Name) or n.func.id!='WorkflowRequest':continue
  args={k.arg:ast.get_source_segment(s,k.value) for k in n.keywords};args.setdefault('repo_path','None');args.setdefault('repo_url','None');args.setdefault('base_spec','trunk_base("main")');args.setdefault('implied_base','None');args.setdefault('permission_mode','PermissionMode.UNATTENDED');args.setdefault('allowed_tools','ToolPreset.IMPLEMENTATION');args['permission_mode']=args['permission_mode'].replace('"bypassPermissions"','PermissionMode.UNATTENDED');val='WorkflowSubmission('+', '.join(k+'='+v for k,v in args.items())+')';edits.append((offs[n.lineno-1]+n.col_offset,offs[n.end_lineno-1]+n.end_col_offset,val))
 for a,b,v in sorted(edits,reverse=True):s=s[:a]+v+s[b:]
 if rel.endswith('test_jobs.py'):s=s.replace('def _request(prompt: str) -> WorkflowRequest:','def _request(prompt: str) -> WorkflowSubmission:')
 if rel.endswith('test_ab_smoke.py'):s=s.replace('base_spec=trunk_base(request.base_branch)','base_spec=request.base_spec')
 if edits:
  i=s.index('from kodezart.');s=s[:i]+'from kodezart.types.domain.workflow import WorkflowSubmission\nfrom kodezart.types.domain.branch import trunk_base\nfrom kodezart.types.domain.session import PermissionMode, ToolPreset\n'+s[i:];p.write_text(s)
p=root/'tests/integration/test_live_criteria_probes.py';s=p.read_text().replace('from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS_WITH_AGENT','from kodezart.core.constants import EVAL_PERMISSION_MODE\nfrom kodezart.types.domain.session import ToolPreset').replace('allowed_tools=EVAL_TOOLS_WITH_AGENT','allowed_tools=ToolPreset.DELEGATED_EVALUATION');p.write_text(s)
p=root/'tests/chains/test_ticket_generation.py';s=p.read_text().replace('from kodezart.types.domain.session import SessionType','from kodezart.types.domain.session import SessionType, ToolPreset');s=s.replace('''        allowed = call["allowed_tools"]
        assert isinstance(allowed, list)
        assert "WebSearch" in allowed
        assert "WebFetch" in allowed''','''        assert call["allowed_tools"] is ToolPreset.AUTHORING''');p.write_text(s)
