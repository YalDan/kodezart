// Harness that executes a repo-owned workflow script the way the workflow
// runtime does, so a test can observe its fan-in rather than a Python
// re-implementation of it.
//
// A workflow body is not an ES module: it declares `export const meta` AND
// ends in a top-level `return`, which no module or script scope accepts.
// The runtime therefore evaluates the body as a function body with the
// workflow globals supplied as parameters; this harness does the same and
// hoists the `export` keyword off the top-level declarations, which is the
// only edit it makes to the shipped text.
//
// Usage: node run_workflow.mjs <workflow.js>  < scenario.json
// Scenario: { "args": {...}, "results": [ {...} | null, ... ] }
// Prints:   { "report": ..., "dispatches": [...], "log": [...] }

import { readFileSync } from 'node:fs'

const source = readFileSync(process.argv[2], 'utf8')
const scenario = JSON.parse(readFileSync(0, 'utf8'))

const dispatches = []
const lines = []
let dispatched = 0

const agent = async (prompt, options = {}) => {
  const index = dispatched++
  dispatches.push({ prompt, options })
  return scenario.results[index] ?? null
}
const parallel = thunks => Promise.all(thunks.map(thunk => thunk()))
const phase = title => lines.push(`phase:${title}`)
const log = message => lines.push(String(message))

const body = source.replace(/^export\s+/gm, '')
const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor
const run = new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body)

const report = await run(scenario.args ?? {}, agent, parallel, phase, log)
process.stdout.write(JSON.stringify({ report, dispatches, log: lines }))
