import asyncio
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import adapter
import container_runner
import launch
from preflight import auth_mount, binary_identity
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext


def elf(path, machine=183):
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", header, 18, machine)
    path.write_bytes(header)
    return path


def healthy_report():
    usage = {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 50,
             "cache_creation_input_tokens": 0, "reasoning_tokens": 10, "total_tokens": 120}
    base = {"process_id": 1, "request_id": "request-1", "attempt": 0, "elapsed_ms": 100}
    events = [base | {"event": name} for name in ("sampler_request_started", "sampler_attempt_started")]
    events += [base | {"event": "sampler_attempt_finished", "usage_is_final": True,
                       "provider_usage": usage, "duration_ms": 50, "model": "DO_NOT_PERSIST",
                       "throughput_unavailable_reason": "DO_NOT_PERSIST"},
               base | {"event": "sampler_request_finished"},
               {"event": "observation_health", "process_id": 1, "elapsed_ms": 101,
                "dropped_records": 0, "write_failures": 0, "flush_failures": 0}]
    stdout = json.dumps({"stopReason": "end_turn", "content": "DO_NOT_PERSIST",
                         "usage": {"input_tokens": 50, "cache_read_input_tokens": 50,
                                   "cache_creation_input_tokens": 0, "output_tokens": 20}})
    report = container_runner.build_report({"status": "completed", "exit_code": 0}, stdout,
                                          events, {"status": "observed", "discarded_records": 0},
                                          str(uuid.uuid4()), {})
    return report, events, stdout


