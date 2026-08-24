from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
REQUIREMENTS = ROOT / "ansible/requirements.yml"
REQUIREMENTS_SHA256 = "70d34763e23d33b90d322bcb89f4ebfef1889b7bef5411725b622631b7360bc6"
AUTHORIZATION = {
    "schema": "three-t-pipeline/remote-delivery-v1",
    "repository": "jayn2u/3t-clip-pipeline",
    "branch": "codex/next-generation-pipeline-platform",
    "base": "develop",
    "allowPush": True,
    "allowPullRequest": True,
}


def write_executable(path: Path, body: str) -> None:
    _ = path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def prepare_make_fixture(directory: Path) -> None:
    _ = shutil.copy2(MAKEFILE, directory / "Makefile")
    requirements = directory / "ansible/requirements.yml"
    requirements.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copy2(REQUIREMENTS, requirements)
    _ = (directory / ".gitignore").write_text(".cache/\n", encoding="utf-8")
    collections = directory / ".cache/ansible/collections"
    collection_names = (
        "k3s/orchestration",
        "kubernetes/core",
        "ansible/posix",
        "community/general",
    )
    for collection in collection_names:
        (collections / "ansible_collections" / collection).mkdir(parents=True, exist_ok=True)
    _ = (collections / ".requirements.sha256").write_text(
        f"{REQUIREMENTS_SHA256}\n", encoding="utf-8"
    )


def environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    network_log = tmp_path / "network.log"
    write_executable(bin_path / "uv", "exit 0")
    write_executable(bin_path / "gh", f"printf '%s\\n' \"$*\" >> '{network_log}'; exit 0")
    process_environment = os.environ.copy()
    process_environment["PATH"] = f"{bin_path}:{process_environment['PATH']}"
    return process_environment, network_log


def git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments], cwd=directory, check=True, capture_output=True, text=True
    )


def commit(directory: Path, message: str) -> str:
    _ = git(directory, "add", ".")
    _ = git(
        directory,
        "-c",
        "user.name=Fixture Owner",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        message,
    )
    return git(directory, "rev-parse", "HEAD").stdout.strip()


def authorization(directory: Path) -> Path:
    path = directory / "remote-delivery.json"
    _ = path.write_text(json.dumps(AUTHORIZATION), encoding="utf-8")
    _ = path.chmod(0o600)
    return path


def repository(tmp_path: Path, *, conflict: bool) -> tuple[Path, Path, str]:
    remote = tmp_path / "origin.git"
    _ = subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    base = tmp_path / "base"
    _ = git(tmp_path, "init", "-b", "develop", str(base))
    _ = (base / "shared.txt").write_text("base\n", encoding="utf-8")
    _ = commit(base, "base")
    _ = git(base, "remote", "add", "origin", str(remote))
    _ = git(base, "push", "-u", "origin", "develop")
    _ = git(remote, "symbolic-ref", "HEAD", "refs/heads/develop")

    task = tmp_path / "task"
    _ = task.mkdir()
    _ = git(task, "init")
    _ = git(task, "switch", "--orphan", "codex/next-generation-pipeline-platform")
    prepare_make_fixture(task)
    content = "task\n" if conflict else "base\n"
    _ = (task / "shared.txt").write_text(content, encoding="utf-8")
    before = commit(task, "task")
    _ = git(task, "remote", "add", "origin", str(remote))
    return task, remote, before


def run_release(
    directory: Path, process_environment: dict[str, str], authority: Path | None = None
) -> subprocess.CompletedProcess[str]:
    if not (directory / "Makefile").exists():
        prepare_make_fixture(directory)
    command = ["make", "release-handoff", "DELIVERY_MODE=authorized-pr"]
    if authority is not None:
        command.append(f"AUTHORIZATION_FILE={authority}")
    return subprocess.run(
        command,
        cwd=directory,
        env=process_environment,
        check=False,
        capture_output=True,
        text=True,
    )
