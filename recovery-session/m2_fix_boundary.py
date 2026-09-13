from pathlib import Path
T=Path('/private/tmp/kodezart-v03-m2-organize-boundaries');p=T/'src/kodezart/adapters/linear_mcp_tracker.py';s=p.read_text()
pos=s.index('@dataclass(frozen=True, slots=True)\nclass _SplitCreation:')
s=s[:pos]+'''@dataclass(frozen=True, slots=True)
class _CriterionCreation:
    """A completed save receipt requiring readback outside the retry scope."""

    saved: TrackerIssue


'''+s[pos:]
start=s.index('    async def create_criterion_if_absent(');end=s.index('    async def read_issue_identity(',start)
old=s[start:end]
pre,rest=old.split('        children = await self.read_criteria',1)
preconditions='        children = await self.read_criteria'+rest.split('        current = await self.read_issue(issue_key=created.issue_key)')[0]
preconditions=preconditions.replace('await self._call(\n                _TOOL_SAVE_ISSUE,','await self._send(\n                _TOOL_SAVE_ISSUE,')
# All preparation, identity lookup and final lease check are freshly invoked per attempt.
new=pre+'        async def attempt() -> TrackerIssue | _CriterionCreation:\n'+''.join('    '+line if line.strip() else line for line in preconditions.splitlines(keepends=True))+'            return _CriterionCreation(saved=created)\n\n        written = await self._retry_call(_TOOL_SAVE_ISSUE, attempt)\n        if isinstance(written, TrackerIssue):\n            return written\n        created = written.saved\n        # Readback failure must never resend the completed creation.\n        current = await self.read_issue(issue_key=created.issue_key)'+old.split('        current = await self.read_issue(issue_key=created.issue_key)',1)[1]
s=s[:start]+new+s[end:];p.write_text(s)
old='''The current owner can edit the addressed issue's body and create criterion
children. It cannot change graph parentage, blockedBy, relatedTo, priority,
milestone, splits or an existing criterion's body. If one of those operations
is necessary, return the unavailable proposal with its exact capability and
evidence. A missing write capability is not a human decision; reserve the
unresolved proposal for a real unruled decision. Never hide structural changes
inside a body-only proposal or claim that description prose changed the graph.'''
new='''This role returns a `criteria` proposal to create criterion children. The caller
accepts no body, graph or split proposal from this role. Those specification and
structural repairs belong to the separate Organize authoring role. Do not claim
that criterion prose edits the addressed issue or changes its native graph.

Editing an existing criterion is unavailable: when required, return `unavailable`
with capability `criterion_edit` and concrete evidence. This is the only declared
unavailable capability. Reserve `unresolved` for a real unruled human decision;
a missing write capability does not grant approval or constitute that decision.'''
for name in ['anthropic_v5','claude-opus']:
 p=T/f'src/kodezart/prompts/sets/{name}/organize_criteria_author.md';s=p.read_text();assert old in s;p.write_text(s.replace(old,new))
(T/'tests/tracker/test_criterion_retry_independent.py').write_bytes(Path('/private/tmp/kodezart-recovery-session/test_m2_criterion_retry_independent.py').read_bytes())
