import re
import unittest
from pathlib import Path


STACK_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = STACK_ROOT / "terraform"
CODE_SUFFIXES = {".tf", ".py", ".sh"}
OTHER_STACK_PATH = re.compile(r"(\.\./)+argo/|(^|[\s\"'=(])argo/(terraform|scripts|tests)")


def terraform_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TERRAFORM_ROOT.glob("*.tf"))
    }


class KubeflowStackIndependenceTests(unittest.TestCase):
    def test_root_exists_in_stack_directory(self) -> None:
        self.assertTrue((TERRAFORM_ROOT / "variables.tf").is_file())

    def test_no_platform_mode_switch(self) -> None:
        for name, source in terraform_sources().items():
            with self.subTest(file=name):
                self.assertNotIn("platform_mode", source)

    def test_no_standalone_argo_resources(self) -> None:
        names = set(terraform_sources())
        self.assertNotIn("argo_workflows.tf", names)
        self.assertNotIn("rbac.tf", names)
        combined = "\n".join(terraform_sources().values())
        self.assertNotIn('helm_release" "argo_workflows"', combined)
        self.assertNotIn('kubectl_manifest" "labclip_train"', combined)
        self.assertNotIn("labclip_workflow_template_path", combined)
        self.assertNotIn("argo_workflows_chart_version", combined)

    def test_code_does_not_reference_the_argo_stack_directory(self) -> None:
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
        self.assertRegex(guard, r'iac_stack_name\s*=\s*"kubeflow"')
        self.assertIn('data "kubernetes_resources" "ownership_namespace"', guard)
        self.assertIn(
            'try(ns.metadata.labels["labclip.io/iac-stack"], local.iac_stack_name) != local.iac_stack_name',
            guard,
        )
        self.assertIn("precondition", guard)
        namespace = (TERRAFORM_ROOT / "namespace.tf").read_text(encoding="utf-8")
        self.assertIn('"labclip.io/iac-stack" = local.iac_stack_name', namespace)
        self.assertIn("terraform_data.ownership_guard", namespace)

    def test_terraform_readme_describes_only_this_stack(self) -> None:
        readme = (TERRAFORM_ROOT / "README.md").read_text(encoding="utf-8")
        for forbidden in (
            "smoke_labclip_runtime",
            "smoke_cleanup_markers",
            "argo_workflows.tf",
            "rbac.tf",
            "platform_mode",
            "kubectl -n argo get workflow",
            "fullnameOverride",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, readme)
        self.assertIn("ownership_guard.tf", readme)

    def test_stack_readme_states_that_shared_files_are_duplicated(self) -> None:
        readme = (STACK_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("intentionally duplicated", readme)


if __name__ == "__main__":
    unittest.main()
