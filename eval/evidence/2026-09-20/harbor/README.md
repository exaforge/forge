# Harbor pilot

Executed on 2026-09-20 in the host's America/New_York timezone, using Harbor
0.23.0 and native Linux ARM64 containers on Docker Desktop. The release binary
was built with Rust 1.94.0, protoc 29.3, and generic ARM CPU targeting. Its
[build manifest](forge.build.json) records source revision `ff916cf`, admitted
source digest, recipe hashes, and binary checksum. Subsequent evaluation-script
fixes did not change the binary's build inputs.

The unmodified official [hello-world task](https://github.com/harbor-framework/harbor/tree/1e5c5c6db929a10a140d05e606882c671ae20729/examples/tasks/hello-world)
and Terminal-Bench 2 [cancel-async-tasks task](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6/cancel-async-tasks)
were pinned to clean checkouts. [Task source identities](task-sources.json) and
the before/after task-file digests in every report preserve that provenance.
The coding task tests bounded async concurrency and cleanup on cancellation.

Both oracles passed before Forge ran: [hello-world oracle](hello-world-oracle.json)
and [coding-task oracle](cancel-async-tasks-oracle.json). They validated the task
images and verifier plumbing. They ran while Forge compiled, so their timings
are not inference-performance observations. All Forge attempts ran after the
build, one at a time in fresh containers, without retries.

| Task / profile | Verified pass | Forge invocation | Full Harbor trial | Input incl. cache | Cached subset | Output | Requests |
|---|---:|---:|---:|---:|---:|---:|---:|
| hello-world / four-switch | 1/1 | 9.191 s | 49.856 s | 6,079 | 5,376 | 87 | 3 |
| cancel-async-tasks / previous | 1/1 | 62.713 s | 107.739 s | 12,978 | 5,760 | 2,458 | 5 |
| cancel-async-tasks / four-switch | 1/1 | 44.740 s | 89.554 s | 12,230 | 3,584 | 1,710 | 5 |

The previous profile is `forge-optimized`; the four-switch profile is
`forge-coding-v2`. Both coding attempts used the same Linux binary, GPT-5.6 Sol,
medium reasoning, a 30-turn ceiling, and an 880-second inner deadline within the
task's 900-second Harbor agent budget. Adaptive reasoning was excluded. Harbor
used the task-defined one CPU and 2 GiB memory. Nested Forge sandboxing was off
inside the Harbor container. Setup and verifier time are separate from Forge's
process-launch-through-cleanup measurement.

The four-switch coding attempt took 28.7% less Forge wall time and reported 5.8%
less full input, but also generated 30.4% less output. Uncached input increased
from 7,218 to 8,646 tokens. Reported output includes reasoning: reasoning tokens
fell from 1,476 to 653 despite the same medium effort, while non-reasoning output
rose from 982 to 1,057. This was not a shorter answer or fewer model rounds.
These are observations from one sequential pair
(previous first), not an isolated optimization effect: cache state and generated
solutions were uncontrolled, and all four switches changed together. This is one
selected task, not a Terminal-Bench score or an advantage over Codex/Claude.
The [five-task local check](../coding-v2.md) was slower overall with the same
bundle; both outcomes are retained. The switches remain opt-in.

## Native request observations

| Coding profile | Visible-text TTFT (samples) | First generated content | Whole-request output rate | Generation-window output rate |
|---|---:|---:|---:|---:|
| Previous | 2.357 s (1) | 6.102 s | 36.2 tok/s | 113.0 tok/s |
| Four-switch | 2.342 s (1) | 3.687 s | 39.1 tok/s | 82.5 tok/s |

Entries are medians across observed model attempts in the one invocation. Both
coding runs had five first-generation and output-rate samples; only one attempt
per run produced visible text. First generated content can be reasoning, text,
or a tool delta. Rates use reported output tokens, including reasoning, and
client-observed windows. They do not measure server decode speed. The differing
output lengths and windows make these rates unsuitable for a general throughput
claim.

Median request-build durations were 0.039/0.061 ms for previous/four-switch;
sampler preparation medians were 1.007/1.221 ms. These are overlapping client
phase observations, not a decomposition of total wall time or CPU usage. Full
Harbor trial time also includes environment and agent setup, verification, and
cleanup; it must not be substituted for native inference or Forge invocation
time.

Every Forge attempt had reward one, exit zero, normal protocol completion,
unchanged task files, final usage for every observed sampler attempt, and healthy
collector summaries without reported loss, duplicates, or retries. The reduced
Harbor export preserves per-attempt numeric observations and fixed error
categories, but omits event IDs, request-start records, collector footer rows,
and request-context counters. Pairing and collection health were checked by the
shared parser; they cannot be independently reconstructed from the reduced
reports. Individual optimization activation is also not established here.

Input includes cached input once; cache and reasoning are subsets of input and
output respectively. Usage covers the headless prompt ledger and observed
sampler calls, excluding auxiliary direct calls. Subscription dollar cost is
unavailable. Each Forge report contains the selected profile, build identity,
and adapter/parser/launcher hashes. The two earlier oracle reports predate the
launcher-hash addition; their harness source is recorded at `ff916cf`.

Retained reports: [Forge smoke](hello-world-forge-coding-v2.json),
[previous coding profile](cancel-async-tasks-forge-optimized.json), and
[four-switch coding profile](cancel-async-tasks-forge-coding-v2.json).
No raw prompts, code, tool output, provider messages, credentials, or host mount
paths are retained. Raw Harbor job directories were deleted. See the
[integration instructions and credential-mount limitation](../../../harbor/README.md)
before reproducing this trusted-task pilot.
