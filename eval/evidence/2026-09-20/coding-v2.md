# Four-switch coding check

On 2026-09-20, one attempt on each of the five existing coding tasks compared
`forge-optimized` (the previous local-coding profile and context fast path) with
`forge-coding-v2` (that profile plus all four new switches). Both used the same
native release binary built from `e4d3596`, GPT-5.6 Sol, and medium reasoning.
Adaptive reasoning was excluded. This is a ten-attempt smoke comparison, not an
isolated estimate of each optimization's effect. Codex and Claude were not rerun.

| Observed metric | Previous profile | Four-switch profile |
|---|---:|---:|
| Verified tasks | 5/5 | 5/5 |
| Total task wall time | 225.6 s | 382.0 s |
| Median task wall time | 36.2 s | 46.0 s |
| Total input tokens, including cache | 111,448 | 124,288 |
| Cached input subset | 57,472 | 29,184 |
| Total output tokens | 7,364 | 8,670 |
| Model requests | 31 | 32 |
| Median first-request input tokens | 2,522 | 2,344 |
| Advertised tool-description bytes | 2,731 | 2,083 |
| Pooled median visible-text TTFT | 2.785 s (5 samples) | 1.528 s (5 samples) |
| Pooled median first generated content | 3.496 s | 2.645 s |
| Pooled median whole-request output rate | 32.6 tok/s | 36.4 tok/s |
| Pooled median generation-window output rate | 71.5 tok/s | 63.6 tok/s |

All attempts passed their independent checks, but the combined profile was slower
overall and used more total input and output tokens in this check. It remains
opt-in. Smaller descriptions and first-request input do not establish a total
task improvement. Task paths, request counts, output lengths, and cache hits can
change between attempts; one run per task cannot identify causes or variance.

The four-switch exploration run took 217.347 seconds, including one completed
158.256-second model attempt. Its first generated content arrived at 42.864
seconds. There were no retries. This observation is retained in every aggregate;
client telemetry cannot establish whether provider scheduling, model work, or
another factor caused the long request. The other four-switch tasks ranged from
31.967 to 47.190 seconds. See [every task and attempt](coding-v2/RESULTS.md).

The shorter descriptions were observed on requests (2,731 to 2,083 bytes, 23.7%
smaller). The conservative repeated-read reducer replaced zero results on these
small tasks, so this slice provides no live evidence of its token savings. Unit
tests cover its activation and preservation rules. The combined experiment does
not isolate read batching or output-budget effects.

All 63 observed model attempts had complete final usage; every run had a healthy
collector and complete sampler coverage. Native observations cover the sampler
actor; protocol usage covers the headless prompt ledger. Auxiliary calls remain
outside these stated boundaries. Wall time spans process launch through process
group cleanup, excluding workspace setup and independent verification. TTFT and
rates are client observations, not server decode measurements. Most tool-only
requests have no visible-text TTFT. Rates use provider-reported output tokens and
the observed windows, which can include reasoning. Phase intervals overlap.

The median run spent about 94.0% of task wall time inside model-request intervals
for both profiles. Median per-run request-build medians were 0.060 and 0.072 ms;
sampler-prepare medians were 0.745 and 0.761 ms. These observations do not indicate
that request preparation is the dominant latency target for these tasks, and the
remaining wall time is not a pure measurement of Forge CPU overhead.

Retained manifests, results, and allowlisted events are in [coding-v2](coding-v2/).
The aggregate is [coding-v2-aggregate.json](coding-v2-aggregate.json), reproducible
without model calls using `python3 eval/evidence/2026-09-20/summarize-coding-v2.py`.
The earlier 45-attempt snapshot is unchanged.
