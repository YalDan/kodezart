from pathlib import Path
import subprocess,ast
D='d2c6fce'
def donor(path): return subprocess.check_output(['git','show',f'{D}:{path}']).decode()
def method(text, parent, name):
 tree=ast.parse(text);node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==parent)
 return next(n for n in node.body if getattr(n,'name',None)==name)
def copy_method(path,parent,name):
 p=Path(path);old=p.read_text();new=donor(path);a=method(old,parent,name);b=method(new,parent,name)
 lines=old.splitlines(keepends=True);src=new.splitlines(keepends=True)
 start=min([a.lineno]+[d.lineno for d in a.decorator_list])-1
 end=min([b.lineno]+[d.lineno for d in b.decorator_list])-1
 lines[start:a.end_lineno]=src[end:b.end_lineno];p.write_text(''.join(lines))
path='src/kodezart/adapters/linear_mcp_tracker.py';copy_method(path,'LinearMcpTracker','_probe_scope')
p=Path('src/kodezart/services/claim_heartbeat.py');s=p.read_text().replace('McpCredentialRefusedError','TrackerAccessDeniedError').replace('                    server_name=exc.server_name,\n','');p.write_text(s)
p=Path('src/kodezart/services/pass_gate.py');s=p.read_text().replace('from kodezart.core.errors import McpTransportError, PassGateScopeError','from kodezart.core.errors import PassGateScopeError, TrackerUnavailableError, TrackerProtocolError').replace('except McpTransportError as exc:', 'except (TrackerUnavailableError, TrackerProtocolError) as exc:');p.write_text(s)
path='tests/tracker/test_linear_mcp_tracker.py'
for parent,name in [('TestCapabilityProbe','test_a_transport_failure_that_says_nothing_about_scope_propagates'),('TestCapabilityProbe','test_a_refusal_the_vendor_did_not_diagnose_as_scope_propagates'),('TestTransientRetry','test_exhausting_the_budget_raises_the_transient_error'),('TestTransportRetry','test_exhausting_the_budget_raises_the_transport_error'),('TestARefusedCredentialIsNeverRetried','test_the_refusal_stops_the_loop_on_the_attempt_that_met_it'),('TestARefusedCredentialIsNeverRetried','test_the_refusal_names_the_credential_once'),('TestARetryBudgetIsNotSpentOnASessionThatDied','test_the_call_the_death_landed_under_is_told_and_the_next_call_reaches'),('TestARetryBudgetIsNotSpentOnASessionThatDied','test_an_unanswered_write_is_not_made_again_within_any_budget')]:
 copy_method(path,parent,name)
p=Path(path);s=p.read_text().replace('    McpCredentialRefusedError,\n','    TrackerAccessDeniedError,\n').replace('    McpTransportError,\n','    TrackerUnavailableError,\n');p.write_text(s)
copy_method('tests/services/test_claim_heartbeat.py','RefusedCredentialTracker','renew_claim')
p=Path('tests/services/test_claim_heartbeat.py');s=p.read_text().replace('McpCredentialRefusedError','TrackerAccessDeniedError').replace('        assert refused[0]["server_name"] == REFUSING_SERVER\n','');p.write_text(s)
copy_method('tests/services/test_pass_gate.py','RefusingTracker','_refuse')
p=Path('tests/services/test_pass_gate.py');s=p.read_text().replace('McpTransportError','TrackerUnavailableError');p.write_text(s)
print('Extracted typed tracker failure consumer and oracle hunks; read/claim semantics unchanged')
