import sys,ast
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
from extract_m2 import *
# This newly staged donor file exclusively exercises the unrelated public upsert API.
(T/'tests/tracker/test_issue_identity_boundary.py').unlink()
# Keep actual M2-used native approval controls; resolve_scope composition remains M3.
p='tests/tracker/test_milestone_approval.py';text=(T/p).read_text();n=node(text,'test_actual_milestone_scope_uses_current_project_approval');lines=text.splitlines(keepends=True);del lines[start(n):n.end_lineno];text=''.join(lines).replace('from kodezart.services.scope_resolution import resolve_scope\n','');(T/p).write_text(text)
p='tests/tracker/test_native_approval_aliases.py';text=(T/p).read_text().replace('@pytest.mark.parametrize("entry", ["approval", "spec"])','@pytest.mark.parametrize("entry", ["approval"])');(T/p).write_text(text)
symbol('tests/tracker/conftest.py','FIRE_ENTRY_LABELS')
whole('tests/tracker/test_scope_tool_arguments.py')
