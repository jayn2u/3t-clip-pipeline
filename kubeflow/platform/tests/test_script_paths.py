import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PLATFORM_ROOT / "scripts"
INFRA_TERRAFORM = PLATFORM_ROOT.parent / "infra" / "terraform"


def load(name: str):
    if str(SCRIPTS_ROOT) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_ROOT))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PlatformScriptPathTests(unittest.TestCase):
    def test_apply_defaults_point_at_platform_and_infra(self) -> None:
        apply = load("apply")
        self.assertEqual(PLATFORM_ROOT / "generated", apply.GENERATED_ROOT)
        self.assertEqual(INFRA_TERRAFORM, apply.TERRAFORM_ROOT)
        self.assertTrue(apply.TERRAFORM_ROOT.is_dir())

    def test_check_defaults_point_at_platform(self) -> None:
        check = load("check")
        self.assertEqual(PLATFORM_ROOT / "generated", check.GENERATED_ROOT)

    def test_overlay_root_is_the_platform_directory(self) -> None:
        prepare = load("prepare")
        self.assertEqual(PLATFORM_ROOT, prepare.OVERLAY_ROOT)
        self.assertTrue((prepare.OVERLAY_ROOT / "kustomization.yaml").is_file())
        self.assertTrue((prepare.OVERLAY_ROOT / "templates" / "dex-config-template.yaml").is_file())
        self.assertTrue((prepare.OVERLAY_ROOT / "templates" / "kubeflow-tailnet-ingress.yaml").is_file())

    def test_inventory_has_no_platform_mode(self) -> None:
        apply = load("apply")
        self.assertNotIn("platform_mode", apply.TerraformOwnerInventory.__dataclass_fields__)
        self.assertNotIn("platform_mode", apply.TERRAFORM_CONSOLE_EXPRESSION)

    def test_site_patches_require_the_templates_directory(self) -> None:
        prepare = load("prepare")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                prepare.write_site_patches(None, Path(directory))

    def test_site_patches_ignore_templates_left_in_the_old_locations(self) -> None:
        prepare = load("prepare")
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site / "patches").mkdir()
            (site / "patches" / "dex-config-template.yaml").write_text("data: {}\n", encoding="utf-8")
            (site / "kubeflow-tailnet-ingress.yaml").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                prepare.write_site_patches(None, site)

    def test_infra_and_platform_script_directories_are_not_both_on_the_path(self) -> None:
        load("apply")
        infra_scripts = str(PLATFORM_ROOT.parent / "infra" / "scripts")
        self.assertNotIn(infra_scripts, sys.path)


if __name__ == "__main__":
    unittest.main()
