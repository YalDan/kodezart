from pathlib import Path
import ast,json
D=Path('/private/tmp/kodezart-v03-recovery-integration');T=Path('/private/tmp/kodezart-v03-m3-plan-walk');O=Path('/private/tmp/kodezart-recovery-session')
seed='''chains/authored_checks chains/authored_delivery chains/authored_publication chains/criteria chains/fire_consolidation chains/fire_implementation chains/fire_remediation chains/fire_review chains/fire_specification chains/native_amendment chains/native_execution chains/ralph_loop chains/ralph_workflow chains/remediation chains/ticket_generation chains/scope_walker domain/accept_gate domain/amendment domain/authored_outcome domain/criteria domain/criteria_feasibility domain/criteria_grading domain/criterion_evidence domain/dispatch domain/fire_spec domain/gap domain/issue_tree domain/ticket domain/workflow_state domain/outcome services/agent_service services/amendment_writeback services/base_resolver services/criterion_sources services/fire_context services/fire_dispatcher services/native_amendments services/native_execution services/scope_dispatcher services/scope_membership services/scope_planning services/scope_resolution types/domain/accept types/domain/amendment types/domain/amendment_write types/domain/criteria types/domain/criterion_evidence types/domain/criterion_ref types/domain/fire types/domain/fire_spec types/domain/native_execution types/domain/ralph_outcome types/domain/remediation types/domain/scope_ready types/domain/trajectory types/domain/workflow types/domain/workspace'''.split()
rows=[];imports={}
for item in seed:
 name='src/kodezart/'+item+'.py';src=D/name;dest=T/name
 if not src.exists():print('MISSING',name);continue
 s=src.read_text();rows.append({'path':name,'whole':True,'donor':'da39c439898aec1233aa8b6157b35e961df1e453'})
 for n in ast.walk(ast.parse(s)):
  if isinstance(n,ast.ImportFrom) and n.module and n.module.startswith('kodezart'):
   module=n.module.replace('.','/')+'.py';imports.setdefault('src/'+module,set()).update(x.name for x in n.names)
 dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(src.read_bytes())
(O/'m3-extracted-hunks.json').write_text(json.dumps(rows,indent=2)+'\n')
(O/'m3-seed-imports.json').write_text(json.dumps({k:sorted(v) for k,v in imports.items()},indent=2)+'\n')
print('Mechanical seed',len(rows),'modules')
for name,names in imports.items():
 p=T/name
 if not p.exists():print('MISSING IMPORT',name,','.join(sorted(names)))
