#!/usr/bin/env python3
"""One local Harbor pilot, private raw jobs, allowlisted result export."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from preflight import auth_mount, binary_identity, require_harbor, sampler_error_metadata

HERE = Path(__file__).resolve().parent


def public_forge_report(report):
    """Second allowlist boundary: even a modified container log cannot export text."""
    if not isinstance(report, dict):
        return None
    def numbers(value):
        if not isinstance(value, dict):
            return {}
        return {key: value.get(key) if type(value.get(key)) in (int, float)
                and abs(value[key]) < float("inf") else None for key in numeric_fields if key in value}
    numeric_fields = {"exit_code", "signal", "duration_ms", "leader_exit_ms", "cleanup_ms", "input_tokens",
                      "output_tokens", "cached_input_tokens", "cache_creation_input_tokens", "reasoning_tokens",
                      "total_tokens", "request_count", "attempt_count", "retries_scheduled", "terminal_events",
                      "median_request_duration_ms", "median_first_generation_event_ms", "median_first_text_ms",
                      "text_ttft_samples", "median_observed_output_tokens_per_second",
                      "median_end_to_end_output_tokens_per_second", "end_to_end_output_rate_samples",
                      "first_generation_samples", "output_rate_samples", "median_duration_ms", "samples",
                      "first_response_event_ms", "first_text_ms", "first_reasoning_ms", "first_tool_delta_ms",
                      "first_generation_event_ms", "generated_content_event_count", "attempt",
                      "observed_output_tokens_per_second", "end_to_end_output_tokens_per_second",
                      "cache_tokens", "discarded_records"}
    numeric_fields |= {"observed_" + key for key in ("input_tokens", "output_tokens", "cached_input_tokens",
                                                   "cache_creation_input_tokens", "reasoning_tokens", "total_tokens")}
    native, protocol, process = (report.get(key) or {} for key in ("native", "protocol", "process"))
    projected = {"metadata_complete": report.get("metadata_complete") is True,
                 "process": numbers(process), "protocol": numbers(protocol), "native": numbers(native),
                 "harbor_usage": numbers(report.get("harbor_usage")), "requests": [],
                 "collection": numbers(report.get("collection"))}
    try:
        projected["run_id"] = str(uuid.UUID(report["run_id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        projected["metadata_complete"] = False
    for key in ("duplicate_attempts", "collector_reported_loss", "collector_health_complete"):
        projected["native"][key] = native.get(key) if type(native.get(key)) is bool else None
    for section, source in (("native", native), ("protocol", protocol)):
        projected[section]["usage"] = numbers(source.get("usage"))
    for key in ("request_build", "sampler_prepare", "stream_drain", "tool_prepare", "tool_dispatch", "headless", "prompt"):
        projected["native"].setdefault("phase_summary", {})[key] = numbers((native.get("phase_summary") or {}).get(key))
    projected["process"]["completed"] = process.get("status") == "completed"
    projected["protocol"]["completed"] = protocol.get("status") == "completed"
    for row in report.get("requests", [])[:10000]:
        if isinstance(row, dict):
            projected["requests"].append({**numbers(row), **sampler_error_metadata(row), "usage_is_final": row.get("usage_is_final") is True,
                                          "provider_usage": numbers(row.get("provider_usage"))})
    usage = projected["harbor_usage"]
    usage_valid = all(type(usage.get(key)) is int and usage[key] >= 0
                      for key in ("input_tokens", "cache_tokens", "output_tokens"))
    projected["metadata_complete"] &= (usage_valid and usage.get("cache_tokens", 0) <= usage.get("input_tokens", -1)
                                       and projected["native"].get("collector_health_complete") is True
                                       and projected["native"].get("collector_reported_loss") is False
                                       and projected["native"].get("duplicate_attempts") is False
                                       and protocol.get("terminal_events") == 1
                                       and bool(projected["requests"])
                                       and all(row["usage_is_final"] for row in projected["requests"]))
    return projected


def elapsed_ms(timing):
    try:
        start = datetime.fromisoformat(timing["started_at"])
        end = datetime.fromisoformat(timing["finished_at"])
        value = (end - start).total_seconds() * 1000
        return value if value >= 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def failure_timing(raw):
    """Locate exception recording without treating a later verifier as its cause."""
    def timestamp(value):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.utcoffset() is not None else None
        except (TypeError, ValueError):
            return None
    phases = []
    for name in ("environment_setup", "agent_setup", "agent_execution", "verifier"):
        timing = raw.get(name) or {}
        start, end = timestamp(timing.get("started_at")), timestamp(timing.get("finished_at"))
        if start is not None and (end is None or end >= start):
            phases.append((start, end, name))
    phases.sort(key=lambda phase: phase[0])
    result = {"stage": "unknown", "last_started_phase": phases[-1][2] if phases else "unknown",
              "preceding_phase": None, "next_phase": None}
    occurred = timestamp((raw.get("exception_info") or {}).get("occurred_at"))
    if occurred is None or not phases:
        return result
    active = [phase for phase in phases if phase[0] <= occurred and (phase[1] is None or occurred <= phase[1])]
    if active:
        result["stage"] = active[-1][2]
    elif occurred < phases[0][0]:
        result["stage"] = "initialization"
        result["next_phase"] = phases[0][2]
    else:
        # Harbor closes the agent interval in finally before recording its error.
        # Keep this gap explicit instead of assigning that error to the verifier.
        previous = [phase for phase in phases if phase[1] is not None and phase[1] < occurred]
        following = [phase for phase in phases if phase[0] > occurred]
        result["stage"] = "between_recorded_phases" if following else "after_recorded_phases"
        result["preceding_phase"] = max(previous, key=lambda phase: phase[1])[2] if previous else None
        result["next_phase"] = following[0][2] if following else None
    return result


def export_trial(raw, forge_report, oracle=False):
    forge_report = public_forge_report(forge_report)
    rewards = (raw.get("verifier_result") or {}).get("rewards") or {}
    reward = rewards.get("reward")
    if type(reward) not in (int, float) or not 0 <= reward <= 1:
        reward = None
    exception = raw.get("exception_info") is not None
    failure = None
    if exception:
        kind = (raw.get("exception_info") or {}).get("exception_type")
        allowed = {"RuntimeError", "ValueError", "FileNotFoundError", "PermissionError",
                   "AgentSetupTimeoutError", "AgentTimeoutError", "VerifierTimeoutError",
                   "EnvironmentStartTimeoutError", "NonZeroAgentExitCodeError", "ApiError", "ApiRateLimitError"}
        failure = {**failure_timing(raw), "category": kind if kind in allowed else "other_harbor_exception"}
    metadata_ok = oracle or bool(forge_report and forge_report.get("metadata_complete") is True
                               and forge_report["process"]["completed"] and forge_report["protocol"]["completed"])
    return {"schema_version": 1, "harbor_version": "0.23.0", "oracle": oracle,
            "harbor_exception": exception, "harbor_failure": failure, "verifier_reward": reward,
            "verified_success": reward == 1 and not exception and metadata_ok,
            "metadata_complete": metadata_ok,
            "timing": {"trial_ms": elapsed_ms(raw),
                       **{key + "_ms": elapsed_ms(raw.get(key) or {})
                          for key in ("environment_setup", "agent_setup", "agent_execution", "verifier")}},
            "forge": forge_report,
            "privacy": {"raw_harbor_job_is_private": True, "raw_harbor_job_retained": False,
                        "mount_source_path_exported": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--oracle", action="store_true")
    parser.add_argument("--forge-binary", type=Path)
    parser.add_argument("--profile", default="forge-baseline")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--architecture", choices=("aarch64", "x86_64"), default="aarch64")
    parser.add_argument("--auth-file", type=Path, default=Path.home() / ".codex" / "auth.json")
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=110)
    args = parser.parse_args()
    require_harbor()
    launcher_sources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (HERE / "launch.py", HERE / "preflight.py")}
    task = args.task.resolve(strict=True)
    if not (task / "task.toml").is_file():
        parser.error("--task must be one Harbor task directory")
    if args.output.exists():
        parser.error("--output must not already exist")
    if not args.oracle and not args.forge_binary:
        parser.error("--forge-binary is required unless --oracle is selected")
    expected_platform = "linux/" + {"aarch64": "aarch64", "x86_64": "x86_64"}[args.architecture]
    daemon = subprocess.run(["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
                            capture_output=True, text=True, timeout=15)
    actual_platform = daemon.stdout.strip().replace("/arm64", "/aarch64").replace("/amd64", "/x86_64")
    if daemon.returncode or actual_platform != expected_platform:
        parser.error("Docker must be running the declared native Linux architecture")
    initial_task_digest = task_digest(task)
    binary = binary_identity(args.forge_binary, args.architecture) if not args.oracle else None
    build_provenance = None
    if not args.oracle:
        manifest_path = args.forge_binary.parent / "forge.build.json"
        if not manifest_path.is_file():
            parser.error("Build provenance missing; use eval/harbor/build.py to build Forge")
        build_provenance = json.loads(manifest_path.read_text())
        if build_provenance.get("binary") != binary:
            parser.error("Build provenance does not match the Forge binary")
    # No auth content is opened, parsed, copied, or put in argv/environment.
    mounts = [auth_mount(args.auth_file)] if not args.oracle else []
    harbor = Path(sys.executable).parent / "harbor"
    with tempfile.TemporaryDirectory(prefix="forge-harbor-private-") as directory:
        private = Path(directory)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        command = [str(harbor), "run", "-p", str(task), "-e", "docker", "-n", "1", "-k", "1", "-r", "0",
                   "-o", str(private / "jobs"), "--job-name", "pilot", "--delete", "--force-build", "-y"]
        if args.oracle:
            command += ["-a", "oracle"]
        else:
            command += ["-a", "adapter:ForgeInstalledAgent", "-m", args.model,
                        "--mounts", json.dumps(mounts),
                        "--ak", "forge_binary=" + str(args.forge_binary.resolve()),
                        "--ak", "profile=" + args.profile,
                        "--ak", "architecture=" + args.architecture,
                        "--ak", "max_turns=" + str(args.max_turns),
                        "--ak", "timeout_seconds=" + str(args.timeout_seconds)]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(HERE)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Keep Harbor's diagnostic output private: it may mention mount source paths.
        with (private / "harbor.log").open("wb") as log:
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        trials = list((private / "jobs" / "pilot").glob("*/result.json"))
        if len(trials) != 1:
            report = {"schema_version": 1, "harbor_version": "0.23.0", "harbor_exit_code": result.returncode,
                      "verified_success": False, "metadata_complete": False, "error": "missing_single_trial_result"}
        else:
            raw = json.loads(trials[0].read_text())
            metadata = trials[0].parent / "agent" / "forge" / "metadata.json"
            forge_report = json.loads(metadata.read_text()) if metadata.is_file() and not metadata.is_symlink() else None
            report = export_trial(raw, forge_report, args.oracle)
            report["harbor_exit_code"] = result.returncode
            report["verified_success"] &= result.returncode == 0
        report["task_files_sha256"] = initial_task_digest
        report["task_files_unchanged"] = task_digest(task) == initial_task_digest
        report["verified_success"] &= report["task_files_unchanged"]
        report["environment"] = {"platform": expected_platform, "force_build_from_task_dockerfile": True,
                                 "attempts": 1, "concurrency": 1, "retries": 0}
        report["binary"] = binary
        report["build_provenance"] = build_provenance
        report["launcher_sources_sha256"] = launcher_sources
        if not args.oracle:
            from adapter import load_profile, make_spec
            report["configuration"] = make_spec(load_profile(args.profile, args.model), binary, "unused",
                                                 args.max_turns, args.timeout_seconds)["identity"]
        report["metric_scope"] = {"input_tokens": "includes cached input; cache is a subset",
                                  "usage": "headless prompt ledger; auxiliary direct calls excluded",
                                  "native_usage": "sampler actor only; includes cached input and reasoning output",
                                  "rates": "client observed approximation; not server decode speed",
                                  "phases": "overlapping client intervals; not additive wall time",
                                  "cost_usd": "unavailable; subscription usage is not an observed bill"}
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"verified_success": report["verified_success"], "metadata_complete": report["metadata_complete"]}))
    return 0 if report["verified_success"] else 1


def task_digest(task):
    digest = hashlib.sha256()
    for path in sorted(task.rglob("*")):
        if path.is_symlink():
            raise ValueError("Pilot task content must not contain symlinks")
        if path.is_file():
            name = path.relative_to(task).as_posix().encode()
            data = path.read_bytes()
            digest.update(len(name).to_bytes(8, "big") + name + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
