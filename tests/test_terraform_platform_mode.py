import re
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = REPOSITORY_ROOT / "terraform"


def block(source: str, pattern: str) -> str:
    match = re.search(pattern, source)
    if match is None:
        raise AssertionError(f"Missing HCL block matching {pattern!r}")
    opening = source.find("{", match.start())
    depth = 0
    in_string = False
    escaped = False
    for position in range(opening, len(source)):
        character = source[position]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : position + 1]
    raise AssertionError(f"Unclosed HCL block matching {pattern!r}")


def read_terraform(name: str) -> str:
    return (TERRAFORM_ROOT / name).read_text(encoding="utf-8")


def resource(name: str, kind: str, file_name: str) -> str:
    return block(
        read_terraform(file_name),
        rf'resource\s+"{re.escape(kind)}"\s+"{re.escape(name)}"\s*\{{',
    )


class TerraformPlatformModeTests(unittest.TestCase):
    def test_default_mode_keeps_argo(self) -> None:
        platform_mode = block(
            read_terraform("variables.tf"), r'variable\s+"platform_mode"\s*\{'
        )
        self.assertRegex(platform_mode, r'default\s*=\s*"argo"')
        self.assertRegex(
            resource("argo_workflows", "helm_release", "argo_workflows.tf"),
            r'count\s*=\s*var\.platform_mode\s*==\s*"argo"\s*\?\s*1\s*:\s*0',
        )
        self.assertRegex(
            resource("labclip_train", "kubectl_manifest", "argo_workflows.tf"),
            r'count\s*=\s*var\.platform_mode\s*==\s*"argo"\s*\?\s*1\s*:\s*0',
        )
        runner = resource("labclip_runner", "kubernetes_service_account", "rbac.tf")
        self.assertRegex(
            runner,
            r'count\s*=\s*var\.platform_mode\s*==\s*"argo"\s*\?\s*1\s*:\s*0',
        )
        for name, kind, file_name in (
            ("argo_workflows", "helm_release", "argo_workflows.tf"),
            ("labclip_train", "kubectl_manifest", "argo_workflows.tf"),
            ("labclip_runner", "kubernetes_service_account", "rbac.tf"),
            ("labclip_runner", "kubernetes_cluster_role", "rbac.tf"),
            ("labclip_runner", "kubernetes_cluster_role_binding", "rbac.tf"),
            (
                "argo_workflowtaskresults_cleanup",
                "kubernetes_role",
                "rbac.tf",
            ),
            (
                "argo_workflowtaskresults_cleanup",
                "kubernetes_role_binding",
                "rbac.tf",
            ),
        ):
            with self.subTest(state_migration=f"{kind}.{name}"):
                source = read_terraform(file_name)
                self.assertRegex(
                    source,
                    rf'from\s*=\s*{re.escape(kind)}\.{re.escape(name)}\s+to\s*=\s*{re.escape(kind)}\.{re.escape(name)}\[0\]',
                )

    def test_kubeflow_mode_has_no_standalone_argo(self) -> None:
        for name, kind, file_name in (
            ("argo_workflows", "helm_release", "argo_workflows.tf"),
            ("labclip_train", "kubectl_manifest", "argo_workflows.tf"),
            ("labclip_runner", "kubernetes_service_account", "rbac.tf"),
            ("labclip_runner", "kubernetes_cluster_role", "rbac.tf"),
            ("labclip_runner", "kubernetes_cluster_role_binding", "rbac.tf"),
            (
                "argo_workflowtaskresults_cleanup",
                "kubernetes_role",
                "rbac.tf",
            ),
            (
                "argo_workflowtaskresults_cleanup",
                "kubernetes_role_binding",
                "rbac.tf",
            ),
        ):
            with self.subTest(resource=f"{kind}.{name}"):
                self.assertRegex(
                    resource(name, kind, file_name),
                    r'count\s*=\s*var\.platform_mode\s*==\s*"argo"\s*\?\s*1\s*:\s*0',
                )

    def test_run_cache_claim_has_one_namespace(self) -> None:
        claims = re.findall(
            r'resource\s+"kubernetes_persistent_volume_claim"\s+"cache"\s*\{',
            "\n".join(path.read_text(encoding="utf-8") for path in TERRAFORM_ROOT.glob("*.tf")),
        )
        self.assertEqual(1, len(claims))
        claim = resource("cache", "kubernetes_persistent_volume_claim", "storage.tf")
        self.assertRegex(claim, r'for_each\s*=\s*local\.cache_claims')
        self.assertRegex(claim, r'namespace\s*=\s*local\.cache_claim_namespace')

        storage_locals = block(read_terraform("storage.tf"), r'locals\s*\{')
        self.assertRegex(
            storage_locals,
            r'cache_claims\s*=\s*var\.platform_mode\s*==\s*"argo"\s*\?\s*var\.nodes\s*:[\s\S]*?local\.kubeflow_run_bindings_enabled\s*\?\s*var\.nodes\s*:\s*\{\}',
        )
        self.assertRegex(
            storage_locals,
            r'cache_claim_namespace\s*=\s*var\.platform_mode\s*==\s*"argo"\s*\?\s*\(?\s*kubernetes_namespace\.argo\.metadata\[0\]\.name\s*\)?\s*:\s*var\.labclip_run_namespace',
        )

        kubeflow_integration = read_terraform("kubeflow_integration.tf")
        self.assertRegex(
            kubeflow_integration,
            r'kubeflow_run_bindings_enabled\s*=\s*var\.platform_mode\s*==\s*"kubeflow"\s*&&\s*var\.enable_kubeflow_run_bindings',
        )
        for name in (
            "kubeflow_minio_credentials",
            "kubeflow_ghcr",
            "kubeflow_wandb",
        ):
            with self.subTest(secret=name):
                secret = resource(name, "kubernetes_secret", "kubeflow_integration.tf")
                self.assertRegex(
                    secret,
                    r'(?:for_each\s*=\s*local\.kubeflow_run_bindings_enabled\s*\?\s*[\s\S]*?:\s*\{\}|count\s*=\s*local\.kubeflow_run_bindings_enabled\s*\?\s*1\s*:\s*0)',
                )
                self.assertRegex(secret, r'namespace\s*=\s*var\.labclip_run_namespace')

        ghcr_secret = resource("kubeflow_ghcr", "kubernetes_secret", "kubeflow_integration.tf")
        self.assertRegex(ghcr_secret, r'kubernetes_secret\.ghcr\["ghcr-secret"\]')
        minio_secret = resource(
            "kubeflow_minio_credentials", "kubernetes_secret", "kubeflow_integration.tf"
        )
        self.assertRegex(minio_secret, r'kubernetes_secret\.minio_credentials\[each\.key\]')
        wandb_secret = resource("kubeflow_wandb", "kubernetes_secret", "kubeflow_integration.tf")
        self.assertRegex(wandb_secret, r'kubernetes_secret\.wandb\.metadata\[0\]\.name')

        outputs = read_terraform("outputs.tf")
        self.assertRegex(outputs, r'output\s+"platform_mode"')
        self.assertRegex(outputs, r'output\s+"labclip_run_namespace"')


if __name__ == "__main__":
    unittest.main()
