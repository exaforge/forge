# Repeated compact-description comparison

Compact tool descriptions reduced first-request input but did not improve total
task latency or token usage in this comparison. They remain opt-in. Codex was
faster overall on these tasks; the results do not establish a Forge speed
advantage, a general product ranking, or faster server-side decoding.

On 2026-09-20, the five existing Python coding fixtures ran three times under each
of three profiles: 45 serial attempts on an Apple M4 Pro. The two Forge profiles
used the same release binary, GPT-5.6 Sol, medium reasoning, six advertised tools,
and configuration. Their only configuration difference was
`FORGE_COMPACT_TOOL_DESCRIPTIONS=1`. Codex CLI used the same declared model and
effort through its existing ChatGPT sign-in, with its own harness and tools.
See the [plan recorded before measurement](compact-ablation-plan.md).

## Outcomes and usage

All 45 processes and protocols completed, and all supplied tests remained
unchanged. Totals include every attempt, including the failed verification.
Each reported protocol-usage total has complete coverage for its stated scope
on all 15 attempts per profile.

| Metric | Existing Forge | Compact descriptions only | Codex CLI |
|---|---:|---:|---:|
| Verified passes | 14/15 | 15/15 | 15/15 |
| Total task wall time | 668.5 s | 713.4 s | 599.5 s |
| Median task wall time | 40.7 s | 44.5 s | 36.2 s |
| Input tokens, including cached subset | 346,219 | 346,965 | 1,154,422 |
| Cached input subset | 127,744 | 99,968 | 938,112 |
| Input outside cached subset | 218,475 | 246,997 | 216,310 |
| Output tokens | 22,820 | 24,166 | 22,266 |
| Observed model requests | 92 | 93 | Unavailable |
| Native reasoning-token subset | 5,835 | 5,932 | Unavailable |
| Median first-request input | 2,521 | 2,306 | Unavailable |
| Advertised tool-description bytes | 2,731 | 1,851 | Unavailable |

Compared with existing Forge, compact descriptions used 6.7% more total wall time,
0.2% more total input, and 5.9% more output. Description text was 32.2% smaller,
and median first-request input was 8.5% lower (15 complete samples per profile).
That request-level saving did not translate into a task-level saving.

Codex used 10.3% less total wall time than existing Forge and 16.0% less than
compact Forge. Its much larger reported input includes much more cached input:
81.3%, versus 36.9% and 28.8% for the two Forge profiles. Input outside the cached
subset was similar for Codex and existing Forge and higher for compact Forge.
Total input alone therefore does not establish lower uncached processing or
cost. These are reported usage counts, not billed totals or dollar estimates.
Cache state, provider load, and model paths were not controlled; these observations
do not identify the cause of latency differences.

### Failure and task-contract limitation

Existing Forge's second `test-diagnosis` attempt
([retained result](compact-ablation/f4ba3cc5-35a5-4d2e-b606-5c56cd9e0a10/result.json))
passed 17/20 independent checks. Three `invalid-attempts` checks failed, while
all five checks that invalid inputs caused no callback invocation passed.
The verifier required `ValueError` for `0`, `-1`, `True`, `1.5`, and `"2"`.
The campaign prompt required positive integers and excluded bool, but only the
public test for zero explicitly demonstrated the required exception type.

This is a failure under the predefined checker, with an ambiguous exception
contract in the task. Reused labels and metadata-only retention cannot identify
the three inputs or distinguish a different exception from a normal return.
Do not infer acceptance of invalid inputs or a specific implementation bug.
The score remains unchanged; this small difference does not establish better
general correctness for compact descriptions. Future runs should use an explicit
exception contract and distinguish invalid-input cases in safe failure labels.

## Matched comparisons and task variation

Each task/repetition group contains all three profiles. Each profile occupied
each position five times, and every pair's precedence was balanced 8/7. Every
scheduled attempt is retained; no failures or slow runs were replaced.

Speed comparisons below use only pairs where both attempts passed. Relative
change is calculated within each task/repetition pair before taking the median;
it is not a ratio of aggregate medians.

| Candidate versus reference | Successful pairs | Candidate faster / slower | Median paired wall change |
|---|---:|---:|---:|
| Compact Forge versus existing Forge | 14/15 | 5 / 9 | +13.5% |
| Codex versus existing Forge | 14/15 | 8 / 6 | -7.0% |
| Codex versus compact Forge | 15/15 | 10 / 5 | -6.9% |

The excluded speed pair in each comparison with existing Forge contains its
verification failure; it remains in all-attempt totals and success denominators.
Among the 14 mutually successful Forge pairs, median paired input change was
+3.3% and output change was +11.2% for compact descriptions.

Task wall time below is the median, with minimum–maximum in parentheses, across
all three attempts. Only existing Forge's test-diagnosis row includes a failure.

