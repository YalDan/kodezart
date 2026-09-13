from pathlib import Path
p=Path('src/kodezart/adapters/linear_mcp_tracker.py');s=p.read_text()
a=s.index('        arguments: dict[str, object] = {"id": issue_key}',s.index('    async def update_issue_graph('))
b=s.index('            else:\n                add_name, remove_name',a)
old=s[a:b]
start=old.index('                if change.milestone_id is None:')
end=old.index('                arguments["milestone"] = change.milestone_id')
validation=old[start:end]
validation='\n'.join(line[4:] for line in validation.splitlines())+'\n'
replacement='''        async def require_milestone(change: MilestoneChange) -> None:
'''+validation+'''
        arguments: dict[str, object] = {"id": issue_key}
        for change in changes:
            if isinstance(change, ParentChange):
                arguments["parentId"] = change.parent_id
            elif isinstance(change, PriorityChange):
                arguments["priority"] = _RAW_BY_PRIORITY[change.priority]
            elif isinstance(change, MilestoneChange):
                await require_milestone(change)
                arguments["milestone"] = change.milestone_id
'''
s=s[:a]+replacement+s[b:]
a=s.index('        for peer in sorted(peers):',s.index('    async def update_issue_graph('))
b=s.index('        if graph_snapshot(',a)
s=s[:a]+'''        surfaces = tuple(
            WritableSurface(
                kind=SurfaceKind.ISSUE_GRAPH,
                ref=ScopeRef(kind=ScopeKind.ISSUE, key=peer),
            )
            for peer in sorted(peers)
        )
        markers = await self._markers_on(
            _GrantKind.LEASE,
            targets=tuple(_LEASE_ADDRESSING.target(surface) for surface in surfaces),
        )
        for surface in surfaces:
            self._assert_surface_holder(surface=surface, holder=holder, markers=markers)
        facts = await self._read_unchanged_graph(issue_key=issue_key, expected=expected)
        for change in changes:
            if isinstance(change, MilestoneChange):
                await require_milestone(change)
        # Recheck the actual deadline after every awaited preparation read. These
        # observed grants cannot prove that an unseen rival did not arrive later.
        for surface in surfaces:
            self._assert_surface_holder(surface=surface, holder=holder, markers=markers)
'''+s[b:]
a=s.index('        # The whole set read also refuses damaged, duplicate and misplaced peers.',s.index('    async def create_split_if_absent('))
b=s.index('        source = await self.read_issue(',a)
s=s[:a]+'''        async def existing_split() -> TrackerIssue | None:
            # Validate the complete identity set and use each returned child's
            # same observed body; a separate identity read could mix revisions.
            for existing in await self.read_split_children(source_key=source_key):
                held = self._issue_identity.decode(
                    existing.body, issue_key=existing.issue_key
                )
                if held == identity:
                    return existing
            return None

        existing = await existing_split()
        if existing is not None:
            return existing
'''+s[b:]
a=s.index('        await self._require_surface_holder(',s.index('    async def create_split_if_absent('))
b=s.index('        created = self._saved_issue(',a)
s=s[:a]+'''        surface = WritableSurface(
            kind=SurfaceKind.ISSUE_SPLIT_SET, ref=identity.scope_key
        )
        markers = await self._markers_on(
            _GrantKind.LEASE, targets=(_LEASE_ADDRESSING.target(surface),)
        )
        self._assert_surface_holder(surface=surface, holder=holder, markers=markers)
        await self._read_unchanged_graph(issue_key=source_key, expected=expected)
        # State resolution and lease acquisition may have allowed another writer
        # to prepare this identity. Return its current child without overwriting.
        existing = await existing_split()
        if existing is not None:
            return existing
        current_source = await self.read_issue(issue_key=source_key)
        expected_source = next(row for row in expected if row.issue_key == source_key)
        if (
            graph_snapshot(current_source) != expected_source
            or current_source.team_key != source.team_key
        ):
            raise OrganizeWriteRefusalError(
                issue_key=source_key,
                reason="split source changed before creation",
            )
        # No await separates this deadline check from issuing the save. The
        # earlier native snapshot is not an atomic uniqueness or fencing token.
        self._assert_surface_holder(surface=surface, holder=holder, markers=markers)
'''+s[b:]
p.write_text(s)
