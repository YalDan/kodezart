from pathlib import Path
root=Path('/private/tmp/kodezart-v03-recovery-alarm-owner')
contract='''    async def record_run_alarm(
        self, *, issue_key: str, alarm: RunAlarm, holder: str
    ) -> None:
        """Upsert the complete (subject, signal) record under its live marker lease.

        The explicit issue_key is the carrier, not an inferred subject lane.
        The actual writing job holds this exact marker through settlement.
        Equal replay writes nothing; damaged or duplicate addressed records
        refuse, as do missing, expired or foreign holders.
        """
        ...

    async def read_run_alarm(
        self, *, issue_key: str, subject: AlarmSubject, signal: AlarmSignal
    ) -> RunAlarm | None:
        """Read exactly this address; absence is None, damage is a typed refusal."""
        ...

'''
impl='''    async def record_run_alarm(
        self, *, issue_key: str, alarm: RunAlarm, holder: str
    ) -> None:
        """Keep one whole-subject record under the existing leased upsert policy."""
        await self.read_run_alarm(
            issue_key=issue_key, subject=alarm.subject, signal=alarm.signal
        )
        await self.upsert_comment(
            target=issue_key,
            marker=run_alarm_marker(
                subject=alarm.subject,
                signal=alarm.signal,
                marker_prefixes=PREFIXES,
            ),
            body=render_run_alarm(alarm=alarm),
            holder=holder,
        )

    async def read_run_alarm(
        self, *, issue_key: str, subject: AlarmSubject, signal: AlarmSignal
    ) -> RunAlarm | None:
        """Resolve the full subject and signal across the native comment log."""
        marker = run_alarm_marker(
            subject=subject, signal=signal, marker_prefixes=PREFIXES
        )
        stored = comment_under_marker(
            target=issue_key,
            marker=marker,
            comments=await self.list_comments(issue_key=issue_key),
        )
        if stored is None:
            return None
        try:
            return parse_run_alarm(
                body=stored.body,
                subject=subject,
                signal=signal,
                marker_prefixes=PREFIXES,
            )
        except ValueError as exc:
            raise TrackerProtocolError(
                "run-alarm record does not match its declared shape",
                tool=TOOL,
                detail=stored.comment_key,
            ) from exc

'''
for rel in ['src/kodezart/core/protocols.py','src/kodezart/adapters/linear_mcp_tracker.py','tests/fakes.py']:
    p=root/rel;s=p.read_text();anchor='from kodezart.types.domain.run_state import '
    if anchor not in s:
        anchor='from kodezart.types.domain.scope import '
    pos=s.index(anchor)
    s=s[:pos]+'from kodezart.types.domain.run_alarm import AlarmSignal, AlarmSubject, RunAlarm\n'+s[pos:]
    if 'protocols' not in rel:
        pos=s.index('from kodezart.domain.run_event_stream import ')
        s=s[:pos]+'from kodezart.domain.run_alarm_record import parse_run_alarm, render_run_alarm, run_alarm_marker\n'+s[pos:]
    start=s.index('class FakeTrackerPort:') if rel=='tests/fakes.py' else 0
    pos=s.index('    async def post_run_event(',start)
    addition=contract if 'protocols' in rel else impl.replace('PREFIXES','self.marker_prefixes' if rel=='tests/fakes.py' else 'self._marker_prefixes').replace('TOOL','"list_comments"' if rel=='tests/fakes.py' else '_TOOL_LIST_COMMENTS')
    s=s[:pos]+addition+s[pos:];p.write_text(s)
p=root/'src/kodezart/domain/run_alarm_record.py';s=p.read_text().replace('_SUBJECT = TypeAdapter(AlarmSubject)','_SUBJECT: TypeAdapter[AlarmSubject] = TypeAdapter(AlarmSubject)').replace('"""Canonical source-reference spelling; typed evidence retains the surface itself."""','"""Canonical source reference; evidence retains the typed surface itself."""');p.write_text(s)
