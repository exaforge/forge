# Coding fixture revisions

Historical manifests retain the fixture prompt, initial file tree, supplied-test,
and verifier hashes. Changing a prompt or checker creates a new workload
revision even when its task ID and JSON schema stay the same. Use a new evidence
directory and identify the revision in comparisons; do not pool old and new
success rates without labeling the difference.

## Test-diagnosis exception contract, 2026-09-20

Applied after the [compact-description campaign](evidence/2026-09-20/compact-ablation.md)
was completed and committed in `424ccca`. That campaign used clean checkout
`351b3ca1723c632b62df13c7fb43e1842ab6e0c7`; every retained result, score, prompt
hash, and verifier hash remains unchanged.

The previous task required a positive integer `attempts` and excluded bool, but
did not explicitly require the same exception type for every invalid input.
Its public zero-input test and independent verifier required `ValueError`.
One measured attempt failed three invalid-input exception checks while passing
all no-callback checks. Repeated generic labels could not identify the inputs or
distinguish a different exception from a normal return. The report preserves
that uncertainty and the original failed score.

For subsequent runs, the prompt explicitly requires `ValueError` for every
invalid `attempts` value before invoking the callback. The independent verifier
keeps the same 20 behavioral checks and values, with fixed labels identifying
zero, negative integer, bool, float, or string. Separate labels identify callback
invocation before rejection. The retention allowlist uses those same fixed
labels; source, exception messages, and model output remain excluded.

The prompt and verifier hashes distinguish this revision. The initial file-tree
and supplied-test hashes stay unchanged. Existing report generation reads saved
verdicts without regrading or rewriting historical labels. JSON schema version
remains 1.

Offline regression checks cover a wrong exception, normal return, and callback
before rejection for each invalid-input case, plus known-correct solutions,
nonretryable exception preservation, and safe failure-label retention. No model
rerun is needed to clarify this future contract.
