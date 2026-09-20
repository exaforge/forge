# Local evaluation — 2026-09-20

The scoped Forge configuration reduced total wall time by **39.7%** and reported
input tokens by **74.7%** relative to baseline Forge in this small task set. It
passed **14/15** tasks; baseline Forge and Codex each passed **15/15**. Codex used
less total wall time than scoped Forge. The candidate remains opt-in; this is
evidence for further engineering, not a general performance ranking or proof of
faster provider decoding.

## Configuration and scope

The comparison ran from approximately 22:07 to 22:48 UTC on an Apple M4 Pro,
macOS 26.6.2 / Darwin 25.6.0, using Python 3.12.11. Forge was built in release
mode with Rust 1.94.0 and the repository's protoc 29.3. No compilation ran during
the measured comparison.

All three profiles requested `gpt-5.6-sol` with medium reasoning. Each of the
five public tasks ran three times per profile, serially, with positions rotated
so each profile occupied every position once per task. Each attempt had a fresh
workspace and session, a 600-second timeout, protected supplied tests, and the
same independent verifier. Native Forge had a 20-turn limit; Codex has no
equivalent CLI flag. These are single headless invocations containing multiple
model/tool rounds, not resumed multi-turn sessions.

* **Forge baseline:** context fast path off, session cache-key experiment off,
  existing shared HTTP client on, memory/subagents/web search disabled.
* **Forge optimized:** the same binary/model/effort and permission settings,
  context fast path on, plus the existing local-coding tool allowlist. The
  actual advertised tool count fell from 23 to 6. Project instructions and the
  normal tool implementations remained in use. Scheduling, delegation and
  external-tool discovery were outside this profile's intended scope.
* **Codex direct:** bundled CLI `0.155.0-alpha.9.2`, ephemeral execution,
  workspace-write sandbox, user config/rules ignored, plugins/apps/multi-agent
  disabled. The installed Homebrew CLI `0.133.0` rejected the selected model in
  a preflight check; it was not used in this comparison. Setup/smoke runs are
  outside these 45 trials.

Both Forge profiles used executable SHA-256
`62c7e2ddbaf7c4ce27ebce1434965ceb79aade0fc6af3f337864319a616338af`.
The binary reported `grok 1.0.0 (aff540e)` and was built from the uncommitted
`dev` changes on base revision `aff540e8df2671c0f1488fc22c5ccf26f28271e3`.
Manifests retain binary/configuration/source digests; this baseline is the
instrumented binary with experiment switches off, not a historical release.

Provider cache state, queueing, quota and effective service tier were not
controlled. Forge requested no Fast Mode. Host skill discovery and managed
settings remained active; fresh Forge homes do not isolate those sources.
External global stores also remain outside complete isolation. Different
system prompts and tools make this a comparison of configurations. Claude Code
was not run because its CLI was not authenticated at setup.

## Outcomes and elapsed time

Every scheduled attempt is retained. Task wall time covers process launch
through process-group cleanup; fixture preparation, output parsing/draining and
independent verification are outside that interval. Totals below include the
failed run. The pooled median mixes five tasks; use the per-task rows and total
wall time to avoid mistaking a favorable pooled median for a consistent lead.

| Configuration | Verified | Total wall | Median task wall | Full reported input | Cached subset | Reported output |
|---|---:|---:|---:|---:|---:|---:|
| Forge baseline | 15/15 | 1,116.669 s | 67.037 s | 1,282,787 | 727,552 | 31,486 |
| Forge optimized | 14/15 | 673.445 s | 39.091 s | 324,661 | 124,288 | 22,378 |
| Codex direct | 15/15 | 639.817 s | 43.766 s | 1,238,452 | 1,031,680 | 24,111 |

Input includes cache; output includes any reported reasoning. Subsets are not
added twice. These are reported scopes, not complete account usage or charges.

| Task | Baseline median / passes | Optimized median / passes | Codex median / passes |
|---|---:|---:|---:|
| Bug fix | 60.858 s / 3/3 | 33.881 s / 3/3 | 30.824 s / 3/3 |
| Multi-file feature | 67.418 s / 3/3 | 48.233 s / 3/3 | 39.479 s / 3/3 |
| Test failure diagnosis | 67.037 s / 3/3 | 45.536 s / 2/3 | 44.983 s / 3/3 |
| Exploration + change | 102.631 s / 3/3 | 62.931 s / 3/3 | 58.732 s / 3/3 |
| Refactor with tests | 60.735 s / 3/3 | 33.444 s / 3/3 | 44.390 s / 3/3 |

