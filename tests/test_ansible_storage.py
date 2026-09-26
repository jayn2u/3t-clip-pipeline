from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_DIR = REPO_ROOT / "ansible"


class AnsibleStorageCapacityTests(unittest.TestCase):
    def run_storage_role(self, available_bytes: int, cache_bytes: int) -> subprocess.CompletedProcess[str]:
        ansible_playbook = shutil.which("ansible-playbook")
        self.assertIsNotNone(ansible_playbook, "ansible-playbook must be installed")

        with tempfile.TemporaryDirectory(prefix="labclip-storage-capacity-") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            cache_root = root / "cache"
            cache_root.mkdir(mode=0o777)
            inventory = root / "hosts.yml"
            inventory.write_text(
                f"""all:
  hosts:
    cache-test:
      ansible_connection: local
      cache_root: {cache_root}
      cache_capacity_gi: 2
""",
                encoding="utf-8",
            )
            playbook = root / "storage.yml"
            playbook.write_text(
                """- name: Check local storage capacity
  hosts: all
  gather_facts: false
  become: false
  roles:
    - local_storage
""",
                encoding="utf-8",
            )

            fake_tools = {
                "df": f"#!/bin/sh\nprintf 'Avail\\n{available_bytes}\\n'\n",
                "du": (
                    "#!/bin/sh\n"
                    "for last; do :; done\n"
                    f"printf '{cache_bytes}\\t%s\\n' \"$last\"\n"
                ),
            }
            for name, body in fake_tools.items():
                tool = fake_bin / name
                tool.write_text(body, encoding="utf-8")
                tool.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            return subprocess.run(
                [
                    ansible_playbook or "ansible-playbook",
                    str(playbook),
                    "--check",
                    "-i",
                    str(inventory),
                ],
                cwd=ANSIBLE_DIR,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )

    def test_existing_cache_usage_counts_toward_retained_capacity(self) -> None:
        result = self.run_storage_role(1_000_000_000, 1_500_000_000)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_capacity_that_exceeds_free_space_plus_existing_cache(self) -> None:
        result = self.run_storage_role(500_000_000, 500_000_000)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("filesystem has", result.stdout)


if __name__ == "__main__":
    unittest.main()
