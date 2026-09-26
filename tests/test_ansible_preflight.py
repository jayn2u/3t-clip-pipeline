from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_DIR = REPO_ROOT / "ansible"


class AnsiblePreflightTests(unittest.TestCase):
    def test_check_mode_runs_preflight_inspections_without_changes(self) -> None:
        ansible_playbook = shutil.which("ansible-playbook")
        self.assertIsNotNone(ansible_playbook, "ansible-playbook must be installed")

        with tempfile.TemporaryDirectory(prefix="labclip-ansible-preflight-") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            inventory = root / "hosts.yml"
            inventory.write_text(
                """all:
  children:
    k3s_cluster:
      hosts:
        preflight-test:
          ansible_connection: local
          node_gpu_model: Synthetic GPU
          storage_mount: /
          k3s_data_dir: /tmp/labclip-k3s
          cache_root: /tmp/labclip-cache
          minio_data_root: /tmp/labclip-minio
          minio_role: minio-test
""",
                encoding="utf-8",
            )

            fake_tools = {
                "modinfo": "#!/bin/sh\nprintf 'synthetic-module\\n'\n",
                "modprobe": "#!/bin/sh\nexit 0\n",
                "nvidia-smi": (
                    "#!/bin/sh\nprintf 'GPU 0: Synthetic GPU (UUID: GPU-test)\\n'\n"
                ),
                "k3s": "#!/bin/sh\nexit 1\n",
                "sysctl": "#!/bin/sh\nexit 0\n",
            }
            for name, body in fake_tools.items():
                tool = fake_bin / name
                tool.write_text(body, encoding="utf-8")
                tool.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            result = subprocess.run(
                [
                    ansible_playbook or "ansible-playbook",
                    "playbooks/preflight.yml",
                    "--check",
                    "-i",
                    str(inventory),
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

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed=0", result.stdout)
        self.assertIn("failed=0", result.stdout)


if __name__ == "__main__":
    unittest.main()
