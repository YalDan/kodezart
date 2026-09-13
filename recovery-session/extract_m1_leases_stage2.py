from pathlib import Path
exec(Path('/private/tmp/kodezart-recovery-session/extract_m1_leases.py').read_text().split('\nfor path in ')[0])
MAP = json.loads(Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage1.json').read_text())

# Preserve definition order where annotations and module constants execute at import.
p='src/kodezart/adapters/linear_mcp_tracker.py';text=(ROOT/p).read_text()
tree=ast.parse(text); cls=nodes(text)['LinearMcpTracker']; late=[n for n in tree.body if n.lineno>cls.end_lineno]
lines=text.splitlines(keepends=True); additions='\n\n'.join(code(text,n) for n in late)
for n in reversed(late):
    a,b=span(n);del lines[a:b]
text=''.join(lines);at=span(nodes(text)['LinearMcpTracker'])[0];lines=text.splitlines(keepends=True);lines[at:at]=[additions+'\n\n'];put(p,''.join(lines))
p='src/kodezart/adapters/linear_mcp_types.py';text=(ROOT/p).read_text();ns=nodes(text);entry=code(text,ns['LinearCommentEntryWire']);remove(p,['LinearCommentEntryWire']);text=(ROOT/p).read_text();a=span(nodes(text)['LinearCommentListWire'])[0];lines=text.splitlines(keepends=True);lines[a:a]=[entry+'\n\n'];put(p,''.join(lines))

p='src/kodezart/core/config.py'
old_fields=['tracker_mcp_server_name','tracker_mcp_server_url','tracker_mcp_auth_header','tracker_mcp_auth_scheme','tracker_token','tracker_timeout_seconds','tracker_mcp_call_timeout_seconds','tracker_mcp_sse_read_timeout_seconds','tracker_mcp_error_detail_limit','tracker_max_retries','tracker_retry_backoff_factor']
remove(p,old_fields,parent='AppConfig');transplant(p,['tracker','settings_customise_sources'],parent='AppConfig')
replace(p,'        env_prefix="KODEZART_",','        env_prefix="KODEZART_",\n        env_nested_delimiter="__",','nested tracker settings')
text=(ROOT/p).read_text();a=text.index('            return name in {');b=text.index('\n\n        def checked(',a)
allowed=old_fields+['tracker_surface_lease_seconds'];text=text[:a]+'            return name in '+repr(set(allowed))+text[b:];put(p,text)
MAP.append({'file':p,'symbol':'settings_customise_sources.retired','source':D2,'normalization':'only tracker-owned retired names; unrelated settings still active'})

p='src/kodezart/composition/tracker.py'
transplant(p,['CREDENTIAL_FIELD','AGENT_IDENTITY_FIELD','ATTRIBUTABLE_WRITER','refuse_unattributable_writer','build_tracker','boot_tracker'])
text=(ROOT/p).read_text();text=text.replace('    operation.require_run_event_table()\n','')
text=text.replace('                scope_labels=operation.scope_labels,\n','').replace('                issue_labels=operation.issue_labels,\n','')
a=text.index('                criteria_stage_label_key=');b=text.index('                team_identifiers=',a);text=text[:a]+text[b:]
text=text.replace('def make_mcp_tool_caller(*, config: AppConfig, token: str)', 'def make_mcp_tool_caller(*, settings: TrackerSettings, token: str)')
names={'tracker_mcp_server_name':'server_name','tracker_mcp_server_url':'server_url','tracker_mcp_auth_header':'auth_header','tracker_mcp_auth_scheme':'auth_scheme','tracker_timeout_seconds':'timeout_seconds','tracker_mcp_call_timeout_seconds':'call_timeout_seconds','tracker_mcp_sse_read_timeout_seconds':'sse_read_timeout_seconds','tracker_mcp_error_detail_limit':'error_detail_limit'}
for old,new in names.items():text=text.replace('config.'+old,'settings.'+new)
put(p,text);imports(p,donor(p));MAP.append({'file':p,'symbol':'M1-only constructor/boot cut','source':D2,'normalization':'exclude Organize mappings and run-event table; keep pre-existing HTTP token constructor'})
for path,old,new in [('src/kodezart/main.py','boot_tracker(config=config,','boot_tracker(settings=config.tracker,'),('src/kodezart/composition/records.py','config.tracker_mcp_server_name','config.tracker.server_name')]:replace(path,old,new,'tracker settings consumer')

p='tests/fakes.py'
transplant(p,['FakeMcpComment','_CURRENT_USER'])
transplant(p,['_next_instant','_comment_stamp','_tool_save_comment','_tool_list_comments','_comment_parent','_tool_get_user'],parent='FakeLinearMcpServer')
replace(p,'        comment_instants: Sequence[datetime] = (),','        comment_instants: Sequence[datetime] = (),\n        comment_clock: Callable[[], datetime] | None = None,','native comment clock parameter')
replace(p,'        self.comment_instants: list[datetime] = list(comment_instants)','        self.comment_instants: list[datetime] = list(comment_instants)\n        self._comment_clock: Callable[[], datetime] = (\n            comment_clock if comment_clock is not None else lambda: FIXTURE_EPOCH\n        )\n        self._stamps: int = 0\n        self._stamped: datetime | None = None','native comment clock state')
# The boundary must return an actual tool failure when a withdrawn marker disappeared.
replace(p,'        result: McpToolResult = handler(arguments)\n        return result','        try:\n            result: McpToolResult = handler(arguments)\n        except LookupError as exc:\n            raise McpTransportError(\n                f"the MCP server reported a tool error: {exc}",\n                server_name="fake-linear", tool_name=name,\n            ) from exc\n        return result','native tool failure fidelity')
transplant(p,['writer_identity','read_issue_movement','claim_issue','renew_claim','release_claim','active_claim','acquire_surfaces','renew_surfaces','release_surfaces','post_comment','upsert_comment','_upsert_comment'],parent='FakeTrackerPort',rev=CURRENT)
replace(p,'        clock: Callable[[], datetime] = lambda: FIXTURE_EPOCH,','        writer_identities: frozenset[str] = frozenset({"kodezart"}),\n        clock: Callable[[], datetime] = lambda: FIXTURE_EPOCH,','fake writer identity parameter')
replace(p,'        self.claims: dict[str, ClaimResult] = {}','        self.claims: dict[str, ClaimResult] = {}\n        self.leases: dict[WritableSurface, SurfaceLease] = {}\n        self.lease_writes: list[SurfaceLease] = []\n        self.writer_identities = writer_identities\n        self.comment_writes: list[tuple[str, str]] = []','fake surface ownership state')
# Move newly appended module definitions before classes which evaluate their annotations.
text=(ROOT/p).read_text();ns=nodes(text); pieces=[]
for name in ['_CURRENT_USER']:
    pieces.append(code(text,ns[name]));remove(p,[name]);text=(ROOT/p).read_text()
pos=span(nodes(text)['FakeLinearMcpServer'])[0];lines=text.splitlines(keepends=True);lines[pos:pos]=['\n'.join(pieces)+'\n'];put(p,''.join(lines));imports(p,donor(p,CURRENT))

Path('/private/tmp/kodezart-recovery-session/m1-lease-source-map-stage2.json').write_text(json.dumps(MAP,indent=2)+'\n')
