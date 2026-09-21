"""Run inside a Harbor container. Never print Forge output or exception details."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

# Shared, tested metadata parsers; these three source files are installed alongside us.
import run as evaluation
from preflight import sampler_error_metadata


def safe_requests(events):
    fields = ("duration_ms", "first_response_event_ms", "first_text_ms", "first_reasoning_ms",
              "first_tool_delta_ms", "first_generation_event_ms", "generated_content_event_count",
              "observed_output_tokens_per_second", "end_to_end_output_tokens_per_second", "attempt")
    result = []
    for event in events:
        if event["event"] != "sampler_attempt_finished":
            continue
        row = {key: evaluation.number(event.get(key)) for key in fields}
        row.update(sampler_error_metadata(event))
        row["usage_is_final"] = event.get("usage_is_final") is True
        row["provider_usage"] = {key: evaluation.number((event.get("provider_usage") or {}).get(key))
                                 for key in evaluation.USAGE_FIELDS}
        result.append(row)
    return result


def build_report(state, stdout, events, collection, run_id, identity):
    process_ok = state.get("status") == "completed"
    protocol = evaluation.protocol_metadata("forge", stdout, not state.get("output_truncated", False))
    native = evaluation.native_metrics(events, collection, process_ok)
    # These maps contain string values from the observation stream; keep the export numeric.
    native.pop("observed_models", None)
    native.pop("throughput_unavailable_reasons", None)
    usage = protocol["usage"]
    parts = [usage.get(key) for key in ("input_tokens", "cached_input_tokens", "cache_creation_input_tokens")]
    complete = (process_ok and protocol["status"] == "completed" and protocol["terminal_events"] == 1
                and protocol["usage_coverage"] == "complete_for_reported_scope"
                and native["usage_coverage"] == "complete_for_observed_sampler_attempts"
                and all(type(value) is int for value in parts)
                and type(usage.get("output_tokens")) is int)
    return {"schema_version": 1, "run_id": run_id, "identity": identity,
            "process": state, "protocol": protocol, "native": native,
            "requests": safe_requests(events), "collection": collection, "metadata_complete": complete,
            "harbor_usage": {"input_tokens": sum(parts) if complete else None,
                             "cache_tokens": usage.get("cached_input_tokens") if complete else None,
                             "output_tokens": usage.get("output_tokens") if complete else None,
                             "cost_usd": None},
            "verifier_reward": None,
            "privacy": {"raw_stdout_stderr_retained": False, "credential_file_copied": False,
                        "authentication": "runtime_read_only_file_mount",
                        "task_processes_can_read_mounted_credentials": True}}


def run(spec_path):
    spec = json.loads(Path(spec_path).read_text())
    run_id = str(uuid.uuid4())
    output = Path("/logs/agent/forge")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="forge-harbor-") as temporary:
        root = Path(temporary)
        home = root / "forge-home"
        home.mkdir()
        (home / "config.toml").write_text(evaluation.forge_config(spec["model"]))
        observations = root / "observations.jsonl"
        env = os.environ.copy()
        for key in evaluation.ALLOWED_ENV | {"CODEX_ACCESS_TOKEN", "OPENAI_CODEX_TOKEN"}:
            env.pop(key, None)
        env.update(spec["env"])
        env.update(GROK_HOME=str(home), FORGE_OBSERVATION_FILE=str(observations),
                   FORGE_RUN_ID=run_id, GROK_DISABLE_AUTOUPDATER="1", PYTHONDONTWRITEBYTECODE="1")
        state, stdout = evaluation.execute(spec["argv"], Path.cwd(), env, spec["timeout_seconds"])
        events, collection = evaluation.read_observations(observations, run_id)
        report = build_report(state, stdout, events, collection, run_id, spec["identity"])
        # No native event strings or arbitrary CLI output are persisted by this wrapper.
        evaluation.write_json(output / "metadata.json", report)
    return 0 if report["metadata_complete"] else 75


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("spec")
    args = parser.parse_args()
    try:
        code = run(args.spec)
    except Exception:
        # Exception text can include provider bodies or paths. Fail closed without it.
        print("Forge Harbor wrapper failed before complete metadata was available", file=sys.stderr)
        code = 76
    raise SystemExit(code)
