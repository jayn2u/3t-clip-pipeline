import os
import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_minio.sh"
REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_KUBECONFIG = REPO_ROOT / "ansible" / "generated" / "kubeconfig"


class BootstrapKubeconfigFallbackTests(unittest.TestCase):
    @unittest.skipIf(SHARED_KUBECONFIG.is_file(), "the shared kubeconfig exists on this machine")
    def test_default_kubeconfig_is_the_shared_ansible_file(self) -> None:
        environment = {key: value for key, value in os.environ.items() if key != "KUBECONFIG"}
        result = subprocess.run(
            ["bash", str(SCRIPT)], env=environment, capture_output=True, text=True, check=False
        )
        self.assertEqual(1, result.returncode)
        printed = result.stderr.strip().removeprefix("Kubeconfig is not readable: ")
        self.assertEqual(SHARED_KUBECONFIG, Path(printed).resolve())


if __name__ == "__main__":
    unittest.main()
