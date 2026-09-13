from pathlib import Path
import shutil
r=Path('/private/tmp/kodezart-v03-m4-verifier-extraction')
p=r/'tests/chains/test_write_back_verifier.py'
p.write_text(p.read_text().replace('        marker_prefixes={"evidence": "fixture-evidence"},\n',''))
p=r/'tests/prompt_census.py'
s=p.read_text()
import re
s=re.sub(r'(PROMPT_FUNCTION_COUNT\s*=\s*)(\d+)', lambda m:m[1]+str(int(m[2])+1),s)
p.write_text(s)
shutil.copyfile('/private/tmp/kodezart-recovery-session/test_m4_tracker_artifacts.py',r/'tests/chains/test_write_back_tracker_boundary.py')
