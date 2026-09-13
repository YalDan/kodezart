import sys,json
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk')
h.whole('src/kodezart/composition/engine.py')
p=h.T/'src/kodezart/composition/engine.py';s=p.read_text().replace('from kodezart.composition.delivery import build_native_lane_workflow\n','').replace('from kodezart.composition.scope_runtime import build_scope_runtime\n','');a=s.index('    scoped_arm = None\n');b=s.index('    return OriginRoutedWorkflowEngine(',a);s=s[:a]+s[b:];s=s.replace('        scoped_arm=scoped_arm,\n','');p.write_text(s)
for n in ['worktree_identity','_identity_git']:h.symbol('src/kodezart/adapters/subprocess_git_service.py',n,'SubprocessGitService')
for n in ['delivery_max_concurrent_watches','delivery_red_rerun_max_attempts']:
 h.symbol('src/kodezart/core/config.py',n,'AppConfig')
log=Path('/private/tmp/kodezart-recovery-session/m3-extracted-hunks.json');log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')
