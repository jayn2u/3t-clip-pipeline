import unittest
from pathlib import Path


KUBEFLOW = Path(__file__).resolve().parents[1] / "kubeflow"


class KubeflowInfraLayoutTests(unittest.TestCase):
    def test_terraform_root_lives_under_infra(self) -> None:
        self.assertFalse((KUBEFLOW / "terraform").exists())

    def test_infra_area_is_complete(self) -> None:
        for relative in (
            "infra/README.md",
            "infra/terraform/variables.tf",
            "infra/scripts/__init__.py",
            "infra/scripts/prepare_terraform_inputs.py",
            "infra/scripts/bootstrap_minio.sh",
            "infra/tests/test_terraform_inputs.py",
            "infra/tests/test_kubeflow_terraform.py",
            "infra/tests/test_stack_independence.py",
        ):
            with self.subTest(path=relative):
                self.assertTrue((KUBEFLOW / relative).is_file())


if __name__ == "__main__":
    unittest.main()
