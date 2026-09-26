from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_DIR = REPO_ROOT / "ansible"


class AnsibleTeardownTests(unittest.TestCase):
    def test_delegated_kubeconfig_cleanup_resolves_playbook_variables(self) -> None:
        ansible_playbook = shutil.which("ansible-playbook")
        self.assertIsNotNone(ansible_playbook, "ansible-playbook must be installed")
        kubeconfig = REPO_ROOT / "terraform/generated/kubeconfig"

        def signature(path: Path) -> tuple[int, int, int, int, int] | None:
            try:
                info = path.stat()
            except FileNotFoundError:
                return None
            return (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_size,
                info.st_mtime_ns,
            )

        before = signature(kubeconfig)

        with tempfile.TemporaryDirectory(prefix="labclip-ansible-teardown-") as tmp:
            root = Path(tmp)
            inventory = Path(tmp) / "hosts.yml"
            inventory.write_text(
                f"""all:
  children:
    k3s_server:
      hosts:
        test-control:
          ansible_connection: local
          k3s_node_ip: 127.0.0.1
          storage_mount: {root}
          k3s_data_dir: {root / 'k3s-data'}
          cache_root: {root / 'cache'}
          minio_role: minio-code
          minio_data_root: {root / 'minio'}
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    ansible_playbook or "ansible-playbook",
                    "playbooks/teardown.yml",
                    "--check",
                    "--start-at-task",
                    "Remove the fetched kubeconfig used by Terraform",
                    "--limit",
                    "test-control",
                    "-i",
                    str(inventory),
                ],
                cwd=ANSIBLE_DIR,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )

        after = signature(kubeconfig)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("failed=0", result.stdout)
        self.assertIn("test-control -> localhost", result.stdout)
        self.assertEqual(before, after, "check mode must not remove or rewrite kubeconfig")


if __name__ == "__main__":
    unittest.main()
