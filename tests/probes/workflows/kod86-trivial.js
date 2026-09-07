export const meta = {
  name: 'kod86-trivial',
  description: 'Harness probe: one untyped subagent returns a fixed word',
  phases: [
    { title: 'Probe', detail: 'one subagent dispatch' },
  ],
}

phase('Probe')
const answer = await agent(
  'Reply with exactly the word pear and nothing else.',
  { label: 'probe', phase: 'Probe' },
)
log('probe agent returned')
return { answer }
