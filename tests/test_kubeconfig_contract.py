import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (REPO_ROOT / "ansible" / "generated" / "kubeconfig").resolve()


def terraform_default(stack: str) -> Path:
    source = (REPO_ROOT / stack / "terraform" / "variables.tf").read_text(encoding="utf-8")
    block = re.search(r'variable "kubeconfig_path" \{[^}]*\}', source).group(0)
    default = re.search(r'default\s*=\s*"([^"]+)"', block).group(1)
    return (REPO_ROOT / stack / "terraform" / default).resolve()


class KubeconfigContractTests(unittest.TestCase):
    def test_ansible_writes_the_shared_kubeconfig(self) -> None:
        group_vars = (REPO_ROOT / "ansible" / "group_vars" / "all.yml").read_text(encoding="utf-8")
        destination = re.search(r'k3s_kubeconfig_fetch_dest:\s*"([^"]+)"', group_vars).group(1)
        resolved = (REPO_ROOT / "ansible" / "playbooks" / destination.replace("{{ playbook_dir }}/", "")).resolve()
        self.assertEqual(EXPECTED, resolved)

    def test_each_stack_reads_the_shared_kubeconfig(self) -> None:
        for stack in ("argo", "kubeflow"):
            with self.subTest(stack=stack):
                self.assertEqual(EXPECTED, terraform_default(stack))

    def test_each_stack_example_inputs_use_the_shared_kubeconfig(self) -> None:
        for stack in ("argo", "kubeflow"):
            with self.subTest(stack=stack):
                example = (REPO_ROOT / stack / "terraform" / "terraform.tfvars.example").read_text(encoding="utf-8")
                value = re.search(r'kubeconfig_path\s*=\s*"([^"]+)"', example).group(1)
                self.assertEqual(EXPECTED, (REPO_ROOT / stack / "terraform" / value).resolve())

    def test_each_stack_ignores_its_own_private_files(self) -> None:
        ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for stack in ("argo", "kubeflow"):
            for pattern in (
                f"{stack}/terraform/.terraform/",
                f"{stack}/terraform/terraform.tfstate",
                f"{stack}/terraform/terraform.tfvars",
                f"{stack}/terraform/*.auto.tfvars.json",
            ):
                with self.subTest(pattern=pattern):
                    self.assertIn(pattern, ignore)
        self.assertIn("ansible/generated/", ignore)
        self.assertNotIn("terraform/generated/", ignore)


if __name__ == "__main__":
    unittest.main()
