import ast
from pathlib import Path

root=Path('/private/tmp/kodezart-v03-m2-description-authority')
p=root/'src/kodezart/types/domain/surface.py';s=p.read_text();s=s.replace('from dataclasses import dataclass','from collections.abc import Awaitable, Callable\nfrom dataclasses import dataclass');anchor='@dataclass(frozen=True, slots=True, kw_only=True)\nclass DescriptionWriteAuthority:';assert anchor in s;s=s.replace(anchor,'type WriteRevalidation = Callable[[], Awaitable[None]]\n\n\n'+anchor);s=s.replace('    holder: str\n    surface: WritableSurface\n','    holder: str\n    surface: WritableSurface\n    revalidate: WriteRevalidation | None = None\n',1);p.write_text(s)
methods={'update_issue_graph','create_split_if_absent','create_criterion_if_absent'}
for f in ['src/kodezart/adapters/linear_mcp_tracker.py','src/kodezart/core/protocols.py','tests/fakes.py']:
 p=root/f;s=p.read_text();s=s.replace('from kodezart.types.domain.surface import (','from kodezart.types.domain.surface import (\n    WriteRevalidation,',1);t=ast.parse(s);lines=s.splitlines(True);repls=[]
 for n in ast.walk(t):
  if isinstance(n,ast.AsyncFunctionDef) and n.name in methods:
   a=n.lineno-1;b=n.end_lineno;piece=''.join(lines[a:b]);needle='    ) -> TrackerIssue:';assert needle in piece
   if n.name=='create_criterion_if_absent':
    piece=piece.replace('        self, *, parent_key: str, title: str, check: str, do: str, holder: str\n','        self, *, parent_key: str, title: str, check: str, do: str, holder: str,\n')
   piece=piece.replace(needle,'        revalidate: WriteRevalidation | None = None,\n'+needle,1)
   if f.startswith('src/kodezart/adapters'):
    old='await self._retry_call(_TOOL_SAVE_ISSUE, attempt)';assert old in piece
    piece=piece.replace(old,'await self._retry_call(_TOOL_SAVE_ISSUE, attempt, revalidate=revalidate)',1)
   elif f=='tests/fakes.py':
    piece=piece.replace('    ) -> TrackerIssue:\n','    ) -> TrackerIssue:\n        if revalidate is not None:\n            await revalidate()\n',1)
   repls.append((a,b,piece))
 for a,b,piece in sorted(repls,reverse=True):lines[a:b]=[piece]
 s=''.join(lines)
 if f.startswith('src/kodezart/adapters'):
  s=s.replace('        self, tool: str, invoke: Callable[[], Awaitable[ResultT]]\n','        self, tool: str, invoke: Callable[[], Awaitable[ResultT]],\n        *, revalidate: WriteRevalidation | None = None,\n',1)
  s=s.replace('            try:\n                return await invoke()','            try:\n                if revalidate is not None:\n                    await revalidate()\n                return await invoke()',1)
  a=s.index('    async def edit_description(');b=s.index('\n    def ',a);piece=s[a:b];piece=piece.replace('await self._retry_call(_TOOL_SAVE_ISSUE, attempt)','await self._retry_call(_TOOL_SAVE_ISSUE, attempt, revalidate=authorization.revalidate)');s=s[:a]+piece+s[b:]
 p.write_text(s)
p=root/'src/kodezart/services/organize_owner.py';s=p.read_text().replace('from hashlib import sha256','from functools import partial\nfrom hashlib import sha256');s=s.replace('                            holder=job_id, surface=surface\n','                            holder=job_id, surface=surface,\n                                revalidate=partial(authorize, proposal, frozenset({request.issue_key})),\n')
# Exact owner call boundaries; current context snapshots advance after each actual creation.
s=s.replace('                            holder=job_id,\n                        )\n                    )\n                return\n            if isinstance(value, SplitProposal):','                            holder=job_id,\n                            revalidate=partial(authorize, proposal, peers),\n                        )\n                    )\n                return\n            if isinstance(value, SplitProposal):',1)
for call in ['create_split_if_absent','create_criterion_if_absent']:
 a=s.index('self._tracker.'+call+'(');b=s.index('holder=job_id,',a)+len('holder=job_id,');s=s[:b]+'''\n                                revalidate=partial(
                                    authorize,
                                    ProposedWrite(
                                        context=expected_context,
                                        revision=proposal.revision,
                                        proposal=proposal.proposal,
                                    ),
                                    frozenset({request.issue_key}),
                                ),'''+s[b:]
p.write_text(s)
