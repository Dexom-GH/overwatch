export const meta = {
  name: 'adr-fanout',
  description: 'Resolve an open Overwatch design decision: fan out agents to argue competing positions on an ADR, judge them, and synthesize a recommendation back into the ADR.',
  phases: [
    { title: 'Positions', detail: 'one advocate per option argues its case' },
    { title: 'Judge', detail: 'independent judges score the arguments' },
    { title: 'Synthesize', detail: 'recommendation + ADR update draft' },
  ],
}

// Usage: pass the ADR number or path as args, e.g. args = "0002" or
// "docs/DECISIONS/0002-zed-deepstream-integration.md". Defaults to 0002.
const adrArg = (typeof args === 'string' && args.trim()) ? args.trim() : '0002'

phase('Positions')
// First, read the ADR and enumerate its options.
const parsed = await agent(
  `Read the Overwatch ADR identified by "${adrArg}" under docs/DECISIONS/ (match by number prefix or path). Also read CLAUDE.md, docs/ARCHITECTURE.md, docs/HARDWARE.md and docs/SOFTWARE_STACK.md for constraints. Return the ADR's file path, its title, current status, and the list of options it considers.`,
  {
    schema: {
      type: 'object',
      properties: {
        path: { type: 'string' },
        title: { type: 'string' },
        status: { type: 'string' },
        options: { type: 'array', items: { type: 'string' } },
      },
      required: ['path', 'title', 'options'],
    },
  },
)

const options = (parsed?.options || []).filter(Boolean)
if (options.length < 2) {
  log(`ADR ${adrArg} has fewer than 2 options to debate (status: ${parsed?.status}).`)
  return { note: 'nothing to fan out', adr: parsed }
}
log(`Debating ${options.length} options for: ${parsed.title}`)

const positions = await parallel(
  options.map((opt) => () =>
    agent(
      `You are an advocate for ONE option on Overwatch ADR "${parsed.title}" (${parsed.path}). Argue the strongest possible case FOR this option: "${opt}". Ground it in the project's real constraints (Jetson Xavier NX ~21 TOPS, JetPack 5.1.x, ZED depth as differentiator, V1 timeline, the hybrid/on-demand patterns). Be concrete about what code it shapes. Give pros, the hardest counter-argument against you, and how you'd mitigate it.`,
      { label: `advocate:${opt.slice(0, 24)}`, phase: 'Positions',
        schema: {
          type: 'object',
          properties: {
            option: { type: 'string' },
            case_for: { type: 'string' },
            hardest_counter: { type: 'string' },
            mitigation: { type: 'string' },
          },
          required: ['option', 'case_for'],
        },
      },
    ),
  ),
)

phase('Judge')
const valid = positions.filter(Boolean)
const verdicts = await parallel(
  ['correctness/risk', 'V1 delivery speed', 'long-term maintainability'].map((lens) => () =>
    agent(
      `Judge these competing positions on Overwatch ADR "${parsed.title}" strictly through the lens of ${lens}. Positions:\n${JSON.stringify(valid, null, 2)}\nRank the options best-to-worst for this lens and justify briefly.`,
      { label: `judge:${lens}`, phase: 'Judge',
        schema: {
          type: 'object',
          properties: {
            lens: { type: 'string' },
            ranking: { type: 'array', items: { type: 'string' } },
            rationale: { type: 'string' },
          },
          required: ['ranking'],
        },
      },
    ),
  ),
)

phase('Synthesize')
const rec = await agent(
  `Synthesize a recommendation for Overwatch ADR "${parsed.title}" (${parsed.path}). Inputs:\nPOSITIONS: ${JSON.stringify(valid)}\nJUDGE VERDICTS: ${JSON.stringify(verdicts.filter(Boolean))}\nRecommend one option (or a hybrid), note what would change the call, and draft the "Decision" + "Consequences" text to paste into the ADR. Do NOT write the file — return the draft so a human can apply it.`,
  {
    schema: {
      type: 'object',
      properties: {
        recommended_option: { type: 'string' },
        why: { type: 'string' },
        decision_text: { type: 'string' },
        consequences_text: { type: 'string' },
        revisit_if: { type: 'string' },
      },
      required: ['recommended_option', 'decision_text'],
    },
  },
)
log(`Recommendation: ${rec?.recommended_option}`)
return { adr: parsed.path, recommendation: rec, verdicts: verdicts.filter(Boolean) }
