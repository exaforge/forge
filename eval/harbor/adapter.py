"""Harbor 0.23 installed agent; import as adapter:ForgeInstalledAgent."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.models.agent.context import AgentContext

from preflight import binary_identity, require_harbor

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
REMOTE = "/installed-agent/forge-eval"
sys.path.insert(0, str(EVAL))
import run as evaluation


def load_profile(profile_id, model):
    profiles = evaluation.load_profiles(EVAL / "profiles.example.json")
    matches = [p for p in profiles.values() if p["id"] == profile_id and p["kind"] == "forge"]
    if len(matches) != 1 or matches[0]["model"] != model:
        raise ValueError("Select an existing Forge profile with the same model as Harbor -m")
    return matches[0]


def make_spec(profile, binary, instruction_path, max_turns, timeout_seconds):
    values = {"prompt": instruction_path, "workspace": ".", "model": profile["model"],
              "effort": profile["effort"], "max_turns": str(max_turns)}
    argv = [part.format_map(values) for part in profile["argv"]]
    argv[0] = "/installed-agent/forge"
    argv[argv.index("-p")] = "--prompt-file"
    # Harbor owns the container boundary. Nested host sandboxing is not portable in Docker.
    argv[argv.index("--sandbox") + 1] = "off"
    identity = {"harbor_version": "0.23.0", "binary": binary, "profile": profile["id"],
                "model": profile["model"], "effort": profile["effort"], "max_turns": max_turns,
                "profile_sha256": hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest(),
                "sandbox": "harbor_docker; nested_forge_sandbox_off",
                "optimization_env": profile.get("env", {})}
    identity["adapter_sources_sha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                          for path in (HERE / "adapter.py", HERE / "container_runner.py",
                                                       EVAL / "run.py", EVAL / "fixtures.py", EVAL / "verify.py")}
    return {"argv": argv, "env": profile.get("env", {}), "model": profile["model"],
            "timeout_seconds": timeout_seconds, "identity": identity}


class ForgeInstalledAgent(BaseInstalledAgent):
    def __init__(self, *args, forge_binary=None, profile="forge-baseline", architecture="aarch64",
                 max_turns=30, timeout_seconds=110, **kwargs):
        require_harbor()
        super().__init__(*args, **kwargs)
        if not forge_binary:
            raise ValueError("forge_binary must name the Linux Forge artifact")
        self.forge_binary = Path(forge_binary).resolve(strict=True)
        self.binary = binary_identity(self.forge_binary, architecture)
        self.profile = load_profile(profile, self.model_name)
        self.max_turns, self.timeout_seconds = int(max_turns), int(timeout_seconds)
        if not 1 <= self.max_turns <= 1000 or not 1 <= self.timeout_seconds <= 86400:
            raise ValueError("Invalid agent budget")

    @staticmethod
    def name():
        return "forge-local"

    def get_version_command(self):
        return "/installed-agent/forge --version"

    async def install(self, environment):
        # Narrow first pilot: root, glibc Linux, Python 3.10+, architecture matched.
        probe = await self.exec_as_agent(environment, "uname -m; id -u; printf '%s\\n' \"$HOME\"")
        if (probe.stdout or "").splitlines() != [self.binary["architecture"], "0", "/root"]:
            raise RuntimeError("Forge pilot requires the declared Linux architecture and root /root home")
        await self.exec_as_root(environment, "(python3 -c 'import sys; assert sys.version_info >= (3, 10)' && test -f /etc/ssl/certs/ca-certificates.crt) >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq python3 ca-certificates)")
        await self.exec_as_root(environment, f"mkdir -p {REMOTE} /root/.codex /logs/agent/forge")
        await environment.upload_file(self.forge_binary, "/installed-agent/forge")
        for filename in ("run.py", "fixtures.py", "verify.py"):
            await environment.upload_file(EVAL / filename, f"{REMOTE}/{filename}")
        await environment.upload_file(HERE / "container_runner.py", f"{REMOTE}/container_runner.py")
        await self.exec_as_root(environment, "chmod 755 /installed-agent/forge && /installed-agent/forge --version >/dev/null && test -r /root/.codex/auth.json")

    @with_prompt_template
    async def run(self, instruction, environment, context: AgentContext):
        # Prompt never enters an exec command, argv, environment, or host evidence directory.
        with tempfile.TemporaryDirectory(prefix="forge-harbor-instruction-") as directory:
            prompt = Path(directory) / "instruction.txt"
            prompt.write_text(instruction)
            prompt.chmod(0o600)
            await environment.upload_file(prompt, f"{REMOTE}/instruction.txt")
            spec = make_spec(self.profile, self.binary, f"{REMOTE}/instruction.txt",
                             self.max_turns, self.timeout_seconds)
            spec_path = Path(directory) / "spec.json"
            spec_path.write_text(json.dumps(spec))
            await environment.upload_file(spec_path, f"{REMOTE}/spec.json")
        await self.exec_as_agent(environment,
                                 f"python3 {REMOTE}/container_runner.py {REMOTE}/spec.json",
                                 timeout_sec=self.timeout_seconds + 5)

    def populate_context_post_run(self, context: AgentContext):
        path = self.logs_dir / "forge" / "metadata.json"
        if not path.is_file() or path.is_symlink():
            context.metadata = {"forge": {"metadata_complete": False, "collection_error": "missing_metadata"}}
            return
        try:
            report = json.loads(path.read_text())
        except (OSError, ValueError):
            context.metadata = {"forge": {"metadata_complete": False, "collection_error": "invalid_metadata"}}
            return
        if not isinstance(report, dict):
            context.metadata = {"forge": {"metadata_complete": False, "collection_error": "invalid_metadata"}}
            return
        context.metadata = {"forge": report}
        if report.get("metadata_complete") is not True:
            # Harbor verifies only after this hook. Keep missing usage unknown and
            # let its verifier run; the export independently gates verified_success.
            return
        usage = report.get("harbor_usage")
        if not isinstance(usage, dict) or not all(type(usage.get(key)) is int and usage[key] >= 0
                                                 for key in ("input_tokens", "cache_tokens", "output_tokens")):
            context.metadata = {"forge": {"metadata_complete": False, "collection_error": "invalid_usage"}}
            return
        context.n_input_tokens = usage["input_tokens"]
        context.n_cache_tokens = usage["cache_tokens"]
        context.n_output_tokens = usage["output_tokens"]
        # OAuth subscription usage is not an observed USD bill. No invented price.
        context.cost_usd = None
