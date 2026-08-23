from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[2]


class CollectionPin(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    version: str


class Requirements(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    collections: tuple[CollectionPin, ...]


class BootstrapValues(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    k3s_version: str
    k3s_binary_url: str
    k3s_binary_sha256: str
    k3s_install_script_url: str
    k3s_install_script_sha256: str
    kubeconfig: str
    kubeconfig_mode: str


@dataclass(frozen=True, slots=True)
class FakeBootstrap:
    command: tuple[str, ...]
    env: dict[str, str]
    artifact_dir: Path
    kubeconfig: Path


def _fake_bootstrap(tmp_path: Path, binary_sha256: str | None = None) -> FakeBootstrap:
    inventory = tmp_path / "inventory.yml"
    artifact_dir = tmp_path / "artifacts"
    kubeconfig = tmp_path / "kubeconfig"
    binary_content = "fake-k3s-linux-amd64"
    installer_content = "fake-pinned-installer"
    expected_binary_sha256 = binary_sha256 or hashlib.sha256(binary_content.encode()).hexdigest()
    _ = inventory.write_text(
        f"""---
all:
  hosts:
    localhost:
      ansible_connection: local
      ansible_python_interpreter: {sys.executable}
      ansible_remote_tmp: {tmp_path}/remote
  vars:
    ansible_connection: local
    ansible_python_interpreter: {sys.executable}
    airgap_dir: {artifact_dir}
    kubeconfig: {kubeconfig}
    kubeconfig_mode: '0600'
    k3s_version: v1.36.2+k3s1
    k3s_binary_url: https://github.com/k3s-io/k3s/releases/download/v1.36.2%2Bk3s1/k3s
    k3s_binary_sha256: 65a55ec56c24eab44383086166ec620a491952b7e23941a49ddca6e8a4c4b4de
    k3s_install_script_url: https://raw.githubusercontent.com/k3s-io/k3s/v1.36.2%2Bk3s1/install.sh
    k3s_install_script_sha256: 46177d4c99440b4c0311b67233823a8e8a2fc09693f6c89af1a7161e152fbfad
    k3s_secrets_encryption: true
    k3s_initial_bootstrap: true
    vault_k3s_token: fake-test-token
    k3s_adapter_fake_host_mode: true
    k3s_adapter_fake_binary_content: {binary_content!r}
    k3s_adapter_fake_installer_content: {installer_content!r}
    k3s_adapter_test_binary_sha256: {expected_binary_sha256}
    k3s_adapter_test_installer_sha256: {hashlib.sha256(installer_content.encode()).hexdigest()}
    host_preflight_test_facts:
      distribution: Ubuntu
      architecture: x86_64
      passwordless_become: true
      required_ports_open: true
      time_synchronized: true
      swap_disabled: true
      firewall_policy_valid: true
      nvidia_prerequisite_present: true
      storage_mount_ready: true
  children:
    k3s_cluster:
      children:
        server:
          hosts:
            server-1:
              platform_hostname: server-1
              platform_machine_id: machine-1
        agent:
          hosts:
            agent-1:
              platform_hostname: agent-1
              platform_machine_id: machine-2
"""
    )
    (ROOT / ".cache/ansible/home").mkdir(parents=True, exist_ok=True)
    return FakeBootstrap(
        command=(
            str(ROOT / ".venv/bin/ansible-playbook"),
            "-i",
            str(inventory),
            "ansible/playbooks/bootstrap.yml",
            "--check",
        ),
        env={
            **os.environ,
            "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
            "ANSIBLE_HOME": str(ROOT / ".cache/ansible/home"),
            "ANSIBLE_COLLECTIONS_PATH": str(ROOT / ".cache/ansible/collections"),
        },
        artifact_dir=artifact_dir,
        kubeconfig=kubeconfig,
    )


def test_collections_and_k3s_are_frozen() -> None:
    requirements = Requirements.model_validate(
        yaml.safe_load((ROOT / "ansible/requirements.yml").read_text())
    )
    sources = {item.name: item.version for item in requirements.collections}
    assert sources["https://github.com/k3s-io/k3s-ansible.git"] == (
        "2c3f3773c704bd00bf7f6fc340cac8ab7ce9121b"
    )
    assert sources["https://github.com/ansible-collections/kubernetes.core.git"] == (
        "0f472b53e2ee73e11b5f9067ab0826d76183c157"
    )
    values = BootstrapValues.model_validate(
        yaml.safe_load((ROOT / "ansible/group_vars/all.yml").read_text())
    )
    assert values.k3s_version == "v1.36.2+k3s1"
    assert values.k3s_binary_sha256 == (
        "65a55ec56c24eab44383086166ec620a491952b7e23941a49ddca6e8a4c4b4de"
    )
    assert values.k3s_binary_url.endswith("/v1.36.2%2Bk3s1/k3s")
    assert values.k3s_install_script_url.endswith("/v1.36.2%2Bk3s1/install.sh")
    assert values.k3s_install_script_sha256 == (
        "46177d4c99440b4c0311b67233823a8e8a2fc09693f6c89af1a7161e152fbfad"
    )


def test_single_server_agents_check_mode_idempotent(tmp_path: Path) -> None:
    fake = _fake_bootstrap(tmp_path)
    managed_paths = (Path("/usr/local/bin/k3s"), Path("/usr/local/bin/k3s-install.sh"))
    before = tuple(
        (path.exists(), path.stat().st_size, path.stat().st_mtime_ns)
        if path.exists()
        else (False, 0, 0)
        for path in managed_paths
    )
    first = subprocess.run(
        fake.command, cwd=ROOT, env=fake.env, check=False, capture_output=True, text=True
    )
    second = subprocess.run(
        fake.command, cwd=ROOT, env=fake.env, check=False, capture_output=True, text=True
    )
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert "changed=0" in second.stdout
    assert "failed=0" in second.stdout
    assert fake.kubeconfig.read_text() == "apiVersion: v1\nkind: Config"
    assert stat.S_IMODE(fake.kubeconfig.stat().st_mode) == 0o600
    assert {path.name for path in fake.artifact_dir.iterdir()} == {"k3s-amd64", "k3s-install.sh"}
    after = tuple(
        (path.exists(), path.stat().st_size, path.stat().st_mtime_ns)
        if path.exists()
        else (False, 0, 0)
        for path in managed_paths
    )
    assert after == before


def test_corrupt_fake_binary_fails_before_bootstrap(tmp_path: Path) -> None:
    fake = _fake_bootstrap(tmp_path, "0" * 64)
    result = subprocess.run(
        fake.command,
        cwd=ROOT,
        env=fake.env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "k3s_binary_checksum_mismatch" in result.stdout


@pytest.mark.parametrize(
    "context", ["", "production", "production-east", "Production-East", "prod-eu", "live"]
)
def test_make_defaults_to_check_and_rejects_production(tmp_path: Path, context: str) -> None:
    dry_run = subprocess.run(
        ["make", "-n", "cluster-bootstrap"], cwd=ROOT, check=False, capture_output=True, text=True
    )
    assert dry_run.returncode == 0
    assert "--check" in dry_run.stdout
    marker = tmp_path / "uv-invocation"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    _ = fake_uv.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {marker}\n")
    fake_uv.chmod(0o755)
    production = subprocess.run(
        ["make", "cluster-bootstrap", "APPLY=1", f"CONTEXT={context}"],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert production.returncode != 0
    assert "production_apply_forbidden" in production.stderr
    assert not marker.exists()


def test_make_allows_explicit_staging_context_without_live_execution(tmp_path: Path) -> None:
    marker = tmp_path / "uv-invocation"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    _ = fake_uv.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {marker}\n")
    fake_uv.chmod(0o755)
    result = subprocess.run(
        ["make", "cluster-bootstrap", "APPLY=1", "CONTEXT=staging-east"],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ansible-playbook" in marker.read_text()
    assert "ansible/playbooks/bootstrap.yml" in marker.read_text()
