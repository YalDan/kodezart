import ast,shutil
from pathlib import Path
D=Path('/private/tmp/kodezart-v03-m5-union-roster-correction');T=Path('/private/tmp/kodezart-v03-m5-delivery-termination')
shutil.copyfile(D/'tests/git_read_cancellation.py',T/'tests/git_read_cancellation.py')
s=(D/'tests/chains/test_audit_pass.py').read_text();t=ast.parse(s);lines=s.splitlines(keepends=True)
names={'REPO','HEAD','BRANCH','pr_state'}
chunks=[]
for n in t.body:
 name=n.name if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) else next((a.id for a in n.targets if isinstance(a,ast.Name)),None) if isinstance(n,ast.Assign) else None
 if name in names:chunks.append(''.join(lines[n.lineno-1:n.end_lineno]))
(T/'tests/pr_state_fixture.py').write_text('"""Original addressed PR fixture shared by delivery and later audit controls."""\n\nfrom kodezart.types.domain.pr_state import PRLifecycle, PRState\n\n'+'\n\n'.join(chunks)+'\n')
p=T/'tests/adapters/test_pr_state_reader.py';p.write_text(p.read_text().replace('from tests.chains.test_audit_pass import pr_state','from tests.pr_state_fixture import pr_state'))
p=T/'tests/integration/test_scope_runtime.py';s=p.read_text().replace('from kodezart.core.job_queue_settings import JobQueueSettings\n','').replace('build_job_queue(settings=JobQueueSettings(), workflow_engine=harness.engine)','build_job_queue(config=AppConfig(), workflow_engine=harness.engine)');p.write_text(s)
