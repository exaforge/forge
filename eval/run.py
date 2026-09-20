#!/usr/bin/env python3
"""Local, metadata-only coding evaluation. Python 3.10+, standard library only."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import statistics
import string
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from fixtures import TASKS, materialize
from verify import CHECK_LABELS

HERE = Path(__file__).resolve().parent
CAPTURE_LIMIT = 8 * 1024 * 1024
SAFE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\Z")
PLACEHOLDERS = {"prompt", "workspace", "model", "effort", "max_turns", "run_id", "python"}
ALLOWED_ENV = {"FORGE_PROMPT_CACHE", "FORGE_CONTEXT_FAST_PATH", "GROK_SAMPLER_SHARED_CLIENT",
               "GROK_POOL_MAX_IDLE", "GROK_POOL_IDLE_TIMEOUT_SECS", "GROK_CONNECT_TIMEOUT_SECS"}
USAGE_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens",
                "cache_creation_input_tokens", "reasoning_tokens", "total_tokens")
OBS_NUMBER_KEYS = {"schema_version", "timestamp_unix_ms", "elapsed_ms", "attempt", "duration_ms",
                   "first_response_event_ms", "first_text_ms", "first_reasoning_ms", "first_tool_delta_ms",
                   "visible_text_chunk_count", "attempts_started", "retries_started", "process_id",
                   "retry_index_within_budget", "retry_budget", "max_retries", "first_generation_event_ms",
                   "end_to_end_output_tokens_per_second", "observed_output_tokens_per_second",
                   "dropped_events", "write_failures", "dropped", "events_written", "events_dropped",
                   "dropped_records", "flush_failures", "records_written", "raw_response_event_count",
                   "raw_stream_error_count", "status_code", "loop_index", "turn_number", "tool_count"}
OBS_NUMBER_KEYS.add("generated_content_event_count")
OBS_STRING_KEYS = {"run_id", "request_id", "session_id", "prompt_id", "phase_id", "phase", "model", "outcome",
                   "usage_scope", "scope", "throughput_scope", "provider", "api_backend", "backend",
                   "timing_scope", "usage_semantics", "error_kind", "counter_scope", "throughput_unavailable_reason",
                   "status", "tool_call_id"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def tree_digest(path):
    """Hash the fixture names and bytes, including ordering and empty files."""
    hasher = hashlib.sha256()
    for file in sorted(path.rglob("*")):
        if file.is_file():
            name = file.relative_to(path).as_posix().encode()
            data = file.read_bytes()
            hasher.update(len(name).to_bytes(8, "big") + name + len(data).to_bytes(8, "big") + data)
    return hasher.hexdigest()


def protected_file_changes(workspace, initial_hashes):
    changed = []
    for name, expected in initial_hashes.items():
        path = workspace / name
        try:
            intact = not path.is_symlink() and file_digest(path) == expected
        except OSError:
            intact = False
        if not intact:
            changed.append(name)
    return changed


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def number(value):
    return value if type(value) in (int, float) and value >= 0 and value < float("inf") else None


class Capture:
    """Drain pipes without deadlocking or retaining unbounded model output."""
    def __init__(self, pipe):
        self.pipe = pipe
        self.data = bytearray()
        self.truncated = False
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        try:
            for chunk in iter(lambda: self.pipe.read(65536), b""):
                space = max(0, CAPTURE_LIMIT - len(self.data))
                self.data.extend(chunk[:space])
                self.truncated |= len(chunk) > space
        finally:
            self.pipe.close()

    def finish(self):
        self.thread.join(timeout=2)
        self.truncated |= self.thread.is_alive()
        return bytes(self.data).decode("utf-8", errors="replace")


def stop_group(process):
    """Reap both the leader and surviving descendants, including after normal exit."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def execute(argv, cwd, env, timeout):
    started = time.monotonic_ns()
    state = {"started_at": now(), "status": "spawn_error", "exit_code": None,
             "signal": None, "duration_ms": None, "output_truncated": False}
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
    except OSError as error:
        state["error_category"] = type(error).__name__
        state["duration_ms"] = (time.monotonic_ns() - started) / 1e6
        return state, ""
    output, errors = Capture(process.stdout), Capture(process.stderr)
    try:
        process.wait(timeout=timeout)
        state["status"] = "completed" if process.returncode == 0 else "process_error"
        state["leader_exit_ms"] = (time.monotonic_ns() - started) / 1e6
    except subprocess.TimeoutExpired:
        state["status"] = "timeout"
    except KeyboardInterrupt:
        state["status"] = "cancelled"
    finally:
        cleanup_started = time.monotonic_ns()
        stop_group(process)
        state["cleanup_ms"] = (time.monotonic_ns() - cleanup_started) / 1e6
        state["duration_ms"] = (time.monotonic_ns() - started) / 1e6
    drain_started = time.monotonic_ns()
    stdout = output.finish()
    errors.finish()  # Drain, but never retain stderr or expose it in result files.
    state.update(exit_code=process.returncode,
                 signal=-process.returncode if process.returncode < 0 else None,
                 output_drain_ms=(time.monotonic_ns() - drain_started) / 1e6,
                 output_truncated=output.truncated or errors.truncated)
    return state, stdout


