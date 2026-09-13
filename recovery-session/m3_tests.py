from pathlib import Path
import json
D=Path('/private/tmp/kodezart-v03-recovery-integration');T=Path('/private/tmp/kodezart-v03-m3-plan-walk');O=Path('/private/tmp/kodezart-recovery-session')
source=json.loads((O/'m3-extracted-hunks.json').read_text());stems={Path(r['path']).stem for r in source if r.get('whole')}
explicit='''tests/chains/test_amendment_review_reentry.py tests/domain/test_authored_feasibility_compatibility.py tests/domain/test_subtree_closure.py tests/domain/test_criterion_amendment_fields.py tests/services/test_applied_native_amendments.py tests/services/test_amendment_interrupted_writes.py tests/tracker/test_native_criterion_amendment.py tests/tracker/test_scope_subtree_reach.py tests/tracker/test_scope_planning.py tests/tracker/test_criterion_resolution.py tests/tracker/test_criterion_resolution_consumers.py tests/chains/test_authored_check_routing.py'''.split()
selected=set(explicit)
for parent in ['chains','services','adapters','domain','types']:
 for p in (D/'tests'/parent).glob('test_*.py'):
  if p.stem[5:] in stems or (p.stem.startswith('test_native_') and p.stem!='test_native_delivery') or (parent=='chains' and p.stem.startswith('test_scope_')):selected.add(str(p.relative_to(D)))
for name in sorted(selected):
 src=D/name
 if not src.exists():print('ABSENT TEST',name);continue
 dest=T/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(src.read_bytes())
(O/'m3-test-extraction-map.json').write_text(json.dumps([{'path':n,'donor':'da39c439898aec1233aa8b6157b35e961df1e453','byte_identical':True} for n in sorted(selected) if (D/n).exists()],indent=2)+'\n')
print('Test modules',len(selected))
