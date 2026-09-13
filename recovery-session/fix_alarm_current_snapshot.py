from pathlib import Path
root=Path('/private/tmp/kodezart-v03-recovery-alarm-owner')
for file in ('src/kodezart/adapters/linear_mcp_tracker.py','tests/fakes.py'):
 p=root/file;s=p.read_text();marker='    async def upsert_comment(\n';start=s.index(marker);body_start=s.index('        content = marked_comment_body', start)
 wrapper='''    async def upsert_comment(
        self, *, target: str, marker: str, body: str, holder: str | None = None
    ) -> TrackerComment:
        """Resolve the marker through the single attributed, leased writer."""
        return await self._upsert_comment(
            target=target, marker=marker, body=body, holder=holder
        )

    async def _upsert_comment(
        self,
        *,
        target: str,
        marker: str,
        body: str,
        holder: str | None,
        validate_existing: Callable[[TrackerComment], None] | None = None,
    ) -> TrackerComment:
        """Validate the exact addressed snapshot before issuing its mutation.

        The synchronous precondition sees the same comment used by this
        writer, after attribution and ownership checks. The backend offers
        no conditional update to fence changes unseen after that read.
        """
'''
 s=s[:start]+wrapper+s[body_start:]
 start=s.index('    async def _upsert_comment(');i=s.index('        if existing is None:',start)
 s=s[:i]+'''        if existing is not None and validate_existing is not None:
            validate_existing(existing)
'''+s[i:]
 start=s.index('    async def record_run_alarm(');i=s.index('        await self.upsert_comment(',start)
 s=s[:i]+'''        def validate_existing(stored: TrackerComment) -> None:
            self._parse_alarm_comment(
                stored=stored, subject=alarm.subject, signal=alarm.signal
            )

'''+s[i:]
 end=s.index('    async def read_run_alarm(',start)
 block=s[start:end].replace('await self.upsert_comment(', 'await self._upsert_comment(').replace('            holder=holder,','            holder=holder,\n            validate_existing=validate_existing,')
 s=s[:start]+block+s[end:]
 start=s.index('    async def read_run_alarm(');i=s.index('        try:',start)
 s=s[:i]+'''        return self._parse_alarm_comment(stored=stored, subject=subject, signal=signal)

    def _parse_alarm_comment(
        self, *, stored: TrackerComment, subject: AlarmSubject, signal: AlarmSignal
    ) -> RunAlarm:
        """Decode one actual native snapshot, preserving a typed protocol refusal."""
'''+s[i:]
 p.write_text(s)
for file in ('domain/run_shape.py','domain/mandate_graph.py','services/run_shape.py','services/mandate_graph.py'):
 p=root/'src/kodezart'/file;s=p.read_text().replace('_read_value','read_alarm_value')
 if file=='domain/run_shape.py':s=s.replace(') -> T:\n    value = reading.value',') -> T:\n    """Extract a typed projection or refuse with its observed source identity."""\n    value = reading.value')
 p.write_text(s)
p=root/'tests/tracker/test_escalation_record_reader.py';s=p.read_text().replace('        "escalation_comment.body.partition",\n','').replace('        "json.dumps",\n','');p.write_text(s)
