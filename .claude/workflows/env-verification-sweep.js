export const meta = {
  name: 'env-verification-sweep',
  description: 'Verify the Jetson environment against the Overwatch pin table and build-order invariant, using a captured device log. Produces a pass/fail report with remediation.',
  phases: [
    { title: 'Verify', detail: 'check captured device env output vs the pin table' },
    { title: 'Report', detail: 'pass/fail + remediation pointing at jetson-env-setup' },
  ],
}

// DEVICE DEPENDENCY: the dev host can't reach the Jetson. Run
// `scripts/target/30_verify_env.sh` (and optionally 00_verify_jetpack.sh) ON THE
// DEVICE, capture stdout to a file, and pass its path as args, e.g.
//   args = "C:/tmp/verify_env.log"
// Without a log, this workflow explains how to produce one.

const logPath = (typeof args === 'string' && args.trim()) ? args.trim() : null

phase('Verify')
if (!logPath) {
  log('No device log provided.')
  return {
    status: 'needs-input',
    instructions:
      'On the Jetson run: bash scripts/target/30_verify_env.sh > verify_env.log 2>&1 ' +
      '(and bash scripts/target/00_verify_jetpack.sh >> verify_env.log), copy the log to the ' +
      'dev host, then re-run this workflow with args set to the log path.',
  }
}

const check = await agent(
  `Read the captured Jetson environment log at "${logPath}". Read docs/SOFTWARE_STACK.md for the authoritative version pins (JetPack 5.1.6 / L4T 35.6.4, CUDA 11.4, cuDNN 8.6, TensorRT 8.5, Python 3.8, torch ~2.1 Jetson wheel, ZED SDK / pyzed for 5.1.x). Compare the log's reported versions against the pins. Also verify the BUILD-ORDER invariant held: pyzed/ZED SDK present AND torch present with CUDA available (torch installed after ZED). Classify each component as ok / mismatch / missing.`,
  {
    schema: {
      type: 'object',
      properties: {
        components: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              name: { type: 'string' },
              expected: { type: 'string' },
              found: { type: 'string' },
              status: { enum: ['ok', 'mismatch', 'missing'] },
            },
            required: ['name', 'status'],
          },
        },
        build_order_ok: { type: 'boolean' },
        overall: { enum: ['pass', 'fail'] },
      },
      required: ['components', 'overall'],
    },
  },
)

phase('Report')
const bad = (check?.components || []).filter((c) => c.status !== 'ok')
if (check?.overall === 'pass' && bad.length === 0 && check?.build_order_ok !== false) {
  log('Environment PASS — matches the pin table.')
  return { status: 'pass', detail: check }
}

const remediation = await agent(
  `The Overwatch Jetson environment check found issues: ${JSON.stringify(bad)} (build_order_ok=${check?.build_order_ok}). Using the jetson-env-setup skill and docs/SOFTWARE_STACK.md, give concrete remediation steps in the correct build order (ZED SDK before PyTorch). Be specific about which scripts/target/*.sh to run.`,
  { schema: { type: 'object', properties: { steps: { type: 'array', items: { type: 'string' } } }, required: ['steps'] } },
)
log(`Environment FAIL — ${bad.length} issue(s).`)
return { status: 'fail', detail: check, remediation: remediation?.steps }