The [failed optimized diagnosis run](comparison/487e99fb-614c-4b7a-bcf3-0d089a00984f/result.json)
exited normally and completed its protocol, but passed only 17 of 20 independent
checks. Its supplied tests were unchanged and verifier integrity was preserved.
Generated source is discarded, and this verifier stores counts rather than
failed-case identifiers, so the exact behavioral defect cannot be reconstructed
from the retained metadata. One failure in 15 attempts does not establish a
general quality regression, but it prevents claiming unchanged quality.

## Native inference and Forge phases

There were 244 observed native requests/attempts: 154 baseline and 90 optimized.
All 30 native runs had healthy final collector footers, final usage, paired
boundaries, no discarded records, and no reported loss or retries. Native usage
reconciled with each headless terminal ledger. Direct/auxiliary clients outside
the instrumented sampler remain outside this coverage.

These are pooled native request medians. Text TTFT has only 15 samples per
profile—one visible-text response per invocation. Tool-only requests have no
text TTFT. First generation also covers reasoning or tool deltas.

| Measurement | Baseline | Optimized |
|---|---:|---:|
| First-request input, median | 6,736 tokens | 2,522 tokens |
| Model requests, median per task | 10 | 6 |
| Text TTFT, median (15 samples each) | 2.397 s | 2.862 s |
| First generated content, median | 4.198 s | 2.935 s |
| Output tokens / full client attempt duration, median | 29.0 tokens/s | 33.7 tokens/s |
| Request-interval union / task wall, median per run | 95.8% | 93.9% |
| Request building, median of run medians | 0.084 ms | 0.062 ms |
| Sampler preparation, median of run medians | 1.052 ms | 0.772 ms |

Request intervals include connection/headers, network, provider waiting and
stream consumption. Their union is calculated per process from paired request
boundaries, not by adding nested or parallel phases. The remainder includes
startup, tools and other work; it is not a measurement of Forge CPU overhead.
Output rate uses provider-reported output tokens, not chunk counts, and is not
server-only decode throughput. The additional generation-window estimate in
the detailed report has buffering/hidden-reasoning limitations. Codex request
TTFT and output rates are unavailable through this adapter and remain null.

The local profile changes model-visible tools and context. Fewer/smaller model
requests are associated with the observed savings; the comparison does not
isolate tool filtering from the context fast path. Separate ablation profiles
are supplied for future experiments. No speed or cache-hit benefit is claimed
for the optional session cache-key flag, which was off throughout this campaign.

## Component checks

[Microbenchmark data](microbench.json) isolates image-free JSON body-size
calculation. Baseline/fast-path medians were 59.19/57.72 microseconds for 103 KB,
461.01/446.48 microseconds for 1.03 MB, and 4,544.96/4,466.17 microseconds for
10.31 MB. Eleven samples of ten iterations alternated order. This measured a
small local improvement, not task or inference acceleration.

The synthetic collector benchmark wrote 10,000 records without loss at about
4.12 microseconds per enabled record. It includes metadata cloning,
serialization and queueing to an in-memory writer. It excludes disk, stream
observation hooks and inference. The recorded disabled value measures only a
synthetic optional-sink branch, not the complete disabled instrumentation path.

Validation included 356 chat-state tests, 218 sampler tests, the subagent
CLI/env/config refresh regression test, 19 offline evaluator tests, and both
release microbenchmarks. The release binary built successfully. The full
workspace test suite was not run.

## Inspect or reproduce

* [Detailed task table with ranges and observation sample counts](comparison/RESULTS.md)
* [Per-task summary JSON](comparison/summary.json)
* [Aggregate calculations](aggregate.json) and [their script](summarize.py)
* [Runner setup, profile flags and limitations](../../README.md)
* [Measurement definitions](../../../docs/inference-evaluation.md)

Every linked result has sibling `manifest.json` and `events.jsonl` files.
Retained evidence contains metadata, not prompts, generated source, raw tool
output or authentication contents. The fixture and verifier definitions are
public repository source. Source-provenance hashing reads nonignored checkout
files and retains digests, not their contents.

To repeat the model comparison, prepare absolute executable paths as described
in the runner documentation, then use a new empty output directory:

```sh
python3 eval/run.py run --profiles /absolute/path/profiles.json \
  --profile forge-baseline --profile forge-optimized --profile codex-direct \
  --repeats 3 --timeout 600 --max-turns 20 \
  --output eval/results/new-comparison
```

To recompute this snapshot without model calls:

```sh
python3 eval/run.py report eval/evidence/2026-09-20/comparison
python3 eval/evidence/2026-09-20/summarize.py
```
