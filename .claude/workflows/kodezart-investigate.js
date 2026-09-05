export const meta = {
  name: 'kodezart-investigate',
  description: 'Fan out one read-only investigator per question; merge evidence with a counted fan-in',
  phases: [{ title: 'Investigate' }],
}

const EVIDENCE = {
  type: 'object',
  required: ['question', 'answered', 'evidence'],
  properties: {
    question: { type: 'string', description: 'The question as dispatched' },
    answered: { type: 'boolean', description: 'False when nothing conclusive was found' },
    evidence: { type: 'string', description: 'file:line quotes or first-party URLs; on unanswered, what was searched' },
  },
}

// Measured: the Workflow tool hands `args` over as a JSON-encoded string.
const input = typeof args === 'string' ? JSON.parse(args) : (args ?? {})

const items = (input.repo_questions ?? []).map(q => ({ q, type: 'explorer' }))
  .concat((input.external_claims ?? []).map(q => ({ q, type: 'doc-verifier' })))

if (items.length === 0) {
  throw new Error(
    'kodezart-investigate: no questions to investigate — pass repo_questions and/or external_claims. ' +
    'A run that dispatches nobody would report 0/0 answered, which reads as settled.',
  )
}

phase('Investigate')
const results = await parallel(items.map(({ q, type }) => () =>
  agent(q, { agentType: type, label: q.slice(0, 60), schema: EVIDENCE })))

const findings = items.map(({ q }, i) =>
  results[i] ?? { question: q, answered: false, evidence: 'agent returned no result' })
const unanswered = findings.filter(f => !f.answered).length
log(`${findings.length - unanswered}/${findings.length} questions answered`)
return { findings, dispatched: items.length, unanswered }
