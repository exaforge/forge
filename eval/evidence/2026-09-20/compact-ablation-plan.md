# Compact-description experiment plan

This plan is recorded before the measured calls. It follows the mixed results
of the four-switch coding check and Harbor pilot. The existing optimizations
remain opt-in; this experiment adds no native behavior or adaptive reasoning.

## Question and design

Does shortening the native read, search, and directory-listing tool descriptions
improve verified task completion time or usage when enabled alone? Compare
`forge-optimized` with `forge-compact-descriptions-only`. Their arguments, model,
medium reasoning, tool scope, binary, and other optimization flags are identical;
the latter adds only `FORGE_COMPACT_TOOL_DESCRIPTIONS=1`.

Include `codex-direct` as an external product reference using GPT-5.6 Sol and
medium reasoning. It is not the controlled description experiment: its system
prompt, tools, discovery, permission implementation, and execution limits differ.
Codex request-level TTFT and generation rates remain unavailable through this
adapter. No metrics will be inferred from wall time or reported text length.
Claude Code is excluded from this round: the installed CLI is signed out, and
its official gateway documentation does not support non-Claude models. See the
[compatibility investigation](../../claude-compatibility.md) for the protocol and
authentication boundaries; no unvalidated proxy is part of this comparison.

Run all five existing fixtures three times per profile: 45 serial attempts,
15 per profile. Each task/repetition group contains all three profiles next to
one another. Rotate positions and alternate orientation across task blocks;
each profile occupies each position five times, with pair precedence as balanced
as an odd number of pairs permits. Each attempt has a fresh workspace/session,
a 600-second wall limit, independent verification, and protected supplied tests.
Forge has a 20-turn ceiling; Codex lacks an equivalent CLI option. Provider cache,
network conditions, account load, managed settings, and host discovery remain
uncontrolled. No compilation or concurrent benchmark model calls run during this
comparison.

Use the existing native release artifact, SHA-256
`c54d86d304462f649bde06f5d75731b648e66d220527bd3baabac0671b168c58`,
built from native source at `e4d3596`; subsequent changes were evaluation scripts
and documentation. Use bundled Codex CLI `0.155.0-alpha.9.2`. Resolve executable
paths in a local profile file and retain hashes/versions in every manifest;
check both artifacts remain unchanged at the end. The runner's checkout identity
is separate from the native artifact's earlier build identity.

## Readout

Report every scheduled attempt, verified passes out of 15, failure identifiers,
and unchanged-test checks first. Include total elapsed time across all attempts,
per-task medians/ranges, and compact-versus-reference paired changes by task and
repetition. A fast failed attempt does not count as a speed win: show the number
of mutually successful pairs and the wins/losses and median relative change
within that explicitly stated subset. Do not discard slow successful attempts
or replace failures with fresh attempts.

Report complete-usage sample counts alongside input including cache, the cached
subset, output, and observed native reasoning/request counts. Missing or partial
observations stay unavailable, rather than zero. Check the mechanism directly:
observed tool-description bytes, first-request input, and unchanged Forge
model/effort/tool count. Native coverage verdicts and collected events remain
available for review. No paid-dollar estimates are made from subscription usage.

Three repetitions per task are an exploratory comparison, not broad quality
assurance or a general performance ranking. Any verified failure prevents a claim
of unchanged correctness. Aggregate and paired results must be considered
together; a lower median does not cancel failures or a worse all-attempt total.
Record the result even if compact descriptions lose. A favorable small result
alone will not silently change default behavior.

## Reproduction

```sh
PYTHONDONTWRITEBYTECODE=1 python3 eval/run.py run \
  --profiles /absolute/path/compact-ablation-profiles.json \
  --profile forge-optimized --profile forge-compact-descriptions-only \
  --profile codex-direct --repeats 3 --timeout 600 --max-turns 20 \
  --output eval/evidence/2026-09-20/compact-ablation
```

The local profile file contains only the three existing profile definitions with
absolute executable paths and descriptive build labels. Credentials are neither
placed in it nor copied by the runner. The existing ChatGPT sign-in is owned by
Codex; Forge uses its established fallback. Earlier evidence remains unchanged.
