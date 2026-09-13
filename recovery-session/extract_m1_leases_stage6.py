from pathlib import Path
exec(Path('/private/tmp/kodezart-recovery-session/extract_m1_leases.py').read_text().split('\nfor path in ')[0])
MAP=json.loads(Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage5.json').read_text())
p='src/kodezart/adapters/linear_mcp_tracker.py'
transplant(p,['_saved_issue'],parent='LinearMcpTracker')
text=(ROOT/p).read_text();src=donor(p)
text=src[:src.index('\n\nimport asyncio')]+text[text.index('\n\nimport asyncio'):]
# Only exact mutation receipts, leaving later identity/state-history policy out.
text=text.replace('return self._saved_issue(payload)\n\n    async def update_issue','return self._saved_issue(payload, written={"title": title, "description": body})\n\n    async def update_issue')
text=text.replace('return self._saved_issue(payload)\n\n    async def set_workflow_state','return self._saved_issue(payload, written=arguments)\n\n    async def set_workflow_state')
text=text.replace('return self._saved_issue(payload)\n\n    async def set_queue_state','return self._saved_issue(payload, written={"state": state_name})\n\n    async def set_queue_state')
text=text.replace('return self._saved_issue(payload)\n\n    async def post_comment','return self._saved_issue(payload, written={"labels": [*preserved, self._label_for(state)]})\n\n    async def post_comment')
for method,field in [('work_refs','work_ref_pattern'),('read_base_spec','base_spec_pattern'),('recorded_repository','repository_pattern')]:
 n=nodes(text,'LinearMcpTracker')[method];old=code(text,n)
 new=old.replace('        for wire in await self._comment_wires(issue_key):',f'        pattern = self._markers.{field}\n        for wire in await self._comment_wires(issue_key):').replace(f'self._markers.{field}.search','pattern.search')
 assert old!=new
 text=text.replace(old,new)
 MAP.append({'file':p,'symbol':f'LinearMcpTracker.{method}: configured reader preflight','source':D2,'normalization':'preserve existing M1 marker contents; exclude M4 landing policy'})
put(p,text);imports(p,src)
MAP.append({'file':p,'symbol':'existing create/update/state/queue receipt tails and module documentation','source':D2,'normalization':'only declared fields receipts; state history and issue identity excluded'})
p='tests/fakes.py';text=(ROOT/p).read_text();n=nodes(text,'FakeLinearMcpServer')['_tool_save_issue'];old=code(text,n)
new=old.replace('        self._moved(issue.id)', '''        if "addLabels" in arguments:
            additions = arguments["addLabels"]
            assert isinstance(additions, list)
            issue.labels = list(dict.fromkeys([*issue.labels, *map(str, additions)]))
        self._moved(issue.id)''')
assert new!=old;put(p,text.replace(old,new));MAP.append({'file':p,'symbol':'FakeLinearMcpServer._tool_save_issue addLabels native boundary fidelity','source':D2})
for p,names,owner in [('tests/tracker/test_comment_expected.py',['test_alarm_parser_reads_the_same_final_snapshot_as_the_lease'],'M7'),('tests/adapters/test_self_write_replay.py',['test_mixed_declared_issue_fields_and_comment_churn_stay_quiet','test_label_addition_never_claims_other_labels_from_the_response'],'M2/M4 classification consumer')]:
 remove(p,names)
 MAP.append({'file':p,'symbol':names,'source':D2,'excluded_owner':owner,'reason':'original unchanged consumer controls retained with later consumer; its source not in M1'})
p='tests/tracker/test_comment_expected_independent.py';text=(ROOT/p).read_text();a=text.index('        issue_labels={');b=text.index('        caller=server,',a);put(p,text[:a]+text[b:]);MAP.append({'file':p,'symbol':'retry fixture constructor','source':CURRENT,'normalization':'omit later criterion/scope mappings not used by protected comments; assertions unchanged'})
p='tests/tracker/test_tracker_boot.py';text=(ROOT/p).read_text().replace('assert users == {APPROVER, BYSTANDER}','assert users == {APPROVER, BYSTANDER, AGENT_IDENTITY}').replace('            APPROVER,\n            BYSTANDER,','            APPROVER,\n            BYSTANDER,\n            AGENT_IDENTITY,').replace('users=[APPROVER, BYSTANDER]','users=[APPROVER, BYSTANDER, AGENT_IDENTITY]');put(p,text)
MAP.append({'file':p,'symbol':'configured writer user fixture membership','source':D2,'normalization':'account for actual declared writer identity in existing mapping tests'})
replace('tests/services/test_run_surface_lease.py','                repo_url=None,','                repo_url="https://github.com/example/fixture.git",','valid actual M1 queue request fixture')
replace('docs/configuration.md','the `KODEZART_TRACKER__` nested environment prefix.','nested environment names such as `KODEZART_TRACKER__SERVER_NAME`.','document real nested field instead of incomplete variable')
Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage6.json').write_text(json.dumps(MAP,indent=2)+'\n')