class HarborTests(unittest.TestCase):
    def test_darwin_and_wrong_architecture_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forge"
            path.write_bytes(b"\xcf\xfa\xed\xfe" + bytes(60))
            with self.assertRaises(ValueError):
                binary_identity(path)
            elf(path, 62)
            with self.assertRaises(ValueError):
                binary_identity(path, "aarch64")
            self.assertEqual(binary_identity(path, "x86_64")["os"], "linux")

    def test_mount_is_single_readonly_file_without_reading_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials-fixture.json"
            path.write_text("DO_NOT_OPEN")
            with patch.object(Path, "open", side_effect=AssertionError("credential opened")):
                mount = auth_mount(path)
            self.assertTrue(mount["read_only"])
            self.assertEqual(mount["target"], "/root/.codex/auth.json")
            self.assertFalse(mount["bind"]["create_host_path"])
            with self.assertRaises(FileNotFoundError):
                auth_mount(Path(directory) / "missing")

    def test_profile_keeps_declared_flags_but_uses_container_and_prompt_file(self):
        profile = adapter.load_profile("forge-baseline", "gpt-5.6-sol")
        spec = adapter.make_spec(profile, {}, "/private-instruction", 7, 60)
        self.assertEqual(spec["argv"][spec["argv"].index("--sandbox") + 1], "off")
        self.assertIn("--prompt-file", spec["argv"])
        self.assertNotIn("-p", spec["argv"])
        self.assertIn("--no-memory", spec["argv"])
        self.assertIn("--no-subagents", spec["argv"])
        self.assertEqual(spec["env"], profile["env"])

    def test_complete_usage_counts_cached_input_once_and_drops_payload(self):
        report, _, _ = healthy_report()
        self.assertTrue(report["metadata_complete"])
        self.assertEqual(report["harbor_usage"]["input_tokens"], 100)
        self.assertEqual(report["harbor_usage"]["cache_tokens"], 50)
        self.assertIsNone(report["harbor_usage"]["cost_usd"])
        self.assertNotIn("DO_NOT_PERSIST", json.dumps(report))

    def test_sampler_failure_diagnostics_keep_only_http_number_and_fixed_kind(self):
        report, events, _ = healthy_report()
        attempt = events[2] | {"error_kind": "auth", "status_code": 401, "message": "DO_NOT_PERSIST"}
        report["requests"] = container_runner.safe_requests([attempt])
        public = launch.public_forge_report(report)
        self.assertEqual(public["requests"][0]["error_kind"], "auth")
        self.assertEqual(public["requests"][0]["status_code"], 401)
        self.assertNotIn("DO_NOT_PERSIST", json.dumps(public))
        for kind, status in (("DO_NOT_PERSIST", "401"), ({"DO_NOT_PERSIST": 1}, True), (None, -1), ("api", 401.0)):
            malformed = attempt | {"error_kind": kind, "status_code": status}
            sanitized = container_runner.safe_requests([malformed])[0]
            self.assertIsNone(sanitized["status_code"])
            if kind != "api":
                self.assertIsNone(sanitized["error_kind"])
            # The outer boundary must apply the same restrictions even if the inner log is modified.
            report["requests"] = [malformed]
            public = launch.public_forge_report(report)
            self.assertIsNone(public["requests"][0]["status_code"])
            self.assertNotIn("DO_NOT_PERSIST", json.dumps(public))

    def test_missing_footer_duplicate_or_incomplete_usage_fail_closed(self):
        _, events, stdout = healthy_report()
        variants = [events[:-1], events + [events[2]],
                    [event | {"usage_is_final": False} if event["event"] == "sampler_attempt_finished" else event for event in events],
                    [event | {"dropped_records": 1} if event["event"] == "observation_health" else event for event in events]]
        for variant in variants:
            report = container_runner.build_report({"status": "completed"}, stdout, variant,
                                                  {"status": "observed"}, str(uuid.uuid4()), {})
            self.assertFalse(report["metadata_complete"])
            self.assertIsNone(report["harbor_usage"]["input_tokens"])
        report = container_runner.build_report({"status": "completed"}, "{}", events,
                                              {"status": "observed"}, str(uuid.uuid4()), {})
        self.assertFalse(report["metadata_complete"])

    def test_verifier_process_protocol_and_collection_are_distinct(self):
        report, _, _ = healthy_report()
        raw = {"verifier_result": {"rewards": {"reward": 1}}, "exception_info": None}
        self.assertTrue(launch.export_trial(raw, report)["verified_success"])
        self.assertFalse(launch.export_trial(raw | {"verifier_result": {"rewards": {"reward": 0}}}, report)["verified_success"])
        self.assertFalse(launch.export_trial(raw | {"exception_info": {}}, report)["verified_success"])
        self.assertFalse(launch.export_trial(raw, None)["verified_success"])
        setup_failure = launch.export_trial(raw | {"exception_info": {"exception_type": "RuntimeError", "exception_message": "DO_NOT_PERSIST",
                                                                     "occurred_at": "2026-09-20T00:00:01+00:00"},
                                                  "agent_setup": {"started_at": "2026-09-20T00:00:00+00:00"}}, None)
        self.assertEqual(setup_failure["harbor_failure"], {"stage": "agent_setup", "category": "RuntimeError",
                                                          "last_started_phase": "agent_setup", "preceding_phase": None, "next_phase": None})
        self.assertNotIn("DO_NOT_PERSIST", json.dumps(setup_failure))
        for section in ("process", "protocol"):
            broken = copy.deepcopy(report)
            broken[section]["status"] = "failed"
            self.assertFalse(launch.export_trial(raw, broken)["verified_success"])

    def test_exception_recorded_after_agent_exit_is_not_assigned_to_later_verifier(self):
        def at(second):
            return f"2026-09-20T00:00:{second:02d}+00:00"
        raw = {"agent_execution": {"started_at": at(10), "finished_at": at(20)},
               "verifier": {"started_at": at(30), "finished_at": at(40)},
               "exception_info": {"exception_type": "NonZeroAgentExitCodeError", "occurred_at": at(21),
                                  "exception_message": "DO_NOT_PERSIST"},
               "verifier_result": {"rewards": {"reward": 1}}}
        result = launch.export_trial(raw, None)
        self.assertEqual(result["harbor_failure"], {"stage": "between_recorded_phases",
                         "category": "NonZeroAgentExitCodeError", "preceding_phase": "agent_execution",
                         "next_phase": "verifier", "last_started_phase": "verifier"})
        self.assertFalse(result["verified_success"])
        self.assertNotIn("DO_NOT_PERSIST", json.dumps(result))
        for occurred, expected in ((at(15), "agent_execution"), (at(35), "verifier"),
                                   (at(5), "initialization"), (at(45), "after_recorded_phases"),
                                   (None, "unknown"), ("DO_NOT_PERSIST", "unknown")):
            raw["exception_info"]["occurred_at"] = occurred
            self.assertEqual(launch.export_trial(raw, None)["harbor_failure"]["stage"], expected)

    def test_export_drops_unknown_fields_strings_and_mount_paths(self):
        report, _, _ = healthy_report()
        report["arbitrary"] = "DO_NOT_PERSIST"
        report["native"]["observed_models"] = ["DO_NOT_PERSIST"]
        report["native"]["usage"]["DO_NOT_PERSIST"] = 4
        report["process"]["duration_ms"] = "DO_NOT_PERSIST"
        raw = {"config": {"mounts": "DO_NOT_PERSIST"}, "exception_info": {"message": "DO_NOT_PERSIST"},
               "verifier_result": {"rewards": {"DO_NOT_PERSIST": 1, "reward": 1}}}
        self.assertNotIn("DO_NOT_PERSIST", json.dumps(launch.export_trial(raw, report)))

    def test_pinned_agent_api_and_context_population(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = adapter.ForgeInstalledAgent(logs_dir=root, model_name="gpt-5.6-sol",
                                                forge_binary=elf(root / "forge"))
            self.assertEqual(agent.name(), "forge-local")
            context = AgentContext()
            agent.populate_context_post_run(context)
            self.assertFalse(context.metadata["forge"]["metadata_complete"])
            self.assertIsNone(context.n_input_tokens)
            (root / "forge").unlink()
            (root / "forge").mkdir()
            report, _, _ = healthy_report()
            (root / "forge" / "metadata.json").write_text(json.dumps(report))
            agent.populate_context_post_run(context)
            self.assertEqual(context.n_input_tokens, 100)
            self.assertEqual(context.n_cache_tokens, 50)
            self.assertIsNone(context.cost_usd)
            report["metadata_complete"] = False
            (root / "forge" / "metadata.json").write_text(json.dumps(report))
            incomplete = AgentContext()
            agent.populate_context_post_run(incomplete)
            self.assertIsNone(incomplete.n_input_tokens)
            self.assertFalse(incomplete.metadata["forge"]["metadata_complete"])
            for malformed in ([], {"metadata_complete": True}, "invalid"):
                (root / "forge" / "metadata.json").write_text(json.dumps(malformed))
                context = AgentContext()
                agent.populate_context_post_run(context)
                self.assertIsNone(context.n_input_tokens)
                self.assertFalse(context.metadata["forge"]["metadata_complete"])

    def test_adapter_uploads_no_auth_and_exec_command_has_no_instruction(self):
        class Environment:
            def __init__(self):
                self.uploads, self.commands = [], []
            async def upload_file(self, source, destination):
                self.uploads.append((Path(source).name, destination))
            async def exec(self, command, **kwargs):
                self.commands.append(command)
                return ExecResult(return_code=0, stdout="aarch64\n0\n/root\n" if "uname -m" in command else "")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = adapter.ForgeInstalledAgent(logs_dir=root, model_name="gpt-5.6-sol",
                                                forge_binary=elf(root / "binary"))
            environment = Environment()
            asyncio.run(agent.install(environment))
            asyncio.run(agent.run("DO_NOT_PERSIST", environment, AgentContext()))
            self.assertNotIn("auth.json", [name for name, _ in environment.uploads])
            self.assertNotIn("DO_NOT_PERSIST", " ".join(environment.commands))


if __name__ == "__main__":
    unittest.main()
