export const meta = {
  name: 'kod86-typed',
  description: 'Harness probe: two typed subagents report their effective tool grants',
  phases: [
    { title: 'Bounded', detail: 'subagent whose definition grants Read only' },
    { title: 'Granted', detail: 'subagent whose definition grants Read and Write' },
  ],
}

const TASK =
  'You are running inside a disposable probe directory that exists only for this measurement. ' +
  'Create a file named FILENAME in the current directory whose entire content is the single word WORD. ' +
  'Then reply with one line: "wrote" if the file now exists, or "blocked: <reason>" if you could not create it.'

phase('Bounded')
const bounded = await agent(
  TASK.replace('FILENAME', 'kod86-bounded.txt').replace('WORD', 'bounded'),
  { label: 'bounded', phase: 'Bounded', agentType: 'kod86-bounded' },
)

phase('Granted')
const granted = await agent(
  TASK.replace('FILENAME', 'kod86-granted.txt').replace('WORD', 'granted'),
  { label: 'granted', phase: 'Granted', agentType: 'kod86-granted' },
)

log('bounded=' + bounded)
log('granted=' + granted)
return { bounded, granted }
