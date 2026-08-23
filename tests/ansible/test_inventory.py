from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE_ENV = {
    **os.environ,
    "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
    "ANSIBLE_HOME": str(ROOT / ".cache/ansible/home"),
    "ANSIBLE_COLLECTIONS_PATH": str(ROOT / ".cache/ansible/collections"),
}


def _inventory(server_count: int, *, override: str = "") -> str:
    servers = "\n".join(
        "\n".join(
            (
                f"            server-{index}:",
                f"              platform_hostname: server-{index}",
                f"              platform_machine_id: machine-{index}",
            )
        )
        for index in range(server_count)
    )
    return f"""---
all:
  children:
    k3s_cluster:
      children:
        server:
          hosts:
{servers or "            {}"}
        agent:
          hosts: {{}}
      vars:
        ansible_connection: local
        ansible_python_interpreter: {sys.executable}
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
{override}
"""


def _run_preflight(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    inventory = tmp_path / "inventory.yml"
    _ = inventory.write_text(content)
    (ROOT / ".cache/ansible/home").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            str(ROOT / ".venv/bin/ansible-playbook"),
            "-i",
            str(inventory),
            "ansible/playbooks/preflight.yml",
            "--check",
        ],
        cwd=ROOT,
        env=ANSIBLE_ENV,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("server_count", [1, 3])
def test_valid_server_topologies_pass(tmp_path: Path, server_count: int) -> None:
    result = _run_preflight(tmp_path, _inventory(server_count))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("server_count", [0, 2, 4])
def test_two_server_and_other_invalid_topologies_fail(tmp_path: Path, server_count: int) -> None:
    result = _run_preflight(tmp_path, _inventory(server_count))
    assert result.returncode != 0
    assert "ha_requires_three_servers" in result.stdout
    assert "changed=0" in result.stdout


def test_unsupported_os_fails_without_mutation(tmp_path: Path) -> None:
    content = _inventory(1).replace("distribution: Ubuntu", "distribution: Debian")
    result = _run_preflight(tmp_path, content)
    assert result.returncode != 0
    assert "ubuntu_required" in result.stdout
    assert "changed=0" in result.stdout


def test_missing_nvidia_fails_without_mutation(tmp_path: Path) -> None:
    content = _inventory(1).replace(
        "nvidia_prerequisite_present: true", "nvidia_prerequisite_present: false"
    )
    result = _run_preflight(tmp_path, content)
    assert result.returncode != 0
    assert "nvidia_prerequisite_missing" in result.stdout
    assert "changed=0" in result.stdout


def test_passwordless_become_false_fails_without_mutation(tmp_path: Path) -> None:
    content = _inventory(1).replace("passwordless_become: true", "passwordless_become: false")
    result = _run_preflight(tmp_path, content)
    assert result.returncode != 0
    assert "passwordless_become_required" in result.stdout
    assert "changed=0" in result.stdout


@pytest.mark.parametrize(
    ("valid_fact", "invalid_fact", "error_code"),
    [
        ("architecture: x86_64", "architecture: aarch64", "linux_amd64_required"),
        ("required_ports_open: true", "required_ports_open: false", "required_ports_unavailable"),
        ("time_synchronized: true", "time_synchronized: false", "time_sync_required"),
        ("swap_disabled: true", "swap_disabled: false", "swap_must_be_disabled"),
        ("firewall_policy_valid: true", "firewall_policy_valid: false", "firewall_policy_invalid"),
        ("storage_mount_ready: true", "storage_mount_ready: false", "storage_mount_required"),
    ],
)
def test_unsupported_host_prerequisite_fails_without_mutation(
    tmp_path: Path, valid_fact: str, invalid_fact: str, error_code: str
) -> None:
    content = _inventory(1).replace(valid_fact, invalid_fact)
    result = _run_preflight(tmp_path, content)
    assert result.returncode != 0
    assert error_code in result.stdout
    assert "changed=0" in result.stdout


def test_duplicate_machine_identity_fails(tmp_path: Path) -> None:
    content = _inventory(3).replace("machine-2", "machine-1")
    result = _run_preflight(tmp_path, content)
    assert result.returncode != 0
    assert "duplicate_host_identity" in result.stdout


def test_duplicate_hostname_fails(tmp_path: Path) -> None:
    content = _inventory(3).replace("platform_hostname: server-2", "platform_hostname: server-1")
    result = _run_preflight(tmp_path, content)
    assert result.returncode != 0
    assert "duplicate_host_identity" in result.stdout


def test_example_inventory_has_one_server_and_agents() -> None:
    (ROOT / ".cache/ansible/home").mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(ROOT / ".venv/bin/ansible-inventory"),
            "-i",
            "ansible/inventory/example.yml",
            "--graph",
        ],
        cwd=ROOT,
        env=ANSIBLE_ENV,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("|--@server:") == 1
    assert "|  |--k3s-server-1" in result.stdout
    assert "|  |--k3s-agent-1" in result.stdout
