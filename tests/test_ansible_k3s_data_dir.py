from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_DIR = REPO_ROOT / "ansible"


class K3sDataDirGuardTests(unittest.TestCase):
    def test_missing_teardown_confirmation_stops_before_uninstall(self) -> None:
        ansible_playbook = shutil.which("ansible-playbook")
        self.assertIsNotNone(ansible_playbook, "ansible-playbook must be installed")

        with tempfile.TemporaryDirectory(prefix="labclip-k3s-confirmation-") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            k3s = fake_bin / "k3s"
            k3s.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            k3s.chmod(0o755)
            inventory = root / "hosts.yml"
            inventory.write_text(
                f"""all:
  children:
    k3s_agent:
      hosts:
        test-agent:
          ansible_connection: local
          storage_mount: {root}
          k3s_data_dir: {root / 'k3s-data'}
          cache_root: {root / 'cache'}
          minio_role: minio-ml-assets
          minio_data_root: {root / 'minio'}
    k3s_cluster:
      children:
        k3s_agent:
""",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            for confirmation_args in ([], ["-e", "confirm_teardown=no"]):
                with self.subTest(confirmation=confirmation_args or "omitted"):
                    result = subprocess.run(
                        [
                            ansible_playbook or "ansible-playbook",
                            "playbooks/teardown.yml",
                            "--check",
                            "-i",
                            str(inventory),
                            *confirmation_args,
                            "-e",
                            "ansible_become=false",
                        ],
                        cwd=ANSIBLE_DIR,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=60,
                    )
                    output = result.stdout + result.stderr
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertIn("confirm_teardown was passed", output)
                    self.assertNotIn("Check for the k3s agent uninstall script", output)

    def test_teardown_rejects_cache_overlap_before_uninstall(self) -> None:
        ansible_playbook = shutil.which("ansible-playbook")
        self.assertIsNotNone(ansible_playbook, "ansible-playbook must be installed")

        with tempfile.TemporaryDirectory(prefix="labclip-k3s-data-guard-") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            k3s = fake_bin / "k3s"
            k3s.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            k3s.chmod(0o755)
            cache_root = root / "cache"
            minio_root = root / "minio"
            inventory = root / "hosts.yml"
            inventory.write_text(
                f"""all:
  children:
    k3s_agent:
      hosts:
        test-agent:
          ansible_connection: local
          k3s_node_ip: 127.0.0.1
          storage_mount: {root}
          cache_root: {cache_root}
          cache_capacity_gi: 2
          minio_role: minio-ml-assets
          minio_data_root: {minio_root}
          k3s_data_dir: {cache_root / 'k3s-data'}
    k3s_cluster:
      children:
        k3s_agent:
""",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            result = subprocess.run(
                [
                    ansible_playbook or "ansible-playbook",
                    "playbooks/teardown.yml",
                    "--check",
                    "--limit",
                    "test-agent",
                    "-i",
                    str(inventory),
                    "-e",
                    "confirm_teardown=yes",
                    "-e",
                    "ansible_become=false",
                ],
                cwd=ANSIBLE_DIR,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("must not overlap", output)
        self.assertNotIn("Run k3s agent uninstall script", output)


if __name__ == "__main__":
    unittest.main()
