# Small local inference and coding evaluation

This is a Python-standard-library runner for engineering experiments, not a
benchmark platform. It runs five small coding tasks, retains metadata, and checks
the resulting code independently. Python 3.10+ and POSIX process groups
(macOS/Linux) are required. No model calls occur when listing tasks, generating a
report, or running the offline tests.

```sh
python3 eval/run.py list
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s eval/tests -v
```

The public task definitions and starting source are in `fixtures.py`. Workloads
cover inclusive integer ranges, a multi-file CSV reporting feature, retry-test
diagnosis, configuration precedence, and an integer-pricing refactor. They use
only Python's standard library. `verify.py` tests edge cases, CLI behavior,
exception behavior, input preservation and the refactor's shared-helper contract.
Offline tests establish that all five initial fixtures fail their verifier and
known-correct changes pass it. These fixtures are small engineering probes, not
evidence of broad agent quality or a universal ranking.

## Run an experiment

Copy `profiles.example.json` to a local file and replace each Forge profile's
first `argv` entry with the **absolute path to the built binary** being tested.
Set the optional `build_profile` string to `release`, `debug`, or an honest
description of the installed comparator; it is retained alongside the version.
Profiles use argument arrays with no shell execution. Supported placeholders are
`{prompt}`, `{workspace}`, `{model}`, `{effort}`, `{max_turns}`, `{run_id}` and
`{python}`. Do not put credentials in profiles; only documented non-secret
optimization variables are accepted under `env`.

Forge profiles support configuration comparisons and individual-switch ablations:

| Profile | Prompt cache key | Context fast path | Requested tools | Shared HTTP client |
| --- | --- | --- | --- | --- |
| `forge-baseline` | off | off | default | on |
| `forge-optimized` | off | on | local coding allowlist | on |
| `forge-context-only` | off | on | default | on |
| `forge-local-tools-only` | off | off | local coding allowlist | on |
| `forge-cache-only` (experimental) | on | off | default | on |

The baseline and optimized profiles deliberately use the same executable, model,
effort and tool permissions. Optimized runs use the existing `--tools` option to
request a smaller set for local repository work: file reading/editing, search,
terminal commands and their lifecycle operations. They explicitly disallow
`search_tool` and `use_tool`, which would otherwise remain available for dynamic
tool discovery. The exact canonical allowlist and denylist are retained in each
manifest's `configuration.tool_scope`; actual availability still depends on the
active agent/toolset. The normal agent and project-instruction loading remain in
use. These profiles have different available capabilities, so this is a local
coding configuration comparison, not an identical-toolset algorithm comparison.

The context-only profile isolates the implementation fast path; local-tools-only
isolates the requested toolset reduction. Prompt-cache keys are an optional
experiment: setting a key does not establish a cache hit or a speed gain. These
comparisons do not automatically represent a historical released binary.
Separate binary paths can be used when needed.

```sh
python3 eval/run.py run --profiles /absolute/path/profiles.json \
  --profile forge-baseline --profile forge-optimized \
  --repeats 3 --timeout 600 --max-turns 20 \
  --output eval/results/forge-comparison
```

Select a single task for an initial smoke test with `--task bug-fix --repeats 1`.
An explicit `run` can consume account quota. The runner never installs tools,
logs in, or repairs authentication. An absent executable or invalid model is
recorded as a failed attempt. Choose only profiles you have configured and
authenticated. Nothing automatically launches optional comparators.

The native profiles target `gpt-5.6-sol` with medium reasoning and no Fast Mode
request; the effective provider service tier is unobserved. Each attempt receives
a fresh temporary `GROK_HOME` containing a minimal
canonical ChatGPT Codex provider configuration and `[memory] enabled = false`.
It also supplies `--no-memory`, `--no-subagents`, `--disable-web-search`,
`--sandbox workspace`, explicit model/effort, and a turn limit. The generated
Forge config and session storage are fresh; host skill/context discovery remains
inherited, including `~/.agents` skills and `~/.claude`/`~/.cursor` compatibility
locations. Managed configuration such as `/etc/grok` settings still applies. The
runner neither disables these settings nor changes global state, and records
this limitation explicitly in the manifest. There is no copied credential file.
Forge itself uses the real
Codex CLI-owned OAuth file through its existing narrow fallback. Ambient Codex
token overrides are removed for the Forge process so this experiment uses that
authentication source. The runner never opens that file.

Each attempt uses a fresh copy of the fixed fixture, a fresh session, and a fresh
process. The initial workspace and prompt are hashed. Optimization environment
variables are cleared before applying the profile, preventing an ambient flag
from silently changing the baseline. Other environment variables and external
tool/authentication stores remain inherited; this is recorded rather than
represented as complete machine isolation. Do not run this experiment while
modifying the binaries or configuration being compared.

## Direct comparators

```sh
python3 eval/run.py run --profiles /absolute/path/profiles.json \
  --profile forge-optimized --profile codex-direct \
  --repeats 3 --output eval/results/codex-comparison
```

The supplied Codex profile uses `exec --json`, explicit GPT model/effort,
`--ignore-user-config`, `--ignore-rules`, `--ephemeral`, workspace-write sandbox,
and no Git-repository requirement. It disables the `plugins`, `apps`, and
`multi_agent` features for these local fixtures. Choose the Codex executable in
the profile's first `argv` entry, and check that its version supports both the
requested model and every supplied flag. An older installed CLI may reject a
model supported by a newer executable. Authentication and external global stores
remain owned by Codex; the runner does not copy their secrets or claim complete
isolation. Codex's
public CLI does not offer the same native turn-budget flag: the common wall-time
limit still applies, and the manifest records that execution-limit difference.

