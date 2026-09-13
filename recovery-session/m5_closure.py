import ast,json,sys
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m5-delivery-termination');h.D=Path('/private/tmp/kodezart-v03-m5-union-roster-correction')
original=h.key;h.key=lambda n:n.name.id if isinstance(n,ast.TypeAlias) else original(n)
for n in ['PullRequestBranchRepository','PullRequestBranchState','PullRequestStateResponse']:h.symbol('src/kodezart/adapters/github_types.py',n)
h.symbol('src/kodezart/types/domain/agent.py','NativeFireProgressEvent')
for n in ['scope_converged','scope_converged_with_residual','scope_stopped_short']:h.symbol('src/kodezart/types/domain/outcome.py',n,'WorkflowOutcome')
log=Path('/private/tmp/kodezart-recovery-session/m5-extracted-hunks.json');log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')
