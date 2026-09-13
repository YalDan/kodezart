import ast,json,sys
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m5-delivery-termination');h.D=Path('/private/tmp/kodezart-v03-m5-union-roster-correction')
for n in ['FakeForgeQuery','FakePRStateReader','FakeDeliveryProbe']:h.symbol('tests/fakes.py',n)
h.symbol('tests/fakes.py','merge_scratch_head','FakeGitService')
Path('/private/tmp/kodezart-recovery-session/m5-fake-hunks.json').write_text(json.dumps(h.records,indent=2)+'\n')
