export const meta = {
  name: 'model-convert-benchmark',
  description: 'Drive the MegaDescriptor Swin->TRT FP16 conversion and on-device benchmark, then record latency/throughput numbers and flag where they diverge from the V1 estimates.',
  phases: [
    { title: 'Plan', detail: 'assemble the conversion + benchmark command plan' },
    { title: 'Record', detail: 'parse captured device results and record them' },
  ],
}

// This workflow attacks the "all latency figures are estimates" risk by turning
// conversion+benchmark into a repeatable, recorded loop.
//
// DEVICE DEPENDENCY: conversion and benchmarking run ON THE JETSON. Run them
// there (the Plan phase tells you exactly what), capture the output to a file,
// and re-run this workflow with args = that log path to record results.

const resultsLog = (typeof args === 'string' && args.trim()) ? args.trim() : null

if (!resultsLog) {
  phase('Plan')
  const plan = await agent(
    `Produce the on-device command plan to convert and benchmark MegaDescriptor for Overwatch. Use the trt-model-conversion skill, scripts/target/40_convert_megadescriptor.sh, and docs/SOFTWARE_STACK.md (TensorRT 8.5, FP16). The plan must: (1) build models/megadescriptor_t224_fp16.engine, (2) validate the engine produces an embedding of expected dim, (3) benchmark single-crop embed latency (ms, mean/p95) and throughput (embeds/sec) on the Xavier NX, and (4) capture all output to a log file. Give exact commands the operator runs on the device.`,
    { schema: { type: 'object', properties: { commands: { type: 'array', items: { type: 'string' } }, capture_hint: { type: 'string' } }, required: ['commands'] } },
  )
  log('Conversion+benchmark plan ready — run on device, then re-run with the log path as args.')
  return { status: 'needs-device-run', plan }
}

phase('Record')
const parsed = await agent(
  `Read the captured MegaDescriptor conversion+benchmark log at "${resultsLog}". Extract: engine built (yes/no), embedding dimension, single-crop latency (mean ms, p95 ms), throughput (embeds/sec), and any FP16-vs-FP32 similarity number. Then compare against the V1 assumption that ReID is cheap enough to run ON-DEMAND (not per-frame) without stalling the DeepStream pipeline — flag if the measured latency looks too high for the on-demand trigger pattern (ADR-0003).`,
  {
    schema: {
      type: 'object',
      properties: {
        engine_built: { type: 'boolean' },
        embedding_dim: { type: 'number' },
        latency_mean_ms: { type: 'number' },
        latency_p95_ms: { type: 'number' },
        throughput_per_s: { type: 'number' },
        fp16_similarity: { type: 'number' },
        diverges_from_estimate: { type: 'boolean' },
        notes: { type: 'string' },
      },
      required: ['engine_built', 'notes'],
    },
  },
)

const recorder = await agent(
  `Draft a Markdown entry for docs/BENCHMARKS.local.md (gitignored) recording this MegaDescriptor benchmark: ${JSON.stringify(parsed)}. Include the date placeholder <DATE>, device (Jetson Xavier NX, JetPack 5.1.6, TRT 8.5), and a one-line takeaway about on-demand ReID feasibility. Return the markdown only — do not write the file (a human appends it).`,
  { schema: { type: 'object', properties: { markdown: { type: 'string' } }, required: ['markdown'] } },
)
log(`Recorded benchmark — diverges from estimate: ${parsed?.diverges_from_estimate}`)
return { status: 'recorded', results: parsed, benchmark_entry: recorder?.markdown }
