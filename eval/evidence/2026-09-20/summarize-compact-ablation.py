#!/usr/bin/env python3
"""Summarize the fixed 45-trial compact-description experiment from saved metadata.

No imports from the checkout, subprocesses, model calls, or evidence writes.
Usage: python3 summarize-compact-ablation.py EVIDENCE_DIR [--allow-partial]
JSON goes to stdout. Incomplete campaigns fail unless --allow-partial is explicit.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics

PROFILES = ("forge-optimized", "forge-compact-descriptions-only", "codex-direct")
TASKS = ("bug-fix", "exploration-change", "multi-file-feature", "refactor", "test-diagnosis")
REPETITIONS = (1, 2, 3)
EXPECTED = {(profile, task, repeat) for profile in PROFILES for task in TASKS for repeat in REPETITIONS}
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def distribution(values):
    """Reported values only; zero samples has null statistics, never a zero total."""
    observed = [value for value in values if value is not None]
    return {"samples": len(observed), "unavailable_samples": len(values) - len(observed),
            "median": statistics.median(observed) if observed else None,
            "min": min(observed) if observed else None, "max": max(observed) if observed else None}


def accounting(values):
    observed = [value for value in values if value is not None]
    return {"sum_complete_samples": sum(observed) if observed else None,
            "complete_samples": len(observed), "unavailable_samples": len(values) - len(observed),
            "all_trials_available": bool(values) and len(observed) == len(values)}


def variants(values):
    counts = Counter(json.dumps(value, sort_keys=True, separators=(",", ":")) for value in values)
    return [{"value": json.loads(value), "samples": count} for value, count in sorted(counts.items())]


def known_variants(values):
    return {"values": variants([value for value in values if value is not None]),
            "samples": sum(value is not None for value in values),
            "unavailable_samples": sum(value is None for value in values)}


def process_ok(result):
    return result["execution"]["status"] == "completed" and result["execution"].get("output_truncated") is False


def wall_seconds(row):
    duration = number(row["result"]["execution"].get("duration_ms"))
    return duration / 1000 if duration is not None else None


def protocol_usage(row, field):
    result = row["result"]
    protocol = result["protocol"]
    if protocol.get("usage_coverage") != "complete_for_reported_scope" or not process_ok(result):
        return None
    usage = protocol.get("usage", {})
    if field == "full_input_tokens":
        if result["kind"] == "forge":
            if "uncached_input" not in protocol.get("usage_scope", ""):
                return None
            parts = [number(usage.get(key)) for key in ("input_tokens", "cached_input_tokens", "cache_creation_input_tokens")]
            return sum(parts) if all(value is not None for value in parts) else None
        if result["kind"] == "codex" and "input_includes_cache" in protocol.get("usage_scope", ""):
            return number(usage.get("input_tokens"))
        return None
    return number(usage.get(field))


def native_usage(row, field):
    native = row["result"]["native"]
    if native.get("usage_coverage") != "complete_for_observed_sampler_attempts" or not process_ok(row["result"]):
        return None
    return number(native.get("usage", {}).get(field))


def events_of(row, event):
    return [item for item in row["events"] if item.get("event") == event]


def request_key(event):
    return event.get("process_id"), event.get("request_id")


def complete_requests(row):
    result, native = row["result"], row["result"]["native"]
    starts, finishes = events_of(row, "sampler_request_started"), events_of(row, "sampler_request_finished")
    start_keys, finish_keys = [request_key(item) for item in starts], [request_key(item) for item in finishes]
    if (not starts or len(set(start_keys)) != len(starts) or len(set(finish_keys)) != len(finishes)
            or set(start_keys) != set(finish_keys) or any(None in key for key in start_keys)
            or native.get("collector_health_complete") is not True or native.get("collector_reported_loss") is not False
            or native.get("duplicate_attempts") is not False or result["collection"].get("status") != "observed"
            or not process_ok(result) or native.get("request_count") != len(finishes)):
        return None
    return len(finishes)


def first_request_input(row):
    # An incomplete collector may have dropped the actual first request.
    if complete_requests(row) is None:
        return None
    # A failed first attempt does not silently become a later retry's input.
    starts = events_of(row, "sampler_request_started")
    if not starts:
        return None
    first = min(enumerate(starts), key=lambda pair: (pair[1].get("timestamp_unix_ms", math.inf),
                                                    pair[1].get("elapsed_ms", math.inf), pair[0]))[1]
    matches = [event for event in events_of(row, "sampler_attempt_finished")
               if request_key(event) == request_key(first) and event.get("attempt") == 1]
    if len(matches) != 1 or matches[0].get("usage_is_final") is not True:
        return None
    return number((matches[0].get("provider_usage") or {}).get("input_tokens"))


def failure_labels(result):
    labels = []
    if result["execution"]["status"] != "completed":
        labels.append("process:" + result["execution"]["status"])
    if result["execution"].get("output_truncated"):
        labels.append("output:truncated")
    if result["protocol"]["status"] != "completed":
        labels.append("protocol:" + result["protocol"]["status"])
    if result["verification"].get("passed") is not True:
        labels.append("verifier:" + result["verification"]["status"])
    if result.get("constraints", {}).get("provided_tests_unchanged") is not True:
        labels.append("constraint:provided_tests_changed_or_unobserved")
    return labels


def load_runs(directory, allow_partial):
    rows, skipped, hashes = [], [], []
    for path in sorted(directory.glob("*/result.json")):
        try:
            raw = {name: path.with_name(name).read_bytes() for name in ("manifest.json", "result.json", "events.jsonl")}
            manifest, result = json.loads(raw["manifest.json"]), json.loads(raw["result.json"])
            events = [json.loads(line) for line in raw["events.jsonl"].splitlines() if line.strip()]
        except (OSError, ValueError) as error:
            if not allow_partial:
                raise ValueError(f"Incomplete/unreadable retained evidence: {path.parent.name} ({type(error).__name__})") from error
            skipped.append({"run_directory": path.parent.name, "reason": type(error).__name__})
            continue
        for key in ("run_id", "profile", "task", "repetition", "execution_order", "kind"):
            if manifest.get(key) != result.get(key):
                raise ValueError(f"Manifest/result mismatch for {key}: {path.parent.name}")
        if result["run_id"] != path.parent.name or any(event.get("run_id") != result["run_id"] for event in events):
            raise ValueError(f"Run identity mismatch: {path.parent.name}")
        expected_success = (process_ok(result) and result["protocol"]["status"] == "completed"
                            and result["verification"].get("passed") is True
                            and result.get("constraints", {}).get("provided_tests_unchanged") is True)
        if result.get("successful_verified_run") is not expected_success:
            raise ValueError(f"Success verdict inconsistent with retained checks: {path.parent.name}")
        if number(result["execution"].get("duration_ms")) is None:
            raise ValueError(f"Missing/invalid measured process duration: {path.parent.name}")
        rows.append({"manifest": manifest, "result": result, "events": events,
                     "evidence": path.relative_to(directory).as_posix()})
        for name, content in raw.items():
            hashes.append((path.parent.name + "/" + name, hashlib.sha256(content).hexdigest()))
    rows.sort(key=lambda row: row["result"]["execution_order"])
    keys = [(row["result"]["profile"], row["result"]["task"], row["result"]["repetition"]) for row in rows]
    orders = [row["result"]["execution_order"] for row in rows]
    if len(set(keys)) != len(keys) or set(keys) - EXPECTED or len(set(orders)) != len(orders):
        raise ValueError("Duplicate trials/order or unexpected profile/task/repetition")
    if any(type(order) is not int or order < 1 or order > 45 for order in orders):
        raise ValueError("Execution orders must be distinct integers in 1..45")
    missing = [{"profile": profile, "task": task, "repetition": repeat} for profile, task, repeat in sorted(EXPECTED - set(keys))]
    if not allow_partial and missing:
        raise ValueError(f"Expected exactly 45 trials; found {len(rows)} ({len(missing)} missing). Use --allow-partial for progress.")
    fingerprint = hashlib.sha256(json.dumps(sorted(hashes), separators=(",", ":")).encode()).hexdigest()
    return rows, {"expected_trials": 45, "loaded_trials": len(rows), "complete": not missing,
                  "missing_trials": missing, "skipped_incomplete_evidence": skipped,
                  "manifest_without_result": sorted(p.parent.name for p in directory.glob("*/manifest.json") if not p.with_name("result.json").exists()),
                  "input_evidence_sha256": fingerprint}


def profile_summary(rows):
    results = [row["result"] for row in rows]
    passes = [row for row in rows if row["result"]["successful_verified_run"]]
    failures = [{"run_id": result["run_id"], "task": result["task"], "repetition": result["repetition"],
                 "labels": failure_labels(result), "failed_checks": result["verification"].get("failed_checks", []),
                 "verifier_error_category": result["verification"].get("error_category")}
                for result in results if not result["successful_verified_run"]]
    usage = {field: accounting([protocol_usage(row, field) for row in rows])
             for field in ("full_input_tokens", "cached_input_tokens", "output_tokens")}
    native = {field: accounting([native_usage(row, field) for row in rows])
              for field in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")}
    native["request_count"] = accounting([complete_requests(row) for row in rows])
    native["usage_coverage"] = dict(Counter(result["native"]["usage_coverage"] for result in results))
    native["collection_coverage"] = dict(Counter(result["collection"]["status"] for result in results))
    attempts = [event for row in rows for event in events_of(row, "sampler_attempt_finished")]
    native["pooled_attempt_timings"] = {field: distribution([number(event.get(field)) for event in attempts])
        for field in ("duration_ms", "first_text_ms", "first_generation_event_ms", "first_response_event_ms",
                      "end_to_end_output_tokens_per_second", "observed_output_tokens_per_second")}
    native["throughput_unavailable_reasons"] = dict(Counter(event["throughput_unavailable_reason"] for event in attempts
                                                            if event.get("throughput_unavailable_reason")))
    native["attempt_outcomes"] = dict(Counter(event.get("outcome", "unobserved") for event in attempts))
    native["attempt_error_kinds"] = dict(Counter(event["error_kind"] for event in attempts if event.get("error_kind")))
    phase_groups = defaultdict(list)
    for row in rows:
        for event in events_of(row, "phase_end"):
            phase_groups[event.get("phase", "unobserved")].append(event)
    native["phases"] = {phase: {"duration_ms": distribution([number(event.get("duration_ms")) for event in events]),
                                 "statuses": dict(Counter(event.get("status", "unobserved") for event in events))}
                        for phase, events in sorted(phase_groups.items())}
    requests = [event for row in rows for event in events_of(row, "sampler_request_started")]
    contexts = [event for row in rows for event in events_of(row, "request_context")]
    models = [event.get("model") for event in requests]
    efforts = [(event.get("requested_config") or {}).get("reasoning_effort") for event in requests]
    mechanism = {"tool_description_bytes": known_variants([number(event.get("tool_description_bytes")) for event in contexts]),
                 "requested_tool_count": known_variants([number((event.get("requested_config") or {}).get("tool_count")) for event in requests]),
                 "first_request_first_attempt_input_tokens": distribution([first_request_input(row) for row in rows]),
                 "observed_models": known_variants(models), "observed_reasoning_efforts": known_variants(efforts),
                 "model_matches_declared": all(event.get("model") == row["manifest"]["configuration"]["model"] for row in rows for event in events_of(row, "sampler_request_started")) if requests else None,
                 "effort_matches_declared": all((event.get("requested_config") or {}).get("reasoning_effort") == row["manifest"]["configuration"]["reasoning_effort"] for row in rows for event in events_of(row, "sampler_request_started")) if requests else None}
    per_task = {}
    for task in TASKS:
        task_rows = [row for row in rows if row["result"]["task"] == task]
        task_passes = [row for row in task_rows if row["result"]["successful_verified_run"]]
        per_task[task] = {"attempts": len(task_rows), "passes": len(task_passes),
                          "wall_seconds_all": distribution([wall_seconds(row) for row in task_rows]),
                          "wall_seconds_successful": distribution([wall_seconds(row) for row in task_passes]),
                          "first_request_input_tokens": distribution([first_request_input(row) for row in task_rows])}
    return {"attempts": len(rows), "planned_attempts": 15, "passes": len(passes),
            "success_rate_loaded_trials": len(passes) / len(rows) if rows else None,
            "failures": failures, "failure_label_counts": dict(Counter(label for failure in failures for label in failure["labels"])),
            "wall_seconds_all": distribution([wall_seconds(row) for row in rows]),
            "wall_seconds_all_total": sum(wall_seconds(row) for row in rows) if rows else None,
            "wall_seconds_successful": distribution([wall_seconds(row) for row in passes]),
            "protocol_usage": usage, "protocol_usage_coverage": dict(Counter(result["protocol"]["usage_coverage"] for result in results)),
            "protocol_usage_scopes": variants([result["protocol"]["usage_scope"] for result in results]),
            "native": native, "mechanism": mechanism, "per_task": per_task,
            "evidence": [row["evidence"] for row in rows]}


def percent_change(candidate, reference):
    return 100 * (candidate / reference - 1) if candidate is not None and reference is not None and reference > 0 else None


def paired(rows, candidate_profile, reference_profile):
    by_key = {(row["result"]["profile"], row["result"]["task"], row["result"]["repetition"]): row for row in rows}
    comparisons, outcomes, precedence = [], Counter(), Counter()
    for task in TASKS:
        for repeat in REPETITIONS:
            candidate, reference = by_key.get((candidate_profile, task, repeat)), by_key.get((reference_profile, task, repeat))
            if candidate is None or reference is None:
                outcomes["missing_one_or_both"] += 1
                continue
            cp, rp = candidate["result"]["successful_verified_run"], reference["result"]["successful_verified_run"]
            outcome = "both_successful" if cp and rp else "candidate_only_successful" if cp else "reference_only_successful" if rp else "both_failed"
            outcomes[outcome] += 1
            precedence["candidate_first" if candidate["result"]["execution_order"] < reference["result"]["execution_order"] else "reference_first"] += 1
            if not (cp and rp):
                continue
            cw, rw = wall_seconds(candidate), wall_seconds(reference)
            comparisons.append({"task": task, "repetition": repeat, "candidate": candidate["evidence"], "reference": reference["evidence"],
                                "candidate_wall_seconds": cw, "reference_wall_seconds": rw,
                                "wall_percent_change": percent_change(cw, rw),
                                "full_input_percent_change": percent_change(protocol_usage(candidate, "full_input_tokens"), protocol_usage(reference, "full_input_tokens")),
                                "output_percent_change": percent_change(protocol_usage(candidate, "output_tokens"), protocol_usage(reference, "output_tokens"))})
    comparable = [pair for pair in comparisons if pair["wall_percent_change"] is not None]
    return {"candidate": candidate_profile, "reference": reference_profile, "planned_pairs": 15,
            "pair_outcomes": {key: outcomes[key] for key in ("both_successful", "candidate_only_successful", "reference_only_successful", "both_failed", "missing_one_or_both")},
            "precedence_all_matched_pairs": dict(precedence), "speed_comparison_pairs": len(comparable),
            "candidate_faster": sum(pair["wall_percent_change"] < 0 for pair in comparable),
            "candidate_slower": sum(pair["wall_percent_change"] > 0 for pair in comparable),
            "ties": sum(pair["wall_percent_change"] == 0 for pair in comparable),
            "wall_percent_change_both_successful": distribution([pair["wall_percent_change"] for pair in comparisons]),
            "full_input_percent_change_both_successful": distribution([pair["full_input_percent_change"] for pair in comparisons]),
            "output_percent_change_both_successful": distribution([pair["output_percent_change"] for pair in comparisons]),
            "per_task_wall_percent_change": {task: distribution([pair["wall_percent_change"] for pair in comparisons if pair["task"] == task]) for task in TASKS},
            "both_successful_pairs": comparisons}


def native_env_differs_only_by_compact_flag(grouped):
    """Require stable native profiles and exactly the planned declared-env delta."""
    configs = []
    for profile in PROFILES[:2]:
        rows = grouped.get(profile, [])
        values = [row.get("manifest", {}).get("configuration") for row in rows]
        if not values or any(not isinstance(value, dict) for value in values) or len(variants(values)) != 1:
            return None
        configs.append(values[0])
    reference, candidate = (config.get("declared_optimization_env") for config in configs)
    if not isinstance(reference, dict) or not isinstance(candidate, dict):
        return None
    flag = "FORGE_COMPACT_TOOL_DESCRIPTIONS"
    return (flag not in reference and candidate.get(flag) == "1"
            and reference == {key: value for key, value in candidate.items() if key != flag})


def identity_and_order(rows, complete):
    grouped = {profile: [row for row in rows if row["result"]["profile"] == profile] for profile in PROFILES}
    profile_checks = {}
    for profile, group in grouped.items():
        software = [row["manifest"]["software"] for row in group]
        configs = variants([row["manifest"]["configuration"] for row in group])
        binaries = known_variants([value.get("executable_sha256") for value in software])
        positions = Counter((row["result"]["execution_order"] - 1) % 3 + 1 for row in group)
        profile_checks[profile] = {"software": variants(software), "configuration": configs,
                                  "configuration_constant": len(configs) == 1 if group else None,
                                  "one_recorded_binary": len(binaries["values"]) == 1 and binaries["unavailable_samples"] == 0 if group else None,
                                  "position_counts": {str(position): positions[position] for position in (1, 2, 3)},
                                  "position_counts_balanced": all(positions[position] == 5 for position in (1, 2, 3)) if complete else None,
                                  "per_task_positions": {task: dict(Counter(str((row["result"]["execution_order"] - 1) % 3 + 1) for row in group if row["result"]["task"] == task)) for task in TASKS}}
    sources = [row["manifest"]["software"].get("source") for row in rows]
    native = [row for row in rows if row["result"]["kind"] == "forge"]
    native_configs = [{key: value for key, value in row["manifest"]["configuration"].items() if key != "declared_optimization_env"} for row in native]
    native_hashes = [row["manifest"]["software"].get("executable_sha256") for row in native]
    fixtures = {task: variants([{key: row["manifest"]["state"].get(key) for key in ("initial_workspace_sha256", "fixture_prompt_sha256", "provided_test_sha256")}
                               for row in rows if row["result"]["task"] == task]) for task in TASKS}
    return {"profiles": profile_checks, "source_variants": variants(sources),
            "one_recorded_checkout_identity": len(variants(sources)) == 1 and all(value is not None for value in sources) if rows else None,
            "all_recorded_checkouts_clean": all(source and source.get("tracked_diff_sha256") == EMPTY_SHA256 and source.get("untracked_files_sha256") == EMPTY_SHA256 for source in sources) if rows else None,
            "same_native_recorded_binary": len(set(native_hashes)) == 1 and None not in native_hashes if native else None,
            "same_native_configuration_except_optimization_env": len(variants(native_configs)) == 1 if native else None,
            "native_env_differs_only_by_compact_flag": native_env_differs_only_by_compact_flag(grouped),
            "fixture_variants_by_task": fixtures,
            "fixture_identity_constant_by_task": {task: len(values) == 1 if values else None for task, values in fixtures.items()},
            "verifier_hashes": known_variants([row["manifest"].get("verifier_sha256") for row in rows]),
            "machine_variants": variants([row["manifest"].get("machine") for row in rows]),
            "observed_execution_orders": [row["result"]["execution_order"] for row in rows],
            "scope": "Checks consistency of recorded identities only; does not reread live binaries or prove checkout-to-build correspondence."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    rows, completeness = load_runs(args.evidence, args.allow_partial)
    output = {"schema_version": 1, "analysis_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "campaign": completeness, "identity_and_order": identity_and_order(rows, completeness["complete"]),
              "profiles": {profile: profile_summary([row for row in rows if row["result"]["profile"] == profile]) for profile in PROFILES},
              "paired_comparisons": [paired(rows, candidate, reference) for candidate, reference in (
                  (PROFILES[1], PROFILES[0]), (PROFILES[2], PROFILES[0]), (PROFILES[2], PROFILES[1]))],
              "interpretation": [
                  "All loaded attempts, including failures, enter all-attempt wall totals and success denominators.",
                  "Paired speed wins and percent changes use only matched task/repetition pairs where both runs succeeded; negative changes favor the candidate.",
                  "Usage sums include only complete reported samples for each field; sums with fewer than all trials are partial-sample sums, not campaign totals.",
                  "Forge ledger input is uncached plus cached reads plus cache creation; Codex input already includes cache. Cached and reasoning counts are subsets, never added twice.",
                  "Native usage covers the sampler actor only; protocol scope differs between products and excludes some auxiliary calls. No cost or billed-total inference.",
                  "First-request input is final provider input usage for attempt 1 of the first sampler request; unavailable first attempts are not replaced with retry usage.",
                  "Native TTFT/rates pool observed attempt-finish rows, including failures if measured; sample counts describe recorded coverage. External request timing is unavailable.",
                  "Request output tok/s includes initial wait; generation-window tok/s is a client approximation, not server decode speed. Phase intervals overlap and are not additive wall/CPU time.",
                  "Codex model and reasoning effort equality is declared configuration only; this adapter does not retain independent request telemetry confirming the effective values.",
                  "Recorded binary identities are separate from checkout identity; cache, request order, account load and host discovery remain potential confounders.",
                  "This is an exploratory five-task comparison; any verified failure prevents a claim of unchanged correctness."]}
    print(json.dumps(output, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
