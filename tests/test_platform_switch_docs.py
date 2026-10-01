import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PlatformSwitchDocumentationTests(unittest.TestCase):
    def test_root_readme_explains_how_to_leave_kubeflow(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("remove the Kustomize-applied Kubeflow distribution", readme)
        self.assertIn("then destroy the Terraform stack", readme)

    def test_kubeflow_runbook_states_the_switch_order(self) -> None:
        readme = (REPO_ROOT / "kubeflow" / "README.md").read_text(encoding="utf-8")
        self.assertIn("before applying the Argo stack", readme)

    def test_root_readme_names_both_guarded_objects(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`labclip-local-cache` StorageClass", readme)


if __name__ == "__main__":
    unittest.main()
