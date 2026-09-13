import json,shutil,ast
from pathlib import Path
D=Path('/private/tmp/kodezart-v03-m5-union-roster-correction');T=Path('/private/tmp/kodezart-v03-m5-delivery-termination');O=Path('/private/tmp/kodezart-recovery-session')
terms=('delivery','union','scope_runtime','scope_terminal','lane_report','check_chain','landing','scope_request','scope_workflow')
files=[p.relative_to(D).as_posix() for p in D.glob('tests/**/*.py') if any(t in p.name for t in terms) and not any(t in p.name for t in ('audit','knowledge_settings','organize_delivery'))]
files+=['tests/adapters/test_forge_query.py','tests/adapters/test_pr_state_reader.py','tests/test_forge_origin_selection.py']
for f in sorted(set(files)):
 (T/f).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(D/f,T/f)
# Restore precisely the scope-egress original deferred at the M3 boundary.
import sys
sys.path.insert(0,str(O));import extract_m2 as h
h.T=T;h.D=D
h.symbol('tests/domain/test_amendment.py','test_actual_scope_egress_roundtrips_required_nulls_and_rejects_bad_native_reports')
# Source census again covers the now-transferred real delivery author.
p=T/'tests/chains/test_ralph_workflow.py';s=p.read_text();n=ast.parse(s)
needle='KEYED_DISPATCH_COUNTS = {'
assert needle in s and '"lane_delivery.py": 1' not in s
s=s.replace(needle,needle+'\n    "lane_delivery.py": 1,',1);p.write_text(s)
(O/'m5-original-test-selection.json').write_text(json.dumps(sorted(set(files)),indent=2)+'\n')
(O/'m5-scope-egress-hunk.json').write_text(json.dumps(h.records,indent=2)+'\n')
print(len(files),'original test modules')
