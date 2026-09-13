import ast,json,sys
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m5-delivery-termination');h.D=Path('/private/tmp/kodezart-v03-m5-union-roster-correction')
original=h.key;h.key=lambda n:n.name.id if isinstance(n,ast.TypeAlias) else original(n)
for p in ['composition/engine.py','handlers/agent_handler.py','types/requests/agent.py']:h.whole('src/kodezart/'+p)
for n in ['open_pr_for_head','branch_web_url','read_pr_state','open_delivery_exists']:h.symbol('src/kodezart/adapters/github_api.py',n,'GitHubAPIClient')
for n in ['union_check_step_timeout_seconds','union_stale_max_attempts']:h.symbol('src/kodezart/core/config.py',n,'AppConfig')
p=h.T/'src/kodezart/core/config.py';s=p.read_text();needle='"write_back_max_verify_rounds",';assert needle in s;s=s.replace(needle,needle+'\n                "union_check_cleanup_poll_interval_seconds",',1);p.write_text(s)
p=h.T/'src/kodezart/types/domain/operation.py';s=p.read_text().replace('_check_chain_failures','check_chain_failures');p.write_text(s);h.symbol('src/kodezart/types/domain/operation.py','check_chain_failures')
for n in ['work_refs','record_work_ref']:h.symbol('src/kodezart/adapters/linear_mcp_tracker.py',n,'LinearMcpTracker')
for n in ['work_ref_pattern','work_ref_body']:h.symbol('src/kodezart/adapters/linear_markers.py',n,'LinearMarkers')
h.symbol('src/kodezart/types/domain/branch.py','WorkRef')
log=Path('/private/tmp/kodezart-recovery-session/m5-extracted-hunks.json');log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')
