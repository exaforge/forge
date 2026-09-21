"""Checks that never open the authentication file or invoke a model."""
import hashlib
import importlib.metadata
from pathlib import Path
import struct

HARBOR_VERSION = "0.23.0"
# Mirrors the stable SamplingErrorKind::as_str vocabulary; unknown strings stay absent.
SAMPLER_ERROR_KINDS = frozenset({"auth", "http", "api", "serialization", "idle_timeout",
                               "rate_limited", "empty_response", "max_tokens_truncation", "doom_loop_detected"})


def sampler_error_metadata(row):
    kind, status = row.get("error_kind"), row.get("status_code")
    return {"error_kind": kind if isinstance(kind, str) and kind in SAMPLER_ERROR_KINDS else None,
            "status_code": status if type(status) is int and 100 <= status <= 599 else None}


def require_harbor():
    if importlib.metadata.version("harbor") != HARBOR_VERSION:
        raise ValueError("This adapter requires Harbor 0.23.0")


def binary_identity(path, architecture="aarch64"):
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(64)
        if len(header) < 64 or header[:4] != b"\x7fELF" or header[4:6] != b"\x02\x01":
            raise ValueError("Forge must be a little-endian 64-bit Linux ELF binary; Darwin binaries cannot run here")
        machine = struct.unpack_from("<H", header, 18)[0]
        expected = {"aarch64": 183, "x86_64": 62}.get(architecture)
        if machine != expected or expected is None:
            raise ValueError("Forge ELF architecture does not match the selected container architecture")
        digest = hashlib.sha256(header)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "os": "linux", "architecture": architecture}


def auth_mount(path):
    # Only metadata is inspected. Docker opens the file later at container startup.
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("The runtime credential source must be an existing file")
    return {"type": "bind", "source": str(path), "target": "/root/.codex/auth.json",
            "read_only": True, "bind": {"create_host_path": False}}
