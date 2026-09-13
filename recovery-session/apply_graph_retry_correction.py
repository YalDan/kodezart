from pathlib import Path
p=Path('src/kodezart/adapters/linear_mcp_tracker.py');s=p.read_text()
pos=s.index('@dataclass\nclass _LabelListings:')
s=s[:pos]+'''@dataclass(frozen=True, slots=True)
class _SplitCreation:
    """Actual native save receipt and the facts needed for its separate readback."""

    saved: TrackerIssue
    source: TrackerIssue
    content: str


'''+s[pos:]
a=s.index('    async def update_issue_graph(')
b=s.index('    async def read_split_children(',a)
method=s[a:b]
head_end=method.index('        facts = await self._read_unchanged_graph')
head=method[:head_end]
verify_start=method.index('        for expected_issue in (')
verify=method[verify_start:]
verify=verify.replace('''        for expected_issue in (
            candidate,
            *changed_peers(issue_key=issue_key, changes=changes, issues=facts),
        ):
''','''        for expected_issue in written:
''')
public=head+'''        async def attempt() -> tuple[TrackerIssue, ...]:
            return await self._update_issue_graph_once(
                issue_key=issue_key, expected=expected, changes=changes, holder=holder
            )

        written = await self._retry_call(_TOOL_SAVE_ISSUE, attempt)
        # A completed save must never be retried because a later read cannot answer.
'''+verify
once=head.replace('async def update_issue_graph(', 'async def _update_issue_graph_once(').replace('    ) -> TrackerIssue:', '    ) -> tuple[TrackerIssue, ...]:')+method[head_end:verify_start]
once=once.replace('            return candidate\n','            return (candidate,)\n').replace('await self._call(_TOOL_SAVE_ISSUE, arguments)', 'await self._send(_TOOL_SAVE_ISSUE, arguments)')
once+='''        return (
            candidate,
            *changed_peers(issue_key=issue_key, changes=changes, issues=facts),
        )

'''
s=s[:a]+public+once+s[b:]
a=s.index('    async def create_split_if_absent(')
b=s.index('    async def _unstarted_state_id(',a)
method=s[a:b]
pre_end=method.index('        self._issue_identity.require_prefix()')
post_start=method.index('        current = await self.read_issue(issue_key=created.issue_key)')
post=method[post_start:]
public=method[:pre_end]+'''        async def attempt() -> TrackerIssue | _SplitCreation:
            return await self._create_split_once(
                identity=identity,
                title=title,
                body=body,
                holder=holder,
                expected=expected,
            )

        written = await self._retry_call(_TOOL_SAVE_ISSUE, attempt)
        if isinstance(written, TrackerIssue):
            return written
        created, source, content = written.saved, written.source, written.content
        # This verification is outside the resend boundary even when it fails.
'''+post
once='''    async def _create_split_once(
        self,
        *,
        identity: IssueIdentity,
        title: str,
        body: str,
        holder: str,
        expected: tuple[IssueGraphSnapshot, ...],
    ) -> TrackerIssue | _SplitCreation:
        source_key = identity.scope_key.key
'''+method[pre_end:post_start].replace('await self._call(_TOOL_SAVE_ISSUE, arguments)', 'await self._send(_TOOL_SAVE_ISSUE, arguments)')+'''        return _SplitCreation(saved=created, source=source, content=content)

'''
s=s[:a]+public+once+s[b:]
p.write_text(s)
