import importlib.util
import sys
import unittest
from pathlib import Path


STACK_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = STACK_ROOT / "scripts"


def load(name: str):
    if str(SCRIPTS_ROOT) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_ROOT))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class KubeflowScriptPathTests(unittest.TestCase):
    def test_apply_defaults_point_inside_the_stack(self) -> None:
        apply = load("apply_kubeflow")
        self.assertEqual(STACK_ROOT / "generated", apply.GENERATED_ROOT)
        self.assertEqual(STACK_ROOT / "terraform", apply.TERRAFORM_ROOT)
        self.assertTrue(apply.TERRAFORM_ROOT.is_dir())

    def test_check_defaults_point_inside_the_stack(self) -> None:
        check = load("check_kubeflow")
        self.assertEqual(STACK_ROOT / "generated", check.GENERATED_ROOT)

    def test_overlay_root_points_at_the_moved_overlay(self) -> None:
        prepare = load("prepare_kubeflow_overlay")
        self.assertEqual(STACK_ROOT / "overlays" / "labclip", prepare.OVERLAY_ROOT)
        self.assertTrue((prepare.OVERLAY_ROOT / "kustomization.yaml").is_file())

    def test_inventory_has_no_platform_mode(self) -> None:
        apply = load("apply_kubeflow")
        self.assertNotIn("platform_mode", apply.TerraformOwnerInventory.__dataclass_fields__)
        self.assertNotIn("platform_mode", apply.TERRAFORM_CONSOLE_EXPRESSION)


if __name__ == "__main__":
    unittest.main()
