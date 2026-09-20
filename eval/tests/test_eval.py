import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run
from fixtures import TASKS, materialize
from solutions import solve


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-eval-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def profile(self, mode="solve"):
        return {"id": "fake-" + mode, "kind": "fake", "model": "fake-model", "effort": "medium",
                "argv": [sys.executable, str(EVAL / "tests/fake_harness.py"), mode]}

    def attempt(self, mode="solve", task="bug-fix"):
        destination = self.root / (mode + "-" + task)
        destination.mkdir()
        limits = {"max_turns": 20, "timeout_seconds": 3, "verifier_timeout_seconds": 10}
        return run.run_attempt(self.profile(mode), task, 1, 1, destination, limits, {"version": "fake"}), destination

    def test_every_fixture_fails_initially_and_passes_real_solution(self):
        for task in TASKS:
            with self.subTest(task=task):
                workspace = self.root / task
                workspace.mkdir()
                materialize(task, workspace)
                original = run.tree_digest(workspace)
                verifier = EVAL / "verify.py"
                initial = run.verify(task, workspace, verifier, run.file_digest(verifier), 10)
                self.assertEqual(initial["status"], "failed")
                self.assertEqual(run.tree_digest(workspace), original)
                solve(workspace)
                result = run.verify(task, workspace, verifier, run.file_digest(verifier), 10)
                self.assertEqual(result["status"], "passed", result)

    def test_metadata_only_evidence_and_usage_no_double_count(self):
        result, destination = self.attempt()
        self.assertTrue(result["successful_verified_run"])
        self.assertEqual(result["native"]["usage"]["input_tokens"], 10)
        self.assertEqual(result["native"]["usage"]["output_tokens"], 2)
        self.assertEqual(result["native"]["usage_coverage"], "complete_for_observed_sampler_attempts")
        self.assertIsNone(result["native"]["usage"]["reasoning_tokens"])
        self.assertEqual(result["native"]["median_end_to_end_output_tokens_per_second"], 500)
        self.assertEqual(result["native"]["end_to_end_output_rate_samples"], 1)
        self.assertIsNone(result["native"]["median_observed_output_tokens_per_second"])
        self.assertEqual(result["native"]["throughput_unavailable_reasons"], {"reasoning_tokens_unreported": 1})
        self.assertEqual(result["native"]["text_ttft_samples"], 1)
        for file in destination.rglob("*"):
            if file.is_file():
                self.assertNotIn("DO_NOT_PERSIST", file.read_text())
        self.assertEqual(sorted(path.name for path in next(destination.iterdir()).iterdir()),
                         ["events.jsonl", "manifest.json", "result.json"])
        manifest = json.loads(next(destination.iterdir()).joinpath("manifest.json").read_text())
        self.assertIsNone(manifest["configuration"]["forge_config_sha256"])
        self.assertEqual(manifest["state"]["forge_home"], "not_applicable")

    def test_process_completion_is_not_verified_success(self):
        result, _ = self.attempt("fail")
        self.assertEqual(result["execution"]["status"], "completed")
        self.assertEqual(result["protocol"]["status"], "completed")
        self.assertFalse(result["verification"]["passed"])
        self.assertFalse(result["successful_verified_run"])
        result, _ = self.attempt("exit-error")
        self.assertTrue(result["verification"]["passed"])
        self.assertFalse(result["successful_verified_run"])

    def test_phase_metadata_is_retained_without_unknown_payloads(self):
        path = self.root / "observations.jsonl"
        event = {"schema_version": 1, "run_id": "phase-run", "event": "phase_end",
                 "phase": "tool_dispatch", "status": "error", "tool_call_id": "call-123",
                 "loop_index": 2, "turn_number": 3, "tool_count": 4,
                 "context_fast_path": True, "includes_lock_wait_and_auth_retry": False,
                 "arguments": {"source": "DO_NOT_PERSIST"}, "raw_tool_output": "DO_NOT_PERSIST",
                 "unknown_boolean": True}
        path.write_text(json.dumps(event) + "\n")
        events, collection = run.read_observations(path, "phase-run")
        self.assertEqual(collection["status"], "observed")
        for key in ("phase", "status", "tool_call_id", "loop_index", "turn_number", "tool_count",
                    "context_fast_path", "includes_lock_wait_and_auth_retry"):
            self.assertEqual(events[0][key], event[key])
        self.assertNotIn("DO_NOT_PERSIST", json.dumps(events))
        self.assertNotIn("unknown_boolean", events[0])

    def test_verifier_tampering_is_detected(self):
        result, _ = self.attempt("tamper")
        self.assertEqual(result["verification"]["status"], "integrity_error")
        self.assertIsNone(result["verification"]["passed"])

    def test_provided_tests_cannot_be_modified_or_deleted_but_new_tests_allowed(self):
        for mode in ("change-tests", "delete-tests", "add-tests"):
            with self.subTest(mode=mode):
                result, destination = self.attempt(mode)
                self.assertTrue(result["verification"]["passed"])
                self.assertEqual(result["successful_verified_run"], mode == "add-tests")
                self.assertEqual(result["constraints"]["provided_tests_unchanged"], mode == "add-tests")
                self.assertEqual(result["constraints"]["changed_or_missing_provided_tests"],
                                 [] if mode == "add-tests" else ["test_public.py"])
                manifest = json.loads(next(destination.iterdir()).joinpath("manifest.json").read_text())
                self.assertEqual(set(manifest["state"]["provided_test_sha256"]), {"test_public.py"})
                self.assertFalse(manifest["privacy"]["authentication_store_read_or_copied_by_runner"])

    def test_verifier_rejects_retried_or_replaced_nonretryable_exception(self):
        for mode in ("repeat", "replace"):
            workspace = self.root / mode
            workspace.mkdir()
            materialize("test-diagnosis", workspace)
            solve(workspace)
            path = workspace / "retrying.py"
            bad_handling = ("            try:\n                call()\n            except TypeError:\n                pass\n            raise\n"
                            if mode == "repeat" else "            raise TypeError('replacement')\n")
            path.write_text(path.read_text() + "\n_original_retry = retry\n"
                            "def retry(call, attempts, retry_on=(ValueError,), wait=lambda: None):\n"
                            "    def wrapped():\n"
                            "        try:\n            return call()\n"
                            "        except TypeError:\n" + bad_handling +
                            "    return _original_retry(wrapped, attempts, retry_on=retry_on, wait=wait)\n")
            verifier = EVAL / "verify.py"
            result = run.verify("test-diagnosis", workspace, verifier, run.file_digest(verifier), 10)
            self.assertEqual(result["status"], "failed", mode)

    def test_missing_observations_stay_unavailable(self):
        result, _ = self.attempt("missing-observations")
        self.assertEqual(result["collection"]["status"], "unavailable")
        self.assertIsNone(result["native"]["request_count"])
        self.assertIsNone(result["native"]["usage"]["input_tokens"])

    def test_incomplete_loss_duplicate_or_partial_usage_never_complete(self):
        result, destination = self.attempt()
        events = [json.loads(line) for line in next(destination.iterdir()).joinpath("events.jsonl").read_text().splitlines()]
        variants = [events[:-1], events[1:], events + [events[2]],
                    [event | {"usage_is_final": False} if event["event"] == "sampler_attempt_finished" else event for event in events],
                    [event | {"dropped_records": 1} if event["event"] == "observation_health" else event for event in events]]
        for variant in variants:
            metrics = run.native_metrics(variant, {"status": "observed"}, True)
            self.assertNotEqual(metrics["usage_coverage"], "complete_for_observed_sampler_attempts")
            self.assertIsNone(metrics["usage"]["input_tokens"])

    def test_counterbalanced_schedule(self):
        a, b = {"id": "a"}, {"id": "b"}
        order = [(p["id"], task, rep) for p, task, rep in run.schedule([a, b], ["one", "two"], 2)]
        self.assertEqual(order, [("a", "one", 1), ("b", "one", 1), ("b", "two", 1), ("a", "two", 1),
                                 ("b", "one", 2), ("a", "one", 2), ("a", "two", 2), ("b", "two", 2)])

    def test_profiles_are_argv_only_and_no_secret_env(self):
        self.assertEqual(len(run.load_profiles(EVAL / "profiles.example.json")), 7)
        document = {"profiles": [self.profile()]}
        document["profiles"][0]["env"] = {"API_TOKEN": "do-not-retain"}
        path = self.root / "bad.json"
        path.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            run.load_profiles(path)
        self.assertEqual(run.expand(["{prompt}"], {"prompt": "$(touch never); literal"}), ["$(touch never); literal"])

    def test_tool_allowlist_validation_and_ablation_profiles(self):
        profiles = run.load_profiles(EVAL / "profiles.example.json")
        baseline, optimized = profiles["forge-baseline"], profiles["forge-optimized"]
        local = profiles["forge-local-tools-only"]
        self.assertEqual(run.declared_tool_scope(baseline)["mode"], "default_agent_tools")
        scoped = run.declared_tool_scope(optimized)
        self.assertEqual(scoped, run.declared_tool_scope(local))
        self.assertIn("run_terminal_cmd", scoped["requested_tool_ids"])
        self.assertEqual(scoped["disallowed_tool_ids"], ["search_tool", "use_tool"])
        self.assertEqual(optimized["env"]["FORGE_PROMPT_CACHE"], "0")
        self.assertEqual(local["env"], baseline["env"])
        for profile in (optimized, local):
            self.assertEqual(profile["model"], baseline["model"])
            self.assertEqual(profile["effort"], baseline["effort"])
            self.assertEqual(profile["argv"][:len(baseline["argv"])], baseline["argv"])
        for suffix in (["--tools"], ["--tools", ""], ["--tools", "read_file,read_file"],
                       ["--tools", "read_file;unsafe"], ["--disallowed-tools", "{prompt}"]):
            document = {"profiles": [baseline | {"argv": baseline["argv"] + suffix}]}
            path = self.root / "bad-tools.json"
            path.write_text(json.dumps(document))
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                run.load_profiles(path)

    def test_tool_scope_and_machine_are_retained_in_manifest(self):
        profile = self.profile() | {"kind": "forge"}
        profile["argv"] += ["--tools", "read_file,run_terminal_cmd", "--disallowed-tools", "search_tool,use_tool"]
        destination = self.root / "tool-manifest"
        destination.mkdir()
        limits = {"max_turns": 20, "timeout_seconds": 3, "verifier_timeout_seconds": 10}
        calls = []
        execute = run.execute
        def capture(argv, cwd, env, timeout):
            if "FORGE_RUN_ID" in env:
                calls.append(argv)
            return execute(argv, cwd, env, timeout)
        with patch.object(run, "execute", capture):
            result = run.run_attempt(profile, "bug-fix", 1, 1, destination, limits, {"version": "fake"})
        self.assertTrue(result["successful_verified_run"])
        self.assertEqual(calls, [profile["argv"]])
        manifest = json.loads(next(destination.iterdir()).joinpath("manifest.json").read_text())
        self.assertEqual(manifest["configuration"]["tool_scope"], run.declared_tool_scope(profile))
        self.assertEqual(manifest["configuration"]["forge_config_sha256"], run.digest(run.forge_config(profile["model"]).encode()))
        self.assertEqual(manifest["state"]["forge_home"], "fresh_temporary")
        self.assertEqual(set(manifest["machine"]), {"system", "release", "architecture", "python_version"})
        self.assertTrue(all(manifest["machine"].values()))
        self.assertNotIn("use_concise", run.forge_config(profile["model"]))

    def test_three_profiles_are_position_balanced_over_three_repeats(self):
        profiles = [{"id": name} for name in ("a", "b", "c")]
        rows = list(run.schedule(profiles, ["one", "two"], 3))
        for task in ("one", "two"):
            for position in range(3):
                at_position = [[profile["id"] for profile, row_task, row_rep in rows
                                if row_task == task and row_rep == repetition][position]
                               for repetition in range(1, 4)]
                self.assertEqual(sorted(at_position), ["a", "b", "c"])

    def test_timeout_kills_descendant_processes(self):
        started_file, survived_file = self.root / "started", self.root / "survived"
        child = "import sys,time;open(sys.argv[1],'w').write('ready');time.sleep(1.2);open(sys.argv[2],'w').write('alive');time.sleep(30)"
        code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]);time.sleep(30)"
        state, _ = run.execute([sys.executable, "-c", code, child, str(started_file), str(survived_file)], self.root, os.environ.copy(), 0.5)
        self.assertEqual(state["status"], "timeout")
        self.assertLess(state["duration_ms"], 3000)
        self.assertTrue(started_file.exists())
        time.sleep(1)
        self.assertFalse(survived_file.exists(), "descendant survived group cleanup")

    def test_invalid_protocol_is_not_success(self):
        metadata = run.protocol_metadata("forge", "not-json", True)
        self.assertEqual(metadata["status"], "unobserved")
        self.assertIsNone(metadata["usage"]["input_tokens"])

    def test_output_drain_not_in_process_duration(self):
        finish = run.Capture.finish
        def slow_finish(capture):
            time.sleep(0.1)
            return finish(capture)
        with patch.object(run.Capture, "finish", slow_finish):
            state, _ = run.execute([sys.executable, "-c", "print('ok')"], self.root, os.environ.copy(), 3)
        self.assertGreaterEqual(state["output_drain_ms"], 200)
        self.assertLess(state["duration_ms"], state["output_drain_ms"])

    def test_summary_keeps_failures_and_nulls_and_evidence_links(self):
        good, _ = self.attempt()
        bad, _ = self.attempt("fail")
        bad["profile"] = good["profile"]
        summary = run.summarize([good, bad])[0]
        self.assertEqual(summary["attempts"], 2)
        self.assertEqual(summary["successful_verified_runs"], 1)
        self.assertEqual(summary["success_rate"], 0.5)
        self.assertEqual(summary["median_reported_input_including_cache"], 10)
        run.write_summary(self.root, [good, bad])
        self.assertIn("/result.json", (self.root / "RESULTS.md").read_text())

    def test_ambient_optimization_knobs_are_cleared(self):
        actual = run.execute
        seen = []
        def spy(argv, cwd, env, timeout):
            if "FORGE_RUN_ID" in env:
                seen.append(env.copy())
            return actual(argv, cwd, env, timeout)
        with patch.dict(os.environ, {"FORGE_PROMPT_CACHE": "1"}), patch.object(run, "execute", spy):
            self.attempt()
        self.assertNotIn("FORGE_PROMPT_CACHE", seen[0])


if __name__ == "__main__":
    unittest.main()
