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

    def test_ownership_guard_names_this_stack(self) -> None:
        guard = (TERRAFORM_ROOT / "ownership_guard.tf").read_text(encoding="utf-8")
        self.assertRegex(guard, r'iac_stack_name\s*=\s*"argo"')
        self.assertIn('data "kubernetes_resources" "ownership_namespace"', guard)
        self.assertIn(
            'try(ns.metadata.labels["labclip.io/iac-stack"], local.iac_stack_name) != local.iac_stack_name',
            guard,
        )
        self.assertIn("precondition", guard)
        namespace = (TERRAFORM_ROOT / "namespace.tf").read_text(encoding="utf-8")
        self.assertIn('"labclip.io/iac-stack" = local.iac_stack_name', namespace)
        self.assertIn("terraform_data.ownership_guard", namespace)

    def test_terraform_readme_lists_the_ownership_guard(self) -> None:
        readme = (TERRAFORM_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("ownership_guard.tf", readme)

    def test_stack_readme_states_that_shared_files_are_duplicated(self) -> None:
        readme = (STACK_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("intentionally duplicated", readme)

    def test_ownership_guard_also_covers_the_cache_storage_class(self) -> None:
        guard = (TERRAFORM_ROOT / "ownership_guard.tf").read_text(encoding="utf-8")
        self.assertIn('data "kubernetes_resources" "ownership_storage_class"', guard)
        self.assertRegex(guard, r'cache_storage_class_name\s*=\s*"labclip-local-cache"')
        self.assertIn(
            'try(sc.metadata.labels["labclip.io/iac-stack"], local.iac_stack_name) != local.iac_stack_name',
            guard,
        )
        storage = (TERRAFORM_ROOT / "storage.tf").read_text(encoding="utf-8")
        self.assertIn("name = local.cache_storage_class_name", storage)
        self.assertIn('"labclip.io/iac-stack" = local.iac_stack_name', storage)
        self.assertIn("terraform_data.ownership_guard", storage)

    def test_terraform_readme_describes_the_storage_class_guard(self) -> None:
        readme = (TERRAFORM_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(readme, r"ownership_guard\.tf.*StorageClass")


if __name__ == "__main__":
    unittest.main()
