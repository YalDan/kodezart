import json,subprocess,sys
from pathlib import Path
O=Path('/private/tmp/kodezart-recovery-session')
files=json.loads((O/'m5-original-test-selection.json').read_text())
args=['/Users/kodezart/.local/bin/uv','run','--locked','--python','3.12','pytest','-q',*files,'tests/domain/test_amendment.py','tests/chains/test_ralph_workflow.py::test_house_rules_delivered_as_system_prompt_append',*sys.argv[1:]]
print('COMMAND:',repr(args),flush=True)
raise SystemExit(subprocess.call(args))
