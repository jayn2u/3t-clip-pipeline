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


def variable(name: str) -> str:
    return block(
        read_terraform("variables.tf"),
        rf'variable\s+"{re.escape(name)}"\s*\{{',
    )


class KubeflowTerraformTests(unittest.TestCase):
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
            r'cache_claims\s*=\s*local\.kubeflow_run_bindings_enabled\s*\?\s*var\.nodes\s*:\s*\{\}',
        )
        self.assertRegex(
            storage_locals,
            r'cache_claim_namespace\s*=\s*var\.labclip_run_namespace',
        )

        kubeflow_integration = read_terraform("kubeflow_integration.tf")
        self.assertRegex(
            kubeflow_integration,
            r'kubeflow_run_bindings_enabled\s*=\s*var\.enable_kubeflow_run_bindings',
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
        self.assertNotRegex(outputs, r'output\s+"platform_mode"')
        self.assertRegex(outputs, r'output\s+"labclip_run_namespace"')

    def test_kubeflow_run_bindings_require_a_distinct_namespace(self) -> None:
        self.assertRegex(variable("labclip_run_namespace"), r'default\s*=\s*"argo"')
        guard = resource(
            "kubeflow_run_bindings_guard", "terraform_data", "kubeflow_integration.tf"
        )
        self.assertRegex(
            guard,
            r'count\s*=\s*local\.kubeflow_run_bindings_enabled\s*\?\s*1\s*:\s*0',
        )
        self.assertRegex(
            guard,
            r'condition\s*=\s*var\.labclip_run_namespace\s*!=\s*var\.argo_namespace',
        )
        self.assertRegex(guard, r'error_message\s*=.*(?:distinct|differ)')

    def test_kubeflow_claim_creation_requires_explicit_pv_rebind_confirmation(self) -> None:
        confirmation = variable("confirm_kubeflow_cache_pv_rebind")
        self.assertRegex(confirmation, r'type\s*=\s*bool')
        self.assertRegex(confirmation, r'default\s*=\s*false')
        guard = resource(
            "kubeflow_run_bindings_guard", "terraform_data", "kubeflow_integration.tf"
        )
        self.assertRegex(
            guard,
            r'condition\s*=\s*var\.confirm_kubeflow_cache_pv_rebind',
        )
        self.assertRegex(guard, r'error_message\s*=.*claimRef')
        integration = read_terraform("kubeflow_integration.tf")
        pv_data_source = block(
            integration,
            r'data\s+"kubernetes_resource"\s+"kubeflow_cache"\s*\{',
        )
        self.assertRegex(
            pv_data_source,
            r'for_each\s*=\s*local\.kubeflow_run_bindings_enabled\s*\?\s*var\.nodes\s*:\s*\{\}',
        )
        self.assertRegex(pv_data_source, r'name\s*=\s*each\.value\.cache_claim')
        self.assertIn("data.kubernetes_resource.kubeflow_cache", guard)
        pv_preflight = block(
            guard,
            r'precondition\s*\{\s*condition\s*=\s*alltrue',
        )
        self.assertIn('["status"]["phase"]', pv_preflight)
        self.assertIn('["spec"]["claimRef"]["uid"]', pv_preflight)
        self.assertIn('["spec"]["local"]["path"]', guard)
        self.assertIn('["spec"]["nodeAffinity"]', guard)
        self.assertIn('== "Available"', pv_preflight)
        self.assertIn('== "Bound"', pv_preflight)
        self.assertNotIn('== "Released"', pv_preflight)
        self.assertIn("labclip_run_namespace", guard)
        self.assertIn("cache_claim", guard)
        claims_data_source = block(
            integration,
            r'data\s+"kubernetes_resources"\s+"kubeflow_cache_claims"\s*\{',
        )
        self.assertRegex(claims_data_source, r'kind\s*=\s*"PersistentVolumeClaim"')
        self.assertRegex(claims_data_source, r'namespace\s*=\s*var\.labclip_run_namespace')
        self.assertIn("data.kubernetes_resources.kubeflow_cache_claims[0].objects", pv_preflight)
        self.assertIn(
            'try(claim["metadata"]["uid"], "") == try(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]["uid"], "")',
            pv_preflight,
        )
        self.assertIn(
            'try(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]["namespace"], "") == var.labclip_run_namespace',
            pv_preflight,
        )
        self.assertIn(
            'try(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["claimRef"]["name"], "") == node.cache_claim',
            pv_preflight,
        )
        self.assertIn('try(claim["spec"]["volumeName"], "") == node.cache_claim', pv_preflight)
        self.assertRegex(
            resource("cache", "kubernetes_persistent_volume_claim", "storage.tf"),
            r'depends_on\s*=\s*\[terraform_data\.kubeflow_run_bindings_guard\]',
        )

    def test_kubeflow_cache_affinity_requires_one_exact_node_match(self) -> None:
        guard = resource(
            "kubeflow_run_bindings_guard", "terraform_data", "kubeflow_integration.tf"
        )
        affinity_marker = '["spec"]["nodeAffinity"]'
        affinity_offset = guard.index(affinity_marker)
        affinity_start = guard.rfind("precondition {", 0, affinity_offset)
        affinity_preflight = block(guard[affinity_start:], r'precondition\s*\{')
        self.assertIn('length(keys(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"])) == 1', affinity_preflight)
        self.assertIn('["nodeAffinity"]["required"])) == 1', affinity_preflight)
        self.assertIn('["nodeSelectorTerms"]) == 1', affinity_preflight)
        self.assertIn('["matchExpressions"]) == 1', affinity_preflight)
        self.assertIn('["matchExpressions"][0])) == 3', affinity_preflight)
        self.assertIn('["values"]) == 1', affinity_preflight)
        self.assertIn('== "kubernetes.io/hostname"', affinity_preflight)
        self.assertIn('== "In"', affinity_preflight)
        self.assertIn('[0] == node_name', affinity_preflight)

    def test_provider_null_claimref_is_unclaimed_and_non_null_fields_are_rejected(self) -> None:
        guard = resource(
            "kubeflow_run_bindings_guard", "terraform_data", "kubeflow_integration.tf"
        )
        pv_preflight = block(
            guard,
            r'precondition\s*\{\s*condition\s*=\s*alltrue',
        )
        self.assertRegex(
            pv_preflight,
            r'length\(\[\s*for claim_ref_value in values\(data\.kubernetes_resource\.kubeflow_cache\[node_name\]\.object\["spec"\]\["claimRef"\]\)\s*:\s*claim_ref_value\s*if\s*claim_ref_value\s*!=\s*null\s*\]\)\s*==\s*0',
        )

    def test_provider_null_match_fields_are_allowed_but_non_null_fields_are_rejected(self) -> None:
        guard = resource(
            "kubeflow_run_bindings_guard", "terraform_data", "kubeflow_integration.tf"
        )
        affinity_marker = '["spec"]["nodeAffinity"]'
        affinity_offset = guard.index(affinity_marker)
        affinity_start = guard.rfind("precondition {", 0, affinity_offset)
        affinity_preflight = block(guard[affinity_start:], r'precondition\s*\{')
        self.assertIn(
            'contains(keys(data.kubernetes_resource.kubeflow_cache[node_name].object["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]), "matchExpressions")',
            affinity_preflight,
        )
        self.assertIn(
            'contains(["matchExpressions", "matchFields"], term_key)',
            affinity_preflight,
        )
        self.assertRegex(
            affinity_preflight,
            r'try\(length\(data\.kubernetes_resource\.kubeflow_cache\[node_name\]\.object\["spec"\]\["nodeAffinity"\]\["required"\]\["nodeSelectorTerms"\]\[0\]\["matchFields"\]\),\s*0\)\s*==\s*0',
        )

    def test_kubeflow_gpu_runtime_policy_is_stage_two_gated(self) -> None:
        policy = resource(
            "kubeflow_gpu_runtime_policy", "kubectl_manifest", "kubeflow_integration.tf"
        )
        binding = resource(
            "kubeflow_gpu_runtime_binding", "kubectl_manifest", "kubeflow_integration.tf"
        )
        for resource_block in (policy, binding):
            self.assertRegex(
                resource_block,
                r'count\s*=\s*local\.kubeflow_run_bindings_enabled\s*\?\s*1\s*:\s*0',
            )
        self.assertRegex(
            policy,
            r'depends_on\s*=\s*\[terraform_data\.kubeflow_run_bindings_guard\]',
        )
        self.assertIn("kubeflow_gpu_runtime_policy_name", read_terraform("kubeflow_integration.tf"))
        self.assertIn("kubectl_manifest.kubeflow_gpu_runtime_policy", binding)

    def test_kubeflow_gpu_runtime_policy_matches_only_target_gpu_pod_creates(self) -> None:
        integration = read_terraform("kubeflow_integration.tf")
        policy = resource(
            "kubeflow_gpu_runtime_policy", "kubectl_manifest", "kubeflow_integration.tf"
        )
        self.assertRegex(integration, r'apiGroups\s*=\s*\[""\]')
        self.assertRegex(integration, r'apiVersions\s*=\s*\["v1"\]')
        self.assertRegex(integration, r'operations\s*=\s*\["CREATE"\]')
        self.assertRegex(integration, r'resources\s*=\s*\["pods"\]')
        self.assertIn('request.namespace == ${jsonencode(var.labclip_run_namespace)}', integration)
        self.assertRegex(policy, r'yaml_body\s*=\s*local\.kubeflow_gpu_runtime_policy_yaml')

    def test_kubeflow_gpu_runtime_policy_preserves_explicit_runtime_classes(self) -> None:
        integration = read_terraform("kubeflow_integration.tf")
        self.assertIn('"nvidia.com/gpu" in container.resources.requests', integration)
        self.assertIn("object.spec.initContainers.exists", integration)
        self.assertIn("!has(object.spec.runtimeClassName)", integration)
        self.assertIn('runtimeClassName: "nvidia"', integration)

    def test_runbook_defines_profile_namespace_before_using_it(self) -> None:
        readme = (TERRAFORM_ROOT / "README.md").read_text(encoding="utf-8")
        declaration = readme.index("LABCLIP_RUN_NAMESPACE=")
        first_use = readme.index('kubectl -n "$LABCLIP_RUN_NAMESPACE"')
        self.assertLess(declaration, first_use)


if __name__ == "__main__":
    unittest.main()
