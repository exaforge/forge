#!/usr/bin/env python3
"""Build Linux Forge and write source provenance beside its binary, without auth."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from preflight import binary_identity

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROOT_FILES = ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml")
ROOT_DIRS = (".cargo", "crates", "prod", "third_party")


def source_identity(repo):
    paths = [repo / name for name in ROOT_FILES]
    for name in ROOT_DIRS:
        paths.extend((repo / name).rglob("*"))
    digest = hashlib.sha256()
    files = 0
    for path in sorted(paths):
        relative = path.relative_to(repo)
        if any(part in {".git", "target", "node_modules", "auth.json", ".env"}
               or part.startswith(".env.") for part in relative.parts):
            continue
        if path.is_symlink():
            kind, content = b"link", os.readlink(path).encode()
        elif path.is_file():
            kind, content = b"file", path.read_bytes()
        else:
            continue
        name = relative.as_posix().encode()
        digest.update(kind + len(name).to_bytes(8, "big") + name + len(content).to_bytes(8, "big") + content)
        files += 1
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    revision = result.stdout.strip() if result.returncode == 0 else None
    return {"revision": revision, "build_source_sha256": digest.hexdigest(), "source_files": files,
            "scope": "admitted Docker build source including uncommitted content; excludes git metadata and artifacts"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--platform", choices=("linux/arm64", "linux/amd64"), default="linux/arm64")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error("--jobs must be between 1 and 32")
    before = source_identity(REPO)
    recipe = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (HERE / "Dockerfile", HERE / "Dockerfile.dockerignore")}
    subprocess.run(["docker", "buildx", "build", "--platform", args.platform,
                    "--build-arg", "CARGO_BUILD_JOBS=" + str(args.jobs),
                    "-f", str(HERE / "Dockerfile"), "--output", "type=local,dest=" + str(args.output.resolve()),
                    str(REPO)], check=True)
    if source_identity(REPO) != before:
        raise RuntimeError("Build sources changed during compilation; no provenance manifest written")
    architecture = "aarch64" if args.platform == "linux/arm64" else "x86_64"
    identity = {"schema_version": 1, "source": before, "recipe_sha256": recipe,
                "binary": binary_identity(args.output / "forge", architecture),
                "rust": "1.94.0", "protoc": "29.3", "target_cpu": "generic"}
    (args.output / "forge.build.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