def load_profiles(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    profiles = document.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles must be a nonempty array")
    result = {}
    for profile in profiles:
        identifier = profile.get("id", "")
        if not isinstance(identifier, str) or not SAFE_ID.fullmatch(identifier) or identifier in result:
            raise ValueError("profile ids must be unique simple names")
        if profile.get("kind") not in {"forge", "codex", "claude", "fake"}:
            raise ValueError("kind must be forge, codex, claude or fake")
        argv = profile.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise ValueError("argv must be a nonempty string array; shell strings are not supported")
        for arg in argv:
            for _, field, format_spec, conversion in string.Formatter().parse(arg):
                if field is not None and (field not in PLACEHOLDERS or format_spec or conversion):
                    raise ValueError("unsupported argv placeholder")
        env = profile.get("env", {})
        if not isinstance(env, dict) or set(env) - ALLOWED_ENV or not all(isinstance(v, str) for v in env.values()):
            raise ValueError("profile env supports only the documented non-secret optimization knobs")
        if profile.get("kind") == "forge":
            for flag in ("--output-format", "--no-auto-update", "--no-memory", "--no-subagents", "--max-turns",
                         "--model", "--effort", "--sandbox", "--disable-web-search"):
                if flag not in argv:
                    raise ValueError("Forge profiles must explicitly include " + flag)
            if argv[argv.index("--output-format") + 1] != "json":
                raise ValueError("Forge profiles must use --output-format json")
            declared_tool_scope(profile)
        version_argv = profile.get("version_argv", [argv[0], "--version"])
        if not isinstance(version_argv, list) or not version_argv or not all(isinstance(v, str) for v in version_argv):
            raise ValueError("version_argv must be a string array")
        for name in ("model", "effort"):
            if not isinstance(profile.get(name), str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", profile[name]):
                raise ValueError("profiles must declare simple model and effort identifiers")
        result[identifier] = profile
    return result


def expand(argv, values):
    return [arg.format_map(values) for arg in argv]


def declared_tool_scope(profile):
    """Retain the requested canonical allowlist, without claiming actual tool coverage."""
    if profile["kind"] != "forge":
        return {"mode": "profile_defined", "requested_tool_ids": None}
    argv = profile["argv"]
    def identifiers(flag):
        values = []
        for index, arg in enumerate(argv):
            if arg == flag:
                if index + 1 >= len(argv):
                    raise ValueError(flag + " requires a comma-separated canonical tool list")
                values.append(argv[index + 1])
            elif arg.startswith(flag + "="):
                values.append(arg[len(flag) + 1:])
        if not values:
            return None
        if len(values) != 1:
            raise ValueError("Forge profiles must declare at most one " + flag + " list")
        tool_ids = values[0].split(",")
        if len(set(tool_ids)) != len(tool_ids) or not all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./:-]{0,99}", tool) for tool in tool_ids):
            raise ValueError(flag + " must contain unique simple canonical tool identifiers")
        return tool_ids
    allowed, denied = identifiers("--tools"), identifiers("--disallowed-tools")
    return {"mode": "explicit_allowlist" if allowed is not None else "default_agent_tools",
            "requested_tool_ids": allowed, "disallowed_tool_ids": denied}


def forge_config(model):
    # A fresh home isolates this config and session memory, not host skill/context
    # discovery, compatibility roots or managed settings.
    # Authentication remains with Codex; the runner never opens/copies auth.json.
    model_toml = json.dumps(model)
    return f'''[memory]
enabled = false
[models]
default = {model_toml}
[model_providers.codex]
base_url = "https://chatgpt.com/backend-api/codex"
api_backend = "responses"
env_key = "CODEX_ACCESS_TOKEN"
[model_providers.codex.extra_headers]
OpenAI-Beta = "responses=experimental"
originator = "codex_cli_rs"
[model.{model_toml}]
model_provider = "codex"
model = {model_toml}
name = {model_toml}
context_window = 200000
supports_reasoning_effort = true
supports_fast_mode = false
'''


def parse_objects(text):
    try:
        value = json.loads(text)
        return [value] if isinstance(value, dict) else []
    except (ValueError, TypeError):
        result = []
        for line in text.splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    result.append(value)
            except ValueError:
                pass
        return result


def protocol_metadata(kind, stdout, complete):
    """Project only usage and terminal status; discard all content-bearing fields."""
    objects = parse_objects(stdout)
    terminals, usage_rows = [], []
    status, scope = "unobserved", "unavailable"
    for obj in objects:
        if kind in {"forge", "fake"} and ("stopReason" in obj or "error" in obj):
            terminals.append(obj)
            status = "completed" if obj.get("stopReason") in {"end_turn", "EndTurn"} else "incomplete_or_error"
            if isinstance(obj.get("usage"), dict):
                usage_rows = [obj["usage"]]
                scope = "headless_prompt_ledger_uncached_input_auxiliary_calls_excluded"
                complete &= not obj.get("usage_is_incomplete", False)
        elif kind == "codex" and obj.get("type") == "turn.completed":
            terminals.append(obj)
            status = "completed"
            if isinstance(obj.get("usage"), dict):
                usage_rows.append(obj["usage"])
                scope = "codex_cli_reported_turn_usage_input_includes_cache"
        elif kind == "codex" and obj.get("type") in {"turn.failed", "error"}:
            status = "incomplete_or_error"
        elif kind == "claude" and obj.get("type") == "result":
            terminals.append(obj)
            status = "incomplete_or_error" if obj.get("is_error") else "completed"
            if isinstance(obj.get("usage"), dict):
                usage_rows = [obj["usage"]]
                scope = "claude_cli_reported_result_usage_uncached_input"
    usage = {}
    aliases = {"cached_input_tokens": "cache_read_input_tokens"}
    for key in USAGE_FIELDS:
        values = [number(row.get(key, row.get(aliases.get(key, key)))) for row in usage_rows]
        usage[key] = sum(values) if values and all(v is not None for v in values) else None
    return {"status": status, "terminal_events": len(terminals), "usage": usage,
            "usage_scope": scope,
            "usage_coverage": "complete_for_reported_scope" if usage_rows and complete and status == "completed"
                              else "partial" if usage_rows else "unavailable"}


def read_observations(path, run_id):
    events, discarded = [], 0
    if not path.exists() or path.is_symlink():
        return events, {"status": "unavailable", "discarded_records": None}
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return events, {"status": "unavailable", "discarded_records": None}
    with handle:
        for index, line in enumerate(handle):
            if index >= 100000 or len(line) > 65536:
                discarded += 1
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                discarded += 1
                continue
            if not isinstance(raw, dict) or raw.get("run_id") != run_id or raw.get("schema_version") != 1:
                discarded += 1
                continue
            name = raw.get("event", raw.get("type"))
            if name not in {"sampler_request_started", "sampler_attempt_started", "sampler_attempt_finished",
                            "sampler_request_finished", "sampler_retry_scheduled", "phase_start", "phase_end", "observation_health"}:
                discarded += 1
                continue
            event = {"event": name}
            for key in OBS_NUMBER_KEYS:
                if key in raw:
                    event[key] = number(raw[key])
            for key in OBS_STRING_KEYS:
                if isinstance(raw.get(key), str):
                    event[key] = raw[key][:200]
            for key in ("usage_is_final", "context_fast_path", "includes_lock_wait_and_auth_retry"):
                if type(raw.get(key)) is bool:
                    event[key] = raw[key]
            config = raw.get("requested_config")
            if isinstance(config, dict):
                event["requested_config"] = {}
                for key in ("max_output_tokens", "temperature", "top_p", "max_retries", "idle_timeout_secs",
                            "input_item_count", "tool_count", "hosted_tool_count"):
                    if key in config:
                        event["requested_config"][key] = number(config[key])
                for key in ("fast_mode", "force_http1", "prompt_cache_key_present", "prompt_cache_key_forwarded_by_backend", "json_schema_present"):
                    if type(config.get(key)) is bool:
                        event["requested_config"][key] = config[key]
                if isinstance(config.get("reasoning_effort"), str):
                    event["requested_config"]["reasoning_effort"] = config["reasoning_effort"][:40]
            if "provider_usage" in raw:
                usage = raw["provider_usage"]
                event["provider_usage"] = ({key: number(usage.get(key)) for key in USAGE_FIELDS}
                                           if isinstance(usage, dict) else None)
                if isinstance(usage, dict) and isinstance(usage.get("detail_availability"), str):
                    event["provider_usage"]["detail_availability"] = usage["detail_availability"][:200]
            events.append(event)
    return events, {"status": "partial" if discarded else "observed", "discarded_records": discarded}


def native_metrics(events, collection, process_ok):
    starts = [event for event in events if event["event"] == "sampler_attempt_started"]
    attempts = [event for event in events if event["event"] == "sampler_attempt_finished"]
    request_starts = [event for event in events if event["event"] == "sampler_request_started"]
    requests = [event for event in events if event["event"] == "sampler_request_finished"]
    rows = [event["provider_usage"] for event in attempts if isinstance(event.get("provider_usage"), dict)]
    def identities(items):
        return {(item.get("process_id"), item.get("request_id"), item.get("attempt")) for item in items}
    duplicates = any(len(identities(items)) != len(items) for items in (attempts, starts, requests, request_starts))
    health = [event for event in events if event["event"] == "observation_health"]
    bad_health = any(event["event"] == "observation_health" and any(
        event.get(key, 0) for key in ("dropped_records", "write_failures", "flush_failures")) for event in events)
    sampler_processes = {event.get("process_id") for event in requests + request_starts + starts + attempts}
    health_complete = all(any(footer.get("process_id") == process and all(
        footer.get(key) == 0 for key in ("dropped_records", "write_failures", "flush_failures"))
        and (footer.get("elapsed_ms") if footer.get("elapsed_ms") is not None else -1) >= max((event.get("elapsed_ms", 0) or 0 for event in requests + attempts
                                               if event.get("process_id") == process), default=0)
        for footer in health) for process in sampler_processes) if requests else False
    complete = bool(attempts) and len(rows) == len(attempts) and all(event.get("usage_is_final") is True for event in attempts) \
        and identities(starts) == identities(attempts) and identities(request_starts) == identities(requests) \
        and health_complete \
        and not duplicates and not bad_health and collection["status"] == "observed" and process_ok
    usage = {}
    for key in USAGE_FIELDS:
        values = [number(row.get(key)) for row in rows]
        usage[key] = sum(values) if complete and values and all(v is not None for v in values) else None
        known = [value for value in values if value is not None]
        usage["observed_" + key] = sum(known) if known and not duplicates else None
    phases = {}
    for phase in ("request_build", "sampler_prepare", "stream_drain", "tool_prepare", "tool_dispatch", "headless", "prompt"):
        durations = [number(event.get("duration_ms")) for event in events
                     if event["event"] == "phase_end" and event.get("phase") == phase]
        durations = [duration for duration in durations if duration is not None]
        phases[phase] = {"median_duration_ms": median(durations), "samples": len(durations)}
    return {"request_count": len(requests) if requests else None,
            "attempt_count": len(attempts) if attempts else None,
            "retries_scheduled": sum(event["event"] == "sampler_retry_scheduled" for event in events) if requests else None,
            "usage": usage,
            "usage_coverage": "complete_for_observed_sampler_attempts" if complete else "partial" if rows else "unavailable",
            "usage_scope": "sampler_actor_only; input includes cache, output includes reasoning; auxiliary direct calls excluded",
            "total_tokens_scope": "provider-reported; may be context length, do not interpret as billed tokens",
            "duplicate_attempts": duplicates, "collector_reported_loss": bad_health,
            "collector_health_complete": health_complete,
            "median_request_duration_ms": median([number(event.get("duration_ms")) for event in requests]),
            "median_first_generation_event_ms": median([number(event.get("first_generation_event_ms")) for event in attempts]),
            "median_first_text_ms": median([number(event.get("first_text_ms")) for event in attempts]),
            "text_ttft_samples": sum(number(event.get("first_text_ms")) is not None for event in attempts),
            "median_observed_output_tokens_per_second": median([number(event.get("observed_output_tokens_per_second")) for event in attempts]),
            "median_end_to_end_output_tokens_per_second": median([number(event.get("end_to_end_output_tokens_per_second")) for event in attempts]),
            "end_to_end_output_rate_samples": sum(number(event.get("end_to_end_output_tokens_per_second")) is not None for event in attempts),
            "first_generation_samples": sum(number(event.get("first_generation_event_ms")) is not None for event in attempts),
            "output_rate_samples": sum(number(event.get("observed_output_tokens_per_second")) is not None for event in attempts),
            "rate_scope": "client-observed approximation, includes reported reasoning/tool output; not server decode speed",
            "end_to_end_rate_scope": "reported output tokens / whole sampler attempt duration, including initial wait; not server decode speed",
            "throughput_unavailable_reasons": dict(Counter(event["throughput_unavailable_reason"] for event in attempts if isinstance(event.get("throughput_unavailable_reason"), str))),
            "phase_summary": phases, "phase_scope": "observed overlapping client intervals; not additive wall-time or CPU breakdown",
            "observed_models": sorted({event["model"] for event in requests + request_starts if isinstance(event.get("model"), str)})}


def verify(task_id, workspace, verifier, expected_hash, timeout):
    started = time.monotonic_ns()
    def intact():
        try:
            return not verifier.is_symlink() and file_digest(verifier) == expected_hash
        except OSError:
            return False
    if not intact():
        return {"status": "integrity_error", "passed": None, "duration_ms": 0.0}
    state, stdout = execute([sys.executable, "-I", str(verifier), task_id, str(workspace)],
                            verifier.parent, {"PATH": os.environ.get("PATH", "")}, timeout)
    integrity = intact()
    results = parse_objects(stdout)
    result = results[-1] if results else {}
    valid = type(result.get("passed")) is bool and type(result.get("checks_total")) is int \
        and type(result.get("checks_passed")) is int and 0 <= result["checks_passed"] <= result["checks_total"] \
        and (result["checks_total"] > 0 or result.get("error") == "submission_exception")
    if not integrity:
        status, passed = "integrity_error", None
    elif state["status"] == "timeout":
        status, passed = "timeout", None
    elif not valid:
        status, passed = "verifier_error", None
    else:
        passed = result["passed"] and state["exit_code"] == 0
        status = "passed" if passed else "failed"
    failed = result.get("failed_checks")
    labels = [label for label in failed[:256] if isinstance(label, str) and label in CHECK_LABELS[task_id]] \
        if valid and integrity and isinstance(failed, list) else None
    return {"status": status, "passed": passed, "duration_ms": (time.monotonic_ns() - started) / 1e6,
            "checks_passed": result.get("checks_passed") if valid else None,
            "checks_total": result.get("checks_total") if valid else None,
            "failed_checks": labels,
            "error_category": "submission_exception" if valid and result.get("error") == "submission_exception" else None,
            "verifier_sha256": expected_hash, "integrity_preserved": integrity}


def profile_identity(profile):
    values = {key: "" for key in PLACEHOLDERS}
    values["python"] = sys.executable
    executable = expand([profile["argv"][0]], values)[0]
    resolved = shutil.which(executable)
    identity = {"profile_sha256": digest(json.dumps(profile, sort_keys=True).encode()),
                "executable": Path(executable).name, "executable_sha256": None, "version": None,
                "build_profile": profile.get("build_profile", "not_declared")}
    if resolved:
        identity["executable_sha256"] = file_digest(resolved)
        argv = expand(profile.get("version_argv", [executable, "--version"]), values)
        state, text = execute(argv, HERE, os.environ.copy(), 10)
        if state["status"] == "completed":
            identity["version"] = " ".join(text.split())[:300] or None
    return identity


def source_identity():
    """Persist only source digests, never the dirty diff or untracked file content."""
    def git(*args):
        try:
            result = subprocess.run(["git", *args], cwd=HERE.parent, capture_output=True, timeout=10)
            return result.stdout if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    revision, diff, untracked = git("rev-parse", "HEAD"), git("diff", "--binary", "HEAD"), git("ls-files", "--others", "--exclude-standard", "-z")
    untracked_hasher = hashlib.sha256()
    if untracked is not None:
        for name in sorted(untracked.split(b"\0")):
            if name:
                path = HERE.parent / os.fsdecode(name)
                if path.is_file() and not path.is_symlink():
                    untracked_hasher.update(name + b"\0" + file_digest(path).encode())
    return {"revision": revision.decode().strip() if revision else None,
            "tracked_diff_sha256": digest(diff) if diff is not None else None,
            "untracked_files_sha256": untracked_hasher.hexdigest() if untracked is not None else None,
            "scope": "checkout at runner start; executable_sha256 identifies the executed artifact"}


def run_attempt(profile, task_id, repetition, order, destination, limits, identity):
    run_id = str(uuid.uuid4())
    output = destination / run_id
    output.mkdir()
    task = TASKS[task_id]
    verifier_hash = file_digest(HERE / "verify.py")
    with tempfile.TemporaryDirectory(prefix="forge-eval-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        materialize(task_id, workspace)
        provided_test_hashes = {name: file_digest(workspace / name) for name in task["files"]
                               if Path(name).name.startswith("test_") or "tests" in Path(name).parts}
        forge_home = root / "forge-home"
        forge_home.mkdir()
        config = forge_config(profile["model"])
        (forge_home / "config.toml").write_text(config, encoding="utf-8")
        observation_file = root / "observations.jsonl"
        verifier_dir = root / "verification"
        verifier_dir.mkdir()
        verifier = verifier_dir / "verify.py"
        shutil.copyfile(HERE / "verify.py", verifier)
        verifier.chmod(0o444)
        values = {"prompt": task["prompt"], "workspace": str(workspace), "model": profile["model"],
                  "effort": profile["effort"], "max_turns": str(limits["max_turns"]),
                  "run_id": run_id, "python": sys.executable}
        env = os.environ.copy()
        # Native config/state is fresh. External tools retain their authentication store.
        # Never open or copy a credential file.
        for key in ALLOWED_ENV:
            env.pop(key, None)
        env.update(profile.get("env", {}))
        if profile["kind"] == "forge":
            # Pin subscription-backed auth to the CLI-owned file, not an ambient override.
            env.pop("CODEX_ACCESS_TOKEN", None)
            env.pop("OPENAI_CODEX_TOKEN", None)
        env.update(GROK_HOME=str(forge_home), FORGE_OBSERVATION_FILE=str(observation_file),
                   FORGE_RUN_ID=run_id, GROK_DISABLE_AUTOUPDATER="1", PYTHONDONTWRITEBYTECODE="1")
        manifest = {"schema_version": 1, "run_id": run_id, "task": task_id, "category": task["category"],
                    "profile": profile["id"], "kind": profile["kind"], "repetition": repetition,
                    "execution_order": order, "created_at": now(), "software": identity,
                    "machine": {"system": platform.system(), "release": platform.release(),
                                "architecture": platform.machine(), "python_version": platform.python_version()},
                    "configuration": {"model": profile["model"], "reasoning_effort": profile["effort"],
                                      "tool_scope": declared_tool_scope(profile),
                                      "declared_optimization_env": profile.get("env", {}),
                                      "limits": limits, "forge_config_sha256": digest(config.encode()) if profile["kind"] == "forge" else None,
                                      "service_tier": "default" if profile["kind"] == "forge" else "not_observed"},
                    "state": {"initial_workspace_sha256": tree_digest(workspace),
                              "provided_test_sha256": provided_test_hashes,
                              "fixture_prompt_sha256": digest(task["prompt"].encode()),
                              "session": "fresh", "memory": "disabled" if profile["kind"] == "forge" else "profile_defined",
                              "forge_home": "fresh_temporary" if profile["kind"] == "forge" else "not_applicable",
                              "skill_context_discovery": "inherited_host_discovery_not_isolated",
                              "managed_configuration": "retained",
                              "workspace": "fresh_temporary_copy",
                              "external_global_state": "not_isolated; see profile flags",
                              "ambient_optimization_keys": "cleared_before_profile_overrides",
                              "account_quota": "not_observed", "provider_cache": "not_controlled; report usage when available"},
                    "privacy": {"raw_stdout_stderr_retained": False, "workspace_retained": False,
                                "authentication_store_read_or_copied_by_runner": False},
                    "verifier_sha256": verifier_hash}
        write_json(output / "manifest.json", manifest)
        process, stdout = execute(expand(profile["argv"], values), workspace, env, limits["timeout_seconds"])
        process_ok = process["status"] == "completed" and not process["output_truncated"]
        protocol = protocol_metadata(profile["kind"], stdout, process_ok)
        del stdout
        events, collection = read_observations(observation_file, run_id)
        native = native_metrics(events, collection, process_ok)
        changed_tests = protected_file_changes(workspace, provided_test_hashes)
        verification = verify(task_id, workspace, verifier, verifier_hash, limits["verifier_timeout_seconds"])
        changed_tests = sorted(set(changed_tests + protected_file_changes(workspace, provided_test_hashes)))
        constraints = {"provided_tests_unchanged": not changed_tests,
                       "changed_or_missing_provided_tests": changed_tests}
        with (output / "events.jsonl").open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, allow_nan=False) + "\n")
        result = {"schema_version": 1, "run_id": run_id, "task": task_id, "profile": profile["id"],
                  "kind": profile["kind"], "repetition": repetition, "execution_order": order,
                  "execution": process, "protocol": protocol, "verification": verification,
                  "constraints": constraints,
                  "native": native, "collection": collection,
                  "successful_verified_run": process_ok and protocol["status"] == "completed"
                                             and verification["passed"] is True and constraints["provided_tests_unchanged"]}
        write_json(output / "result.json", result)
    return result


