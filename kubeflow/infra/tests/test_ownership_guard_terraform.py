import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


TERRAFORM_ROOT = Path(__file__).resolve().parents[1] / "terraform"
IGNORED = shutil.ignore_patterns(".terraform", "*.tfstate*", "terraform.tfvars", "*.auto.tfvars.json")


@unittest.skipUnless(shutil.which("terraform"), "terraform is not installed")
class OwnershipGuardTerraformTests(unittest.TestCase):
    def test_guard_behaviour_with_mocked_providers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory) / "terraform"
            shutil.copytree(TERRAFORM_ROOT, workdir, ignore=IGNORED)
            init = subprocess.run(
                ["terraform", f"-chdir={workdir}", "init", "-backend=false", "-input=false", "-no-color"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, init.returncode, init.stdout[-2000:] + init.stderr[-2000:])
            result = subprocess.run(
                ["terraform", f"-chdir={workdir}", "test", "-no-color"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout[-3000:] + result.stderr[-2000:])
            self.assertRegex(result.stdout, r"6 passed, 0 failed")


if __name__ == "__main__":
    unittest.main()
