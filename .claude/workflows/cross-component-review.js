export const meta = {
  name: 'cross-component-review',
  description: 'Review an Overwatch change across the pipeline stages it touches, with a dedicated reviewer for the bus schema/topic contract (the highest-risk seam).',
  phases: [
    { title: 'Scope', detail: 'find which stages + the bus contract the diff touches' },
    { title: 'Review', detail: 'one reviewer per touched stage + a bus-contract reviewer' },
    { title: 'Synthesize', detail: 'merge into one prioritized review' },
  ],
}

// Reviews the current working-tree / branch diff. The message-bus contract
// (bus/schemas.py + bus/topics.py) is the seam that breaks stages silently, so
// it always gets its own reviewer when touched.

phase('Scope')
const scope = await agent(
  `You are scoping a code review for the Overwatch repo. Run \`git --no-pager diff master...HEAD --stat\` and \`git --no-pager diff master...HEAD\` (fall back to the unstaged/staged working-tree diff if HEAD == master). Determine which of these areas the diff touches: capture, inference, fusion, output, bus-contract (src/overwatch/bus/schemas.py or topics.py), scripts/target, docs. Read CLAUDE.md and docs/ARCHITECTURE.md for context.`,
  {
    schema: {
      type: 'object',
      properties: {
        touched: {
          type: 'array',
          items: { enum: ['capture', 'inference', 'fusion', 'output', 'bus-contract', 'scripts', 'docs'] },
        },
        summary: { type: 'string' },
      },
      required: ['touched', 'summary'],
    },
  },
)

const areas = (scope?.touched || []).filter(Boolean)
if (!areas.length) {
  log('No reviewable areas detected in the diff.')
  return { reviewed: [], note: 'empty or non-code diff' }
}
log(`Reviewing areas: ${areas.join(', ')}`)

phase('Review')
const reviews = await parallel(
  areas.map((area) => () =>
    agent(
      area === 'bus-contract'
        ? `Review the diff to src/overwatch/bus/schemas.py and topics.py as the MESSAGE-BUS CONTRACT reviewer. This is the highest-risk surface: a schema/topic change can break every stage. Check: backward compatibility of dataclass fields, Python 3.8 compatibility (no 3.9+ syntax), no heavy/target-only imports leaked into the contract, topic naming convention "<stage>.<noun>", and that producers/consumers across stages still agree. Follow the bus-stage-conventions skill. Report concrete findings with file:line and severity (blocker/major/minor).`
        : `Review the diff to the Overwatch \`${area}\` stage. Check correctness, that it speaks the bus contract correctly (topic constants + schema dataclasses, no bare strings), the import-guard convention for any target-only deps, Python 3.8 compatibility, and adherence to docs/ARCHITECTURE.md. Report concrete findings with file:line and severity (blocker/major/minor).`,
      { label: `review:${area}`, phase: 'Review',
        schema: {
          type: 'object',
          properties: {
            area: { type: 'string' },
            findings: {
              type: 'array',
              items: {
                type: 'object',
                properties: {
                  severity: { enum: ['blocker', 'major', 'minor'] },
                  location: { type: 'string' },
                  issue: { type: 'string' },
                },
                required: ['severity', 'issue'],
              },
            },
          },
          required: ['area', 'findings'],
        },
      },
    ),
  ),
)

phase('Synthesize')
const all = reviews.filter(Boolean)
const flat = all.flatMap((r) => (r.findings || []).map((f) => ({ ...f, area: r.area })))
const order = { blocker: 0, major: 1, minor: 2 }
flat.sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9))
log(`${flat.length} findings (${flat.filter((f) => f.severity === 'blocker').length} blockers)`)
return { scope: scope.summary, findings: flat }
