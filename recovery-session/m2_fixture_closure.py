import sys
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
from extract_m2 import *
symbol('src/kodezart/core/prompt_namespaces.py','bindings_for')
# Exact native graph fixture fields; history receipt fields remain outside M2.
for name in ['project_id','milestone_id','entry']:symbol('tests/fakes.py',name,'FakeMcpIssue')
# Remove an unused later-native-fire constructor input from the two M2 fixture sites.
for p in ['tests/chains/test_organize_owner.py','tests/integration/test_organize_scheduler.py']:
 text=(T/p).read_text().replace('        criteria_stage_label_key="criteria",\n','');(T/p).write_text(text)
# Current maintained M1's real workspace provider requires explicit commit identity.
p='tests/chains/test_organize_ownership.py';text=(T/p).read_text().replace('        git=native_git,','        git=native_git,\n        committer_name="fixture",\n        committer_email="fixture@example.invalid",');(T/p).write_text(text)
# Existing actual-adapter fixture classifications required by criterion-creation tests.
p='tests/tracker/test_linear_mcp_tracker.py';text=(T/p).read_text().replace('        "issue_labels": {},','        "issue_labels": {"criterion": "acceptance-condition"},\n        "scope_labels": {"approved": "execution-consent"},');(T/p).write_text(text)
# The real example backs several existing integration fixtures and must declare M2's record prefixes.
p='docs/operation.example.toml';text=(T/p).read_text().replace('[marker_prefixes]\n','[marker_prefixes]\nruling = "kodezart-ruling"\nissue_identity = "kodezart-issue"\nescalation = "escalation"\n');src=read(p);a=src.index('# Optional pre-approval organize phase table.');b=src.index('# Independent native Audit roster.',a);pos=text.index('# Identities the agent posts under');text=text[:pos]+src[a:b]+'\n'+text[pos:];(T/p).write_text(text)