def schedule(profiles, tasks, repetitions):
    """Adjacent matched tasks; rotate positions, reverse each full rotation block."""
    for repetition in range(repetitions):
        for task_index, task in enumerate(tasks):
            shift = (repetition + task_index) % len(profiles)
            order = profiles[shift:] + profiles[:shift]
            if len(profiles) > 2 and (repetition // len(profiles)) % 2:
                order = list(reversed(order))
            for profile in order:
                yield profile, task, repetition + 1


def median(values):
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def summarize(results):
    groups = {}
    for result in results:
        groups.setdefault((result["task"], result["profile"]), []).append(result)
    summary = []
    for (task, profile), rows in sorted(groups.items()):
        successes = [row for row in rows if row["successful_verified_run"]]
        protocol_rows = [row["protocol"] for row in rows if row["protocol"]["usage_coverage"] == "complete_for_reported_scope"]
        def inclusive_input(row):
            usage = row["usage"]
            if "uncached_input" not in row["usage_scope"]:
                return usage["input_tokens"]
            parts = [usage[key] for key in ("input_tokens", "cached_input_tokens", "cache_creation_input_tokens")]
            return sum(parts) if all(value is not None for value in parts) else None
        summary.append({"task": task, "profile": profile, "attempts": len(rows),
                        "verified_passes": sum(row["verification"]["passed"] is True for row in rows),
                        "successful_verified_runs": len(successes),
                        "success_rate": len(successes) / len(rows),
                        "execution_statuses": dict(Counter(row["execution"]["status"] for row in rows)),
                        "verifier_statuses": dict(Counter(row["verification"]["status"] for row in rows)),
                        "constraint_failures": sum(not row["constraints"]["provided_tests_unchanged"] for row in rows),
                        "median_all_elapsed_ms": median([row["execution"]["duration_ms"] for row in rows]),
                        "median_successful_elapsed_ms": median([row["execution"]["duration_ms"] for row in successes]),
                        "min_all_elapsed_ms": min(row["execution"]["duration_ms"] for row in rows),
                        "max_all_elapsed_ms": max(row["execution"]["duration_ms"] for row in rows),
                        "median_observed_requests": median([row["native"]["request_count"] for row in rows]),
                        "median_observed_retries": median([row["native"]["retries_scheduled"] for row in rows]),
                        "median_run_median_first_generation_ms": median([row["native"]["median_first_generation_event_ms"] for row in rows]),
                        "median_run_median_text_ttft_ms": median([row["native"]["median_first_text_ms"] for row in rows]),
                        "text_ttft_samples": sum(row["native"]["text_ttft_samples"] for row in rows),
                        "median_run_median_output_tokens_per_second": median([row["native"]["median_observed_output_tokens_per_second"] for row in rows]),
                        "median_run_median_end_to_end_output_tokens_per_second": median([row["native"]["median_end_to_end_output_tokens_per_second"] for row in rows]),
                        "end_to_end_output_rate_samples": sum(row["native"]["end_to_end_output_rate_samples"] for row in rows),
                        "reported_usage_samples": len(protocol_rows),
                        "median_reported_input_including_cache": median([inclusive_input(row) for row in protocol_rows]),
                        "median_reported_cached_input": median([row["usage"]["cached_input_tokens"] for row in protocol_rows]),
                        "median_reported_output": median([row["usage"]["output_tokens"] for row in protocol_rows]),
                        "native_usage_coverage": dict(Counter(row["native"]["usage_coverage"] for row in rows)),
                        "protocol_usage_coverage": dict(Counter(row["protocol"]["usage_coverage"] for row in rows)),
                        "evidence": [row["run_id"] + "/result.json" for row in rows]})
    return summary


def write_summary(destination, results):
    summary = summarize(results)
    write_json(destination / "summary.json", summary)
    def seconds(value):
        return "—" if value is None else f"{value / 1000:.3f}s"
    lines = ["# Local evaluation results", "", "Elapsed spans process launch through process-group cleanup; output drain/parsing and verifier time are separate.",
             "Passes require normal process/protocol completion, independent verification and unchanged provided tests. All attempts remain in evidence.",
             "These are task/configuration comparisons; Claude also changes the model. Missing metrics are unavailable, not zero.", "",
             "Token columns are medians of complete reported protocol usage: full input / cached input subset / output. Missing cache details leave full input unavailable. Sampler events provide separate request-level evidence; auxiliary coverage differs.", "",
             "Text TTFT, first output and rates are medians of per-run request medians. Text TTFT samples count attempts with visible text; text-free attempts stay unavailable. First output includes text, reasoning or tool deltas. Request tok/s divides reported output tokens by the whole attempt duration including initial wait. Generation-window tok/s uses a client-observed window and is absent when reasoning accounting or streamed events are insufficient. Neither rate is server decode speed.", "",
             "| Task | Profile | Passes / attempts | Median all (range) | Median passed | Text TTFT (n) | First output | Request tok/s (n) | Generation-window tok/s | Native requests / retries | Tokens I / C / O | Usage samples | Evidence |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in summary:
        show = lambda value: "—" if value is None else str(value)
        tokens = " / ".join(show(row[key]) for key in ("median_reported_input_including_cache", "median_reported_cached_input", "median_reported_output"))
        evidence = ", ".join(f"[{i + 1}]({path})" for i, path in enumerate(row["evidence"]))
        rate = row["median_run_median_output_tokens_per_second"]
        generation_rate = "—" if rate is None else f"{rate:.1f}"
        request_rate = row["median_run_median_end_to_end_output_tokens_per_second"]
        request_rate = "—" if request_rate is None else f"{request_rate:.1f}"
        lines.append(f"| {row['task']} | {row['profile']} | {row['successful_verified_runs']} / {row['attempts']} | "
                     f"{seconds(row['median_all_elapsed_ms'])} ({seconds(row['min_all_elapsed_ms'])}–{seconds(row['max_all_elapsed_ms'])}) | "
                     f"{seconds(row['median_successful_elapsed_ms'])} | {seconds(row['median_run_median_text_ttft_ms'])} ({row['text_ttft_samples']}) | {seconds(row['median_run_median_first_generation_ms'])} | {request_rate} ({row['end_to_end_output_rate_samples']}) | {generation_rate} | {show(row['median_observed_requests'])} / {show(row['median_observed_retries'])} | {tokens} | {row['reported_usage_samples']} / {row['attempts']} | {evidence} |")
    (destination / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the five fixed workloads without running models")
    report = sub.add_parser("report", help="regenerate summary from retained result metadata")
    report.add_argument("output", type=Path)
    run = sub.add_parser("run", help="execute explicitly selected profiles; may use paid/account quota")
    run.add_argument("--profiles", type=Path, default=HERE / "profiles.example.json")
    run.add_argument("--profile", action="append", required=True)
    run.add_argument("--task", action="append", choices=sorted(TASKS))
    run.add_argument("--repeats", type=int, default=3)
    run.add_argument("--timeout", type=float, default=600)
    run.add_argument("--verifier-timeout", type=float, default=30)
    run.add_argument("--max-turns", type=int, default=20)
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "list":
        for task, value in TASKS.items():
            print(task + ": " + value["category"])
        return 0
    if args.command == "report":
        results = [json.loads(path.read_text()) for path in sorted(args.output.glob("*/result.json"))]
        write_summary(args.output, results)
        return 0
    if os.name != "posix":
        parser.error("initial runner requires POSIX process groups (macOS/Linux)")
    if min(args.repeats, args.timeout, args.verifier_timeout, args.max_turns) <= 0:
        parser.error("repeat counts, timeouts and turn limits must be positive")
    try:
        profiles = load_profiles(args.profiles)
    except (ValueError, OSError, IndexError) as error:
        parser.error(str(error))
    if len(set(args.profile)) != len(args.profile) or any(name not in profiles for name in args.profile):
        parser.error("selected profiles must exist and be unique")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output directory must be empty; existing evidence is never overwritten")
    args.output.mkdir(parents=True, exist_ok=True)
    limits = {"timeout_seconds": args.timeout, "verifier_timeout_seconds": args.verifier_timeout,
              "max_turns": args.max_turns, "external_max_turns": "profile-dependent; Codex has no equivalent flag"}
    selected = [profiles[name] for name in args.profile]
    identities = {profile["id"]: profile_identity(profile) for profile in selected}
    source = source_identity()
    for identity in identities.values():
        identity["source"] = source
    results = []
    for index, (profile, task, repetition) in enumerate(schedule(selected, args.task or list(TASKS), args.repeats)):
        print(f"{index + 1}: {profile['id']} / {task} / repeat {repetition}", flush=True)
        result = run_attempt(profile, task, repetition, index + 1, args.output, limits, identities[profile["id"]])
        results.append(result)
        write_summary(args.output, results)
        if result["execution"]["status"] == "cancelled":
            return 130
    print(str(args.output / "RESULTS.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
