#!/usr/bin/env python3
"""Offline deterministic harness; never contacts an inference provider."""
import json
import os
from pathlib import Path
import sys
import time

from solutions import solve

if "--version" in sys.argv:
    print("fake-harness 1.0")
    raise SystemExit(0)

mode = sys.argv[1]
workspace = Path.cwd()
if mode == "timeout":
    time.sleep(30)
if mode in {"solve", "exit-error", "change-tests", "delete-tests", "add-tests"}:
    solve(workspace)
if mode == "change-tests":
    (workspace / "test_public.py").write_text("# Removed the original assertions.\n")
if mode == "delete-tests":
    (workspace / "test_public.py").unlink()
if mode == "add-tests":
    (workspace / "test_added.py").write_text("# New tests may be added.\n")
if mode == "tamper":
    verifier = workspace.parent / "verification" / "verify.py"
    verifier.chmod(0o644)
    verifier.write_text("print('{}')\n")
if mode != "missing-observations":
    common = {"schema_version": 1, "run_id": os.environ["FORGE_RUN_ID"], "process_id": os.getpid(),
              "request_id": "request-1", "timestamp_unix_ms": 0}
    rows = [
        {"event": "sampler_request_started", "elapsed_ms": 0},
        {"event": "sampler_attempt_started", "attempt": 1, "elapsed_ms": 1},
        {"event": "sampler_attempt_finished", "attempt": 1, "elapsed_ms": 5, "usage_is_final": True,
         "first_text_ms": 1, "first_generation_event_ms": 1, "end_to_end_output_tokens_per_second": 500,
         "observed_output_tokens_per_second": None, "throughput_unavailable_reason": "reasoning_tokens_unreported",
         "duration_ms": 4, "provider_usage": {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 3,
                                               "total_tokens": 12, "reasoning_tokens": None},
         "raw_tool_output": "DO_NOT_PERSIST", "prompt": "DO_NOT_PERSIST"},
        {"event": "sampler_request_finished", "elapsed_ms": 6, "attempts_started": 1, "retries_started": 0},
        {"event": "observation_health", "elapsed_ms": 7, "dropped_records": 0, "write_failures": 0,
         "flush_failures": 0, "records_written": 4},
    ]
    Path(os.environ["FORGE_OBSERVATION_FILE"]).write_text("".join(json.dumps(common | row) + "\n" for row in rows))
print(json.dumps({"text": "DO_NOT_PERSIST", "thought": "DO_NOT_PERSIST", "stopReason": "end_turn",
                  "usage": {"input_tokens": 7, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 0,
                            "output_tokens": 2, "total_tokens": 12}}))
print("DO_NOT_PERSIST", file=sys.stderr)
raise SystemExit(1 if mode == "exit-error" else 0)
