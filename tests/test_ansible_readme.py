import unittest
from pathlib import Path


ANSIBLE = Path(__file__).resolve().parents[1] / "ansible"


class AnsibleReadmeTests(unittest.TestCase):
    def test_readme_points_at_existing_terraform_roots(self) -> None:
        readme = (ANSIBLE / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("../terraform", readme)
        for root in ("../argo/terraform", "../kubeflow/infra/terraform"):
            with self.subTest(root=root):
                self.assertIn(root, readme)
                self.assertTrue((ANSIBLE / root).resolve().is_dir())


if __name__ == "__main__":
    unittest.main()
