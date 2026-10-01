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


class KubeflowPlatformLayoutTests(unittest.TestCase):
    def test_platform_area_is_complete(self) -> None:
        for relative in (
            "platform/kustomization.yaml",
            "platform/patches/ingress-gateway-service.yaml",
            "platform/patches/trainer-controller-manager-node-selector.yaml",
            "platform/templates/dex-config-template.yaml",
            "platform/templates/kubeflow-tailnet-ingress.yaml",
            "platform/scripts/prepare.py",
            "platform/scripts/render.py",
            "platform/scripts/apply.py",
            "platform/scripts/check.py",
            "platform/tests/test_kubeflow_render.py",
            "platform/tests/test_kubeflow_apply.py",
            "platform/tests/test_kubeflow_access.py",
            "platform/tests/test_script_paths.py",
        ):
            with self.subTest(path=relative):
                self.assertTrue((KUBEFLOW / relative).is_file())

    def test_old_locations_are_gone(self) -> None:
        for old in ("scripts", "tests", "overlays"):
            with self.subTest(old=old):
                self.assertFalse((KUBEFLOW / old).exists())


if __name__ == "__main__":
    unittest.main()
