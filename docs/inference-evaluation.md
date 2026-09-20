# Inference efficiency, Forge overhead, and evidence

Forge's evaluation path connects an explicit configuration to a fresh task
workspace, observed execution, and an independent outcome check. See
[the runner](../eval/README.md) for commands and the five small workloads.

## What is measured

| Measurement | Boundary and interpretation |
|---|---|
| Task wall time | Parent runner's monotonic clock, immediately before process launch through process-group cleanup. Includes startup and shutdown; excludes fixture setup, output draining/parsing, and independent verification. Leader exit and cleanup are also recorded separately. |
| Request/attempt duration | Native sampler client boundary, including request initialization, HTTP connection/headers, and stream consumption. Retries are separate attempts. This includes network and provider queueing. |
| First response event | First observed provider stream event; this may be metadata, not generated content. |
| TTFT (text) | Request attempt start to first nonempty visible text delta. Reasoning and tool deltas have separate first-event times. Tool-only responses have no text TTFT. |
| Observed output rate | Final provider-reported output tokens divided by the stated client-observed interval. The full-attempt rate includes initial waiting; the generation-window estimate starts at the first observed generated content and requires at least two meaningful content events. Buffered output/reasoning can distort the estimate. Neither measures server-only decode speed. |
| Usage | Reported input and output totals for observed attempts. Cached input is a subset of input; reasoning is a subset of output. Subsets are never added again. Missing usage is unavailable. |
| Forge phases | Request building, sampler preparation, stream draining, tool preparation and tool dispatch. These are elapsed intervals, including actor/permission/lock waits where applicable, not CPU measurements. |
| Verified outcome | Fixed checks executed after the agent exits. Process exit, protocol completion, and verifier success are recorded separately. |

Streaming chunks can contain multiple tokens, and providers can buffer them.
Chunk counts and spacing must not be described as token counts or inter-token
latency. Different toolsets, prompts, models, reasoning settings, and provider
tiers make these comparisons of whole configurations. A faster failed
task is not an improvement in successful task completion.

JSONL timings are separate from existing TUI/headless latency metrics, which
remain unchanged and can use different start/event boundaries. Do not treat
those metrics as equivalent TTFT. `requested_config` describes the sampler's
inputs, not every effective wire option: provider adapters can remove options,
and retry policy can resolve defaults later.
The `process_body` phase begins after CLI parsing and ends after runtime
shutdown, before final log/observation flushing. Use the parent's task wall time
for complete process accounting.

## Local observations

Set `FORGE_OBSERVATION_FILE` to an absolute JSONL filename whose parent exists,
and optionally `FORGE_RUN_ID` to an experiment identifier. These explicitly
enable a local collector; nothing is uploaded by this feature. Existing tracing,
session persistence, and provider telemetry retain their own settings.

Schema version 1 records native request/attempt lifecycle events, selected
`phase_start`/`phase_end` intervals, and an `observation_health` footer. Each
process has its own monotonic origin; correlate its timestamps only within that
process. Request, attempt, session, prompt, and tool call identities are present
where the corresponding layer knows them. Provider model/configuration metadata
and token counts are retained; prompts, source, tool arguments/results, raw
provider payloads, credentials, URLs, and error messages are not.

The collector uses a bounded queue and a writer thread. Full queues, oversized
records, and I/O failures do not change inference outcomes. A bounded shutdown
flush reports cumulative loss counters. A missing footer, nonzero losses,
unclosed attempt, or interrupted process means observation coverage is partial.
The runner retains the execution failure rather than turning absent metrics
into zeroes.

**Coverage is deliberately limited:** the first implementation observes calls
through the native sampler actor and selected embedded headless phases. Direct
auxiliary clients, external harness internals, provider-side scheduling, and
server compute may be unobserved. Usage for observed attempts is not necessarily
the account's total usage. An observed failed attempt without a final usage
report may still have consumed tokens. Subscription usage is not converted to
dollar cost.

Parallel tools and nested phases overlap. Do not subtract the sum of their
durations from wall time or call the remainder “Forge overhead.” Compare
individual phase durations and their union within an explicitly stated scope.
Any uninstrumented remainder remains unclassified.

## Controlled efficiency experiments

Both environment switches are opt-in, and only the literal value `1` enables
them. Keep the same binary, model, effort, tools, permissions, and limits when
isolating either switch.

* `FORGE_CONTEXT_FAST_PATH=1`: for image-free histories, calculate the same JSON
  byte count without cloning the conversation first. Images retain the existing
  compaction and byte-budget path. No context is removed or reordered. A local
  microbenchmark isolates this preparation cost; it does not establish a task
  wall-time speedup.
* `FORGE_PROMPT_CACHE=1`: for Responses backends and valid UUID conversation
  identities, send a stable namespaced session key. This is a cache-group
  experiment, not a demonstrated speed optimization. GPT-5.6 and later route
  automatically; keys separate accounting/reuse groups and can reduce reuse
  across sessions. Older models may use keys for routing. See the
  [OpenAI prompt-caching guide](https://developers.openai.com/api/docs/guides/prompt-caching).
  Subscription endpoints may differ from the Platform API. Other backends and
  arbitrary conversation identifiers leave the key unset. The primary comparison
  leaves this experiment off.
* The evaluator's local-coding tool scope uses Forge's **existing** `--tools`
  allowlist to omit scheduling, monitoring, external-app discovery and delegation
  definitions from repository-only tasks. It keeps the normal agent and project
  instructions, file operations, search, editing and terminal lifecycle tools.
  This changes model-visible capabilities, so success checks are essential. It
  is a configuration optimization, not a new inference algorithm. The combined
  profile compares whole configurations; `forge-local-tools-only` and
  `forge-context-only` support separate ablations.

Only enable a switch by default after repeatable evidence of benefit without
outcome regressions. Context selection and adaptive reasoning effort also
change what the model receives or generates and need a larger quality
evaluation before becoming defaults.

## Evidence policy

Use fresh sessions/workspaces, fixed fixture and verifier identities, explicit
limits, and counterbalanced order. Retain run manifests, metadata events,
summaries, and failures. Small samples support exploratory observations, not a
general claim that Forge is faster than Codex or Claude Code. Compare native
Forge configurations first, direct Codex with the same accessible model next,
and Claude Code as a separately labeled configuration when authenticated.

External TTFT or generation throughput remains unavailable if the CLI does not
expose trustworthy event boundaries. Time to a final message is not substituted
for TTFT. A README table can summarize results, but it must link to the evidence
and state the workload, sample size, build profile, and measurement limitations.

The [2026-09-20 evaluation snapshot](../eval/evidence/2026-09-20/README.md)
contains the first 45 trials, component measurements and retained metadata.