| Task | Existing Forge | Compact descriptions only | Codex CLI |
|---|---:|---:|---:|
| Bug fix | 29.8 (27.8–33.7) s | 35.2 (29.6–41.6) s | 34.5 (32.1–35.0) s |
| Multi-file feature | 40.7 (40.5–43.7) s | 55.5 (37.2–59.4) s | 35.2 (33.0–36.2) s |
| Test diagnosis | 51.0 (46.6–52.3) s | 44.5 (41.6–44.8) s | 35.3 (35.3–45.5) s |
| Exploration and change | 58.3 (57.5–82.7) s | 60.1 (50.4–86.8) s | 46.1 (44.6–56.7) s |
| Refactor with tests | 32.8 (32.8–38.2) s | 39.9 (39.5–47.0) s | 43.8 (40.0–46.3) s |

## Native inference timing and Forge phases

All 185 observed Forge sampler attempts completed with final usage and healthy
collection. The following are pooled medians over recorded attempts, rather than
per-task medians. Codex request-level timing is unavailable through this adapter.

| Observation | Existing Forge | Compact descriptions only |
|---|---:|---:|
| Visible-text TTFT | 1.917 s (15 samples) | 2.142 s (15 samples) |
| First generated content | 3.351 s (92 samples) | 3.536 s (93 samples) |
| Whole-request output rate | 34.4 tok/s (92 samples) | 34.9 tok/s (93 samples) |
| Generation-window output rate | 70.9 tok/s (92 samples) | 73.0 tok/s (93 samples) |
| Request build | 0.053 ms (92 samples) | 0.055 ms (93 samples) |
| Sampler preparation | 0.613 ms (92 samples) | 0.651 ms (93 samples) |

TTFT starts at the sampler attempt, not process launch. Most requests produced
tool calls without visible text, so their text TTFT is unavailable. First
generated content can be reasoning, tool arguments, or text. Whole-request rate
includes initial wait; generation-window rate is a client approximation using
reported output tokens, including reasoning. Buffered delivery can produce very
large rates over short windows. Neither rate measures server decode speed or
establishes that compact descriptions made generation faster.

Request preparation is sub-millisecond at these medians, while model attempts
take seconds. This does not identify request preparation as the dominant latency
target in this slice. Phase intervals overlap and cannot be added into a CPU or
wall-time overhead total. Process wall time spans launch through process-group
cleanup and excludes workspace creation and independent verification. Protocol
usage covers Forge's headless prompt ledger or Codex's reported turn; native
usage covers the Forge sampler actor. Auxiliary calls outside those scopes are
not accounted for.

## Provenance, limits, and reproduction

The runner, profiles, fixture prompts, and verifier were frozen at clean checkout
`351b3ca1723c632b62df13c7fb43e1842ab6e0c7`. The native release artifact was built
earlier from `e4d3596`. Both executables were rehashed after the campaign and
matched every manifest:

- Forge: `c54d86d304462f649bde06f5d75731b648e66d220527bd3baabac0671b168c58`.
- Codex CLI `0.155.0-alpha.9.2`: `9280c0754e8f1f6b72f495d30c8c82a006dbc4995bf0492916fa0901f6bfd1f9`.

Recorded native requests all match the model, medium effort, and six-tool scope.
Codex model/effort are declared configuration, not independently observed request
metadata. Every trial used a fresh workspace/session and a 600-second wall limit.
Forge had a 20-turn ceiling; Codex lacks an equivalent flag. Harness prompts,
tools, permissions, host discovery, managed settings, account load, network, and
provider cache remain limitations of the external comparison. No compilation or
other benchmark model calls ran concurrently. Three repetitions of five small
tasks are exploratory, not statistical proof or broad quality assurance.

Claude Code was not run. The installed CLI was signed out, and Anthropic's
official gateway documentation does not support routing Claude Code to
non-Claude models. GPT-5.6 Sol would require a separately validated protocol and
authentication bridge; a model-name override alone is insufficient. See the
[compatibility investigation and primary sources](../../claude-compatibility.md).

The [aggregate](compact-ablation-aggregate.json),
[all attempts](compact-ablation/RESULTS.md), and
[manifests, results, and allowlisted events](compact-ablation/) are retained.
Regenerate the aggregate without model calls:

```sh
python3 eval/evidence/2026-09-20/summarize-compact-ablation.py \
  eval/evidence/2026-09-20/compact-ablation
```

The script requires exactly the planned 45 trials unless `--allow-partial` is
explicit. It checks identities, verdict consistency, complete-usage coverage,
profile differences and ordering, and records hashes of itself and its inputs.
Reproducing the historical workload requires the recorded checkout; later task
contract clarifications must use a new evidence directory and stay separately
labeled. Earlier coding and Harbor evidence is unchanged.

The next useful optimization experiment is recoverable output budgets on larger
files and terminal logs, with required information beyond the initial window.
Count recovery calls, total input, cache usage, and verified outcomes. This slice
does not justify promoting compact descriptions or the four-switch bundle to a
default. Adaptive reasoning remains excluded.
