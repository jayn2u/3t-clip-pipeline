import importlib.util
import hmac
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OVERLAY_ROOT = REPOSITORY_ROOT / "kubeflow/overlays/labclip"
PREPARE_SCRIPT = REPOSITORY_ROOT / "scripts/prepare_kubeflow_overlay.py"
EXPECTED_SOURCE_REF = "github.com/kubeflow/community-distribution/example?ref=f09f3eeaa25cc852665f460497a42b7fc68639ac"


def load_tools():
    if not PREPARE_SCRIPT.is_file():
        return None
    spec = importlib.util.spec_from_file_location("prepare_kubeflow_overlay", PREPARE_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Kubeflow overlay preparation module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class KubeflowRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tools = load_tools()
        cls.temp = tempfile.TemporaryDirectory(prefix="kubeflow-render-tests-")
        cls.temp_root = Path(cls.temp.name)
        cls.receipt = None
        cls.manifest_path = cls.temp_root / "rendered.yaml"
        cls.site_dir = cls.temp_root / "labclip-overlay"
        cls.identity_path = cls.temp_root / "identity.json"
        cls.material = None
        cls.documents = None
        if cls.tools is not None and OVERLAY_ROOT.is_dir():
            shutil.copytree(OVERLAY_ROOT, cls.site_dir, ignore=shutil.ignore_patterns("generated"))
            cls.material = cls.tools.prepare_identity(
                cls.identity_path,
                email="labclip@example.com",
                tailnet_hostname="labclip-kubeflow",
            )
            cls.tools.write_site_patches(cls.material, cls.site_dir)
            cls.receipt = cls.tools.render_distribution(
                EXPECTED_SOURCE_REF, cls.site_dir, cls.manifest_path
            )
            cls.documents = [
                document
                for document in yaml.safe_load_all(cls.manifest_path.read_text())
                if document
            ]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def require_implementation(self):
        self.assertIsNotNone(self.tools, "Kubeflow overlay preparation/rendering is not implemented")
        self.assertIsNotNone(self.receipt, "Kubeflow distribution render is not available")

    def load_documents(self):
        self.require_implementation()
        return self.documents

    def test_release_ref_is_pinned(self) -> None:
        self.require_implementation()
        self.assertEqual("26.03.1", self.tools.UPSTREAM_TAG)
        self.assertEqual("f09f3eeaa25cc852665f460497a42b7fc68639ac", self.tools.UPSTREAM_COMMIT)
        self.assertEqual(EXPECTED_SOURCE_REF, self.tools.UPSTREAM_SOURCE_REF)
        with self.assertRaises(ValueError):
            self.tools.validate_source_ref(
                "github.com/kubeflow/community-distribution/example?ref=master"
            )

    def test_private_identity_permissions(self) -> None:
        self.require_implementation()
        identity = json.loads(self.identity_path.read_text())
        self.assertEqual("labclip@example.com", identity["email"])
        self.assertEqual("kubeflow-user-labclip-example-com", self.material.profile_name)
        self.assertEqual("labclip-kubeflow", identity["tailnet_hostname"])
        self.assertEqual(0o600, stat.S_IMODE(self.identity_path.stat().st_mode))
        repeated = self.tools.prepare_identity(
            self.identity_path,
            email="labclip@example.com",
            tailnet_hostname="labclip-kubeflow",
        )
        self.assertEqual(self.material.password_hash, repeated.password_hash)
        self.assertEqual(self.material.password, repeated.password)
        for private_patch in self.tools.private_patch_paths(self.site_dir):
            with self.subTest(path=private_patch.name):
                self.assertEqual(0o600, stat.S_IMODE(private_patch.stat().st_mode))
        override = self.tools.prepare_identity(
            self.temp_root / "operator-identity.json",
            email="operator@example.net",
            tailnet_hostname="operator-kubeflow",
        )
        self.assertEqual("operator@example.net", override.email)
        self.assertEqual("kubeflow-user-operator-example-net", override.profile_name)
        reused_override = self.tools.prepare_identity(
            self.temp_root / "operator-identity.json"
        )
        self.assertTrue(hmac.compare_digest(override.password, reused_override.password))
        self.assertTrue(
            hmac.compare_digest(override.password_hash, reused_override.password_hash)
        )
        override_site = self.temp_root / "operator-overlay"
        shutil.copytree(OVERLAY_ROOT, override_site, ignore=shutil.ignore_patterns("generated"))
        override_patches = self.tools.write_site_patches(override, override_site)
        config_patch = yaml.safe_load(override_patches[0].read_text())
        dex_settings = yaml.safe_load(config_patch["data"]["config.yaml"])
        self.assertEqual("operator@example.net", dex_settings["staticPasswords"][0]["email"])
        profile_patch = yaml.safe_load(override_patches[2].read_text())
        self.assertEqual("kubeflow-user-operator-example-net", profile_patch[0]["value"])
        ingress_patch = yaml.safe_load(override_patches[3].read_text())
        self.assertEqual("operator-kubeflow", ingress_patch["spec"]["tls"][0]["hosts"][0])

    def test_default_dex_login_absent(self) -> None:
        documents = self.load_documents()
        rendered = self.manifest_path.read_text()
        self.assertNotIn("@".join(("user", "example.com")), rendered)
        self.assertEqual(0o600, stat.S_IMODE(self.manifest_path.stat().st_mode))
        password_secret = next(
            document
            for document in documents
            if document.get("kind") == "Secret"
            and document.get("metadata", {}).get("name") == "dex-passwords"
            and document.get("metadata", {}).get("namespace") == "auth"
        )
        self.assertTrue(
            hmac.compare_digest(
                self.material.password_hash,
                password_secret.get("stringData", {}).get("DEX_USER_PASSWORD", ""),
            )
        )
        self.assertTrue(self.material.password_hash.startswith("$2b$12$"))
        dex_config = next(
            document
            for document in documents
            if document.get("kind") == "ConfigMap"
            and document.get("metadata", {}).get("name") == "dex"
            and document.get("metadata", {}).get("namespace") == "auth"
        )
        dex_settings = yaml.safe_load(dex_config["data"]["config.yaml"])
        self.assertEqual("labclip@example.com", dex_settings["staticPasswords"][0]["email"])
        profile = next(document for document in documents if document.get("kind") == "Profile")
        self.assertEqual("labclip@example.com", profile["spec"]["owner"]["name"])
        self.assertEqual(
            "kubeflow-user-labclip-example-com", profile["metadata"]["name"]
        )

    def test_gateway_is_cluster_ip(self) -> None:
        documents = self.load_documents()
        gateway = [
            document
            for document in documents
            if document.get("kind") == "Service"
            and document.get("metadata", {}).get("name") == "istio-ingressgateway"
            and document.get("metadata", {}).get("namespace") == "istio-system"
        ]
        self.assertEqual(1, len(gateway))
        self.assertEqual("ClusterIP", gateway[0]["spec"]["type"])
        for service in (document for document in documents if document.get("kind") == "Service"):
            with self.subTest(service=service.get("metadata", {}).get("name")):
                service_type = service.get("spec", {}).get("type", "ClusterIP")
                self.assertNotIn(service_type, {"NodePort", "LoadBalancer"})
                self.assertFalse(
                    any("nodePort" in port for port in service.get("spec", {}).get("ports", []))
                )
        ingresses = [document for document in documents if document.get("kind") == "Ingress"]
        self.assertEqual(1, len(ingresses))
        ingress = ingresses[0]
        self.assertEqual("tailscale", ingress["spec"]["ingressClassName"])
        self.assertEqual("labclip-kubeflow", ingress["spec"]["tls"][0]["hosts"][0])
        self.assertNotEqual("true", ingress.get("metadata", {}).get("annotations", {}).get("tailscale.com/funnel"))

    def test_render_inventory_is_deterministic(self) -> None:
        self.require_implementation()
        first_inventory = self.receipt.inventory
        self.assertEqual(tuple(sorted(first_inventory)), first_inventory)
        self.assertEqual(len(first_inventory), len({tuple(row) for row in first_inventory}))
        self.assertEqual(first_inventory, self.tools._build_inventory(self.documents))


if __name__ == "__main__":
    unittest.main()