`claude-direct` is optional and must already be authenticated. Its `sonnet`
setting is a documented CLI alias, not a pinned model version or availability
check. Replace it with an available full model ID before a reproducible
comparison. It uses
Claude's tools and permission controls, which differ from Forge's and Codex's;
external global configuration is not fully isolated. A Claude comparison changes
both harness and model. Never interpret it as a pure inference-engine comparison.
Even Forge versus Codex has different system prompts and tool implementations.

Tasks are run serially in adjacent matched pairs. The runner alternates AB/BA
between repetitions/tasks; for more profiles it rotates positions and reverses
the order only after a complete rotation block. With three profiles and three
repetitions, each profile occupies each position once for every task.
This reduces a fixed order advantage but does not eliminate provider cache or
account-quota effects. Provider cache state and quota are not reset or inferred.
Keep usage/cache observations, report rate-limit failures, and avoid simultaneous
unrelated traffic on the same account where practical. Three repetitions are an
exploratory check, not strong statistical evidence.

## Retained evidence

Every run directory contains only:

- `manifest.json`: run/task/profile IDs, declared configuration and limits,
  binary version/digest, checkout revision and dirty/untracked source digests,
  OS/release/architecture/Python version, starting-workspace/prompt/verifier
  hashes, and state/privacy limitations. Machine metadata omits hostname/user.
- `events.jsonl`: an allowlisted projection of native observation schema v1
  request/attempt/phase/health records. Raw text, prompts, arguments, tool output,
  and unexpected fields are discarded.
- `result.json`: process/protocol status, independent verification, observed
  metrics and coverage. Missing or incomplete measurements remain `null`.

The fixture prompts/source are intentionally public in this repository. Generated
source, stdout, stderr, session transcripts and temporary Forge logs are not
retained by the evaluator. Output is drained into bounded memory and parsed there.
External CLIs may still retain data in their own stores according to their own
settings; the runner does not claim to suppress those stores. Version output and
profile metadata must not contain secrets.

The source snapshot describes the checkout when the runner started; it does not
prove that a preexisting binary was built from it. The executable SHA-256 identifies
the actual artifact. Build immediately before the experiment for clear attribution.
Source provenance hashing reads the bytes of nonignored untracked repository
files, but retains only a combined digest. The no-access privacy claim applies
to authentication stores; it is not a claim that arbitrary files in the checkout
cannot contain credentials. No source bytes are retained in the evidence.

Process completion, protocol completion and verified task outcome are separate.
`successful_verified_run` requires all three, untruncated output, and unchanged
provided tests. Their initial hashes are retained in the manifest and compared
before and after verification; changed/deleted tests fail the recorded constraint
even if the implementation passes its behavioral verifier. Adding tests is allowed.
A clean exit
does not prove the code is correct; a process error can still leave code that
passes the verifier. Verifier failures, verifier errors and timeouts are retained.
The fixed verifier is copied outside the writable workspace, made read-only, and
hashed before and after execution. Tampering is an integrity error. This arrangement
is **not an OS security boundary against hostile code**; use a container or stronger
sandbox before evaluating adversarial submissions.

New runs retain allowlisted public failed-check identifiers and a fixed exception
category to help diagnose failures without saving submitted source or error text.
Earlier evidence snapshots retain their original, less detailed schema.

`execution.duration_ms` spans process launch through process-group cleanup. Leader
exit and cleanup are recorded separately when available. Output-drain/parsing and
verifier time are separate. Timeouts terminate the entire POSIX process group;
cleanup also reaps remaining descendants after normal leader exit.

Native request coverage is limited to requests through the instrumented sampler
actor. Auxiliary calls through other paths may be absent. Usage is aggregated only
from attempt-finish rows, never again from request-finish rows. Complete native
coverage requires paired request/attempt boundaries, no duplicate attempts, final
usage, and healthy per-process collector footers. Partial observations retain their
known counts as `observed_*` while complete totals stay null. Cached/reasoning
details are subsets of full native input/output and are never added twice.
Provider `total_tokens` can mean context length, so it is not treated as a bill.

External usage comes only from the CLI's reported terminal JSON, not process
duration or streamed text. No external request TTFT, server decode rate, token
timing, hidden retries or cost are inferred. First-generation latency can refer
to text, reasoning or tool output. The primary native request output rate divides
reported output tokens by the entire sampler attempt duration, including initial
wait. The additional generation-window rate is a client-observed approximation;
it remains null when reasoning usage is unknown or reported reasoning is not
streamed. Neither rate measures server decode speed. Sampling windows and reasons
for unavailability are retained in events.
Overlapping request/tool/child phases must not be summed into elapsed wall time.

## Results

The [2026-09-20 evaluation snapshot](evidence/2026-09-20/README.md) contains
the first 45 trials and explains the observed speed, usage and quality tradeoff.

`summary.json` and `RESULTS.md` are updated after each attempt. The table shows
successes/attempts, all-attempt elapsed median/range, successful-attempt median,
native first-output/rate observations, request/retry counts, reported usage and
evidence links. Usage columns distinguish complete reported samples from missing
observations. All failed attempts stay in the evidence and success denominator.
Small samples should be described as observations on these tasks/settings.
Text TTFT has its own median and sample count; text-free requests remain null.
`result.json` also retains per-phase medians/sample counts for request building,
sampler preparation, stream drain, tool preparation/dispatch and headless/prompt
boundaries. Those overlapping intervals are not summed into wall-time or CPU.

Regenerate the table without executing models:

```sh
python3 eval/run.py report eval/results/forge-comparison
```

The initial unit is one headless invocation, which can include many model/tool
rounds. Scripted multi-turn evaluation is intentionally not implemented yet. A
future extension should group explicit `--resume <session-id>` invocations under
one workload, preserve per-invocation evidence, and reset workspace/session/memory
before each workload repetition. Avoid implicit `--continue` selection.
