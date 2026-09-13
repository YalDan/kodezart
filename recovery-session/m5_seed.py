import ast,json,sys
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m5-delivery-termination')
h.D=Path('/private/tmp/kodezart-v03-m5-union-roster-correction')
original=h.key
h.key=lambda n:n.name.id if isinstance(n,ast.TypeAlias) else original(n)
files=['chains/delivery_coordinator.py','chains/lane_delivery.py','chains/native_delivery.py','services/union_composition.py','services/union_identity.py','services/union_tick.py','services/lane_reports.py','services/scope_runtime.py','composition/scope_runtime.py','composition/delivery.py','adapters/no_forge_delivery.py','adapters/subprocess_check_chain.py','domain/check_chain.py','types/domain/delivery.py','types/domain/native_delivery.py','types/domain/pr_state.py','types/domain/check_chain.py','types/domain/scope_runtime.py','types/domain/scope_terminal.py','types/domain/union.py','types/domain/union_tick.py']
for p in files:h.whole('src/kodezart/'+p)
for p,names in {
 'core/protocols.py':['ForgeQuery','PRStateReader','CheckChainRunner'],
 'types/domain/run_state.py':['LanePR'],
 'domain/errors.py':['CheckChainExecutionError','DeliveryHeadError','PRStateReadError','UnionHeadReadError','UnionUnstableError'],
 'core/errors.py':['LaneRosterArityError'],
 'services/git_observations.py':['read_remote_head'],
}.items():
 for n in names:h.symbol('src/kodezart/'+p,n)
h.symbol('src/kodezart/core/protocols.py','merge_scratch_head','GitService')
h.symbol('src/kodezart/adapters/subprocess_git_service.py','merge_scratch_head','SubprocessGitService')
Path('/private/tmp/kodezart-recovery-session/m5-extracted-hunks.json').write_text(json.dumps(h.records,indent=2)+'\n')
