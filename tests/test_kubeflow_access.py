from pathlib import Path
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INGRESS_TEMPLATE = REPOSITORY_ROOT / "kubeflow/overlays/labclip/kubeflow-tailnet-ingress.yaml"
TERRAFORM_TAILSCALE = REPOSITORY_ROOT / "terraform/tailscale.tf"
KUBEFLOW_README = REPOSITORY_ROOT / "kubeflow/README.md"


class KubeflowAccessTests(unittest.TestCase):
    def test_tailnet_ingress_is_private(self) -> None:
        ingress = yaml.safe_load(INGRESS_TEMPLATE.read_text(encoding="utf-8"))
        spec = ingress["spec"]
        self.assertEqual("tailscale", spec["ingressClassName"])
        self.assertEqual(["__TAILNET_HOSTNAME__"], spec["tls"][0]["hosts"])
        self.assertEqual("__TAILNET_HOSTNAME__", spec["rules"][0]["host"])
        self.assertEqual("istio-ingressgateway", spec["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"])
        self.assertEqual(80, spec["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"]["number"])
        serialized = ingress["metadata"].get("annotations", {})
        self.assertNotEqual("true", serialized.get("tailscale.com/funnel"))
        self.assertNotIn("nodePort", str(spec))
        self.assertNotIn("LoadBalancer", str(spec))

    def test_tailscale_operator_requires_private_oauth_inputs(self) -> None:
        terraform = TERRAFORM_TAILSCALE.read_text(encoding="utf-8")
        self.assertIn('count = var.enable_tailscale ? 1 : 0', terraform)
        self.assertIn("precondition", terraform)
        self.assertIn("trimspace(var.tailscale_oauth_client_id)", terraform)
        self.assertIn("trimspace(var.tailscale_oauth_client_secret)", terraform)
        self.assertNotIn('resource "kubernetes_ingress', terraform)

    def test_local_access_binds_only_to_loopback(self) -> None:
        readme = KUBEFLOW_README.read_text(encoding="utf-8")
        self.assertIn("port-forward --address 127.0.0.1", readme)
        self.assertIn("http://127.0.0.1:8080/", readme)


if __name__ == "__main__":
    unittest.main()
