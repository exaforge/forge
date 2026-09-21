# Forge in Harbor

This is an opt-in local integration for **Harbor 0.23.0**, Python 3.12+, and a native Linux Docker daemon. It invokes Forge inside the task container using Harbor's installed-agent API. A macOS Forge executable is rejected. The first pilot is deliberately limited to root tasks with `/root` as HOME, glibc Linux, and Python 3.10+ inside the task.

The [retained pilot](../evidence/2026-09-20/harbor/README.md) includes a passing Forge smoke run and passing attempts from both coding profiles on one Terminal-Bench task. It validates this execution path; one selected task is not a benchmark score or a general optimization result.

`adapter.py` implements `BaseInstalledAgent.install`, `run` with `with_prompt_template`, and `populate_context_post_run`, verified against the installed 0.23.0 package. It uses the profiles and metadata parsers in the parent `eval` directory. Harbor's built-in Grok adapter installs a different product and collects sessions; it is not used here. The older `create_run_agent_commands` API is not used. See the official [custom agent documentation](https://docs.harborframework.com/core-concepts/agents/custom-agents) and [pinned base class](https://github.com/harbor-framework/harbor/blob/v0.23.0/src/harbor/agents/installed/base.py).

## Setup and Linux build

From the Forge repository root:

```sh
uv --cache-dir /private/tmp/forge-harbor-uv-cache venv --python 3.12 /private/tmp/forge-harbor-venv
uv --cache-dir /private/tmp/forge-harbor-uv-cache pip install --python /private/tmp/forge-harbor-venv/bin/python 'harbor==0.23.0'
/private/tmp/forge-harbor-venv/bin/harbor --version
docker info --format '{{.OSType}}/{{.Architecture}}'
/private/tmp/forge-harbor-venv/bin/python eval/harbor/build.py --platform linux/arm64 --output /private/tmp/forge-harbor-linux --jobs 2
```

The Dockerfile uses Rust 1.94.0 and protoc 29.3, with the protoc archive SHA256 pinned to the repository's `bin/protoc` manifest. Its dedicated ignore file admits only the Cargo manifests, toolchain/config, and Rust source trees; it excludes `.git`, target output, authentication files, node modules, and evaluation artifacts. It sets `target-cpu=generic` because this repository otherwise targets Neoverse V2 for Linux ARM. BuildKit caches Linux Cargo dependencies and build output. The build wrapper records the git revision, a digest including uncommitted build-source content, recipe hashes, and binary checksum in `forge.build.json`; the launcher requires that manifest to match the binary. A source change during the build prevents provenance publication. A cold build can be substantial; host Darwin build artifacts cannot be reused. The Linux ARM64 release build was validated on 2026-09-20 (local date); its Cargo compilation took 23m 28s with two jobs. The embedded version reports an unknown commit because `.git` is excluded; the [build manifest](../evidence/2026-09-20/harbor/forge.build.json) records the actual source identity.

For a native x86_64 Docker host, build `--platform linux/amd64` and pass `--architecture x86_64` to the launcher. Emulation and mixed-architecture timings are outside this pilot. The launcher verifies the daemon architecture, builds the task's Dockerfile instead of using an architecture-uncertain prebuilt image, and checks `uname -m` again inside the container. Older glibc task images may require a build targeting their older distribution; the supplied builder is Debian bookworm.

## Official smoke task

Fetch the official source at the pinned release and use its unmodified hello-world task:

```sh
git clone --depth 1 --branch v0.23.0 https://github.com/harbor-framework/harbor.git /private/tmp/forge-harbor-source
/private/tmp/forge-harbor-venv/bin/python eval/harbor/launch.py --oracle --task /private/tmp/forge-harbor-source/examples/tasks/hello-world --output /private/tmp/forge-harbor-oracle.json
/private/tmp/forge-harbor-venv/bin/python eval/harbor/launch.py --task /private/tmp/forge-harbor-source/examples/tasks/hello-world --forge-binary /private/tmp/forge-harbor-linux/forge --profile forge-baseline --model gpt-5.6-sol --output /private/tmp/forge-harbor-forge.json
```

