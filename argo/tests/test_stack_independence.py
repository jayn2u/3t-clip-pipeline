import re
import unittest
from pathlib import Path


STACK_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = STACK_ROOT / "terraform"
CODE_SUFFIXES = {".tf", ".py", ".sh"}
OTHER_STACK_PATH = re.compile(r"(\.\./)+kubeflow/|(^|[\s\"'=(])kubeflow/(terraform|scripts|tests|overlays)")
KUBEFLOW_NAMES = (
    "platform_mode",
    "enable_kubeflow_run_bindings",
    "confirm_kubeflow_cache_pv_rebind",
    "labclip_run_namespace",
    "kubeflow_run_bindings_guard",
    "kubeflow_cache",
)


def terraform_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TERRAFORM_ROOT.glob("*.tf"))
    }


class ArgoStackIndependenceTests(unittest.TestCase):
    def test_root_exists_in_stack_directory(self) -> None:
        self.assertTrue((TERRAFORM_ROOT / "argo_workflows.tf").is_file())

    def test_no_kubeflow_files_or_names(self) -> None:
        self.assertNotIn("kubeflow_integration.tf", set(terraform_sources()))
        for name, source in terraform_sources().items():
            for forbidden in KUBEFLOW_NAMES:
                with self.subTest(file=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_argo_resources_are_unconditional(self) -> None:
        source = terraform_sources()["argo_workflows.tf"]
        self.assertIn('resource "helm_release" "argo_workflows"', source)
        self.assertIn('resource "kubectl_manifest" "labclip_train"', source)
        self.assertIsNone(re.search(r"^\s*count\s*=", source, re.M))

    def test_code_does_not_reference_the_kubeflow_stack_directory(self) -> None:
        this_file = Path(__file__).resolve()
        for path in sorted(STACK_ROOT.rglob("*")):
            parts = set(path.parts)
            if not path.is_file() or path.suffix not in CODE_SUFFIXES:
                continue
            if parts & {"generated", ".terraform", "__pycache__"} or path.resolve() == this_file:
                continue
            with self.subTest(file=str(path.relative_to(STACK_ROOT))):
                self.assertIsNone(OTHER_STACK_PATH.search(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