Run the oracle first and require `verified_success: true`. Then run Forge once. Each command uses one attempt, one concurrent trial, no retry, task-defined Harbor timeouts, and a 110-second inner Forge deadline. The smoke tests task execution and the verifier plumbing; it provides no estimate of coding quality or speed. The official [task](https://github.com/harbor-framework/harbor/tree/v0.23.0/examples/tasks/hello-world) asks for a small file, and its verifier downloads uv/pytest, so it needs network access even for an oracle run.

For a subsequent external coding pilot, the official Terminal-Bench 2 [cancel-async-tasks task](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6/cancel-async-tasks) has a small Python environment built from `python:3.13-slim-bookworm`. Its task budget is 900 seconds and its difficulty is labeled hard. Pin the dataset checkout to a recorded commit before running; use its unmodified Dockerfile and verifier, `--timeout-seconds 880`, and the same Forge binary/profile. Both this task's ARM64 oracle and the hello-world oracle passed; their retained reports are in [pilot evidence](../evidence/2026-09-20/harbor/). One selected task is still a pilot, not a Terminal-Bench score. Do not use the smoke timeout to judge that task's quality.

## Authentication and retained data

The launcher defaults to the existing `~/.codex/auth.json` and only checks its filesystem metadata. Docker mounts that single existing file read-only at `/root/.codex/auth.json` at runtime. Forge reads it using its existing canonical Codex endpoint fallback. No token is passed through environment variables or command arguments; the file is never uploaded by the adapter, placed in the image, or copied into evidence. Forge currently ignores `CODEX_HOME` for this fallback and cannot refresh the token. An expired session needs the owning Codex application to refresh it on the host.

**A read-only mount is not secret isolation from the task:** Forge and task processes running as root can read it. Use this pilot only with trusted task images and instructions. Only the individual auth file is mounted; the full Codex home is not mounted. An account token broker or host-side tool bridge would be needed to keep the credential entirely outside task containers.

Harbor's raw job/config/log files can contain mount paths, task instructions, verifier output, and exception text. `launch.py` keeps them in a private temporary directory, exports an allowlisted report, then removes that directory. It does not upload jobs or traces. The wrapper drains Forge stdout/stderr into bounded memory and drops their content. Only usage, status, timing, hashes, declared configuration, and numeric per-request observations are exported. A second export allowlist rejects arbitrary string fields and mount paths. Forge's temporary home/session files remain inside the container, which Harbor deletes on completion. Abrupt host termination may interrupt cleanup; raw job directories must never be published as evidence.

`--prompt-file` keeps the instruction out of Harbor exec logs. Model/effort and optimization flags come from the selected validated profile. Memory, subagents, web search, and tool scope follow that profile. The only deliberate argv change is disabling Forge's nested sandbox: Harbor owns the Docker boundary. Fresh Forge state prevents host skill/config discovery; task-local instructions remain part of the task.

## Evidence semantics and checks

The export keeps process completion, protocol completion, metadata completeness, and verifier reward separate. `verified_success` requires all relevant conditions, no Harbor exception, and reward exactly one. Missing observations, missing usage, unfinished requests, loss counters, duplicate events, or a missing flush footer prevent complete metadata. A valid terminal event without a passing verifier is not a successful task. HTTP status codes and a fixed vocabulary of sampler error kinds are retained without error messages. Exception timing is distinguished from the last phase started, since Harbor may run verification after an agent error.

Harbor input tokens include cached input. The adapter adds Forge's uncached input, cache-read input, and cache-creation input once; cache-read tokens are separately reported as a subset. Usage excludes auxiliary direct calls. USD cost remains unavailable for subscription usage. Native timings are client observations; rates are not server decode speed and overlapping phase durations cannot be added as a CPU/wall-time breakdown. The export hashes the task files before and after execution, profile, binary, adapter, wrapper, shared parsing source, launcher, and preflight helper.

This reduced Harbor export retains per-attempt numeric observations and the parser's coverage verdict. It omits event IDs, request-start records, collector footer rows, and request-context counters; those checks cannot be independently reconstructed from this report. It also does not establish whether each configured optimization activated. The local coding runner retains a fuller allowlisted event stream for that purpose.

```sh
PYTHONDONTWRITEBYTECODE=1 /private/tmp/forge-harbor-venv/bin/python -m unittest discover -s eval/harbor/tests -v
```

These offline tests cover the pinned adapter API, architecture rejection, no-read credential mount construction, instruction handling, usage arithmetic, metadata loss/finalness, privacy filtering, and process/reward distinctions. They do not claim that Docker setup, OAuth inference, or a benchmark run has succeeded. Actual retained pilot results should report those outcomes separately. See Harbor's [job options](https://docs.harborframework.com/core-concepts/jobs/run-a-job), [Docker sandboxes](https://docs.harborframework.com/core-concepts/sandboxes/pre-integrated-sandboxes), and [AgentContext usage semantics](https://github.com/harbor-framework/harbor/blob/v0.23.0/src/harbor/models/agent/context.py).
