import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from prepare_kubeflow_overlay import approve_render


def default_terraform_owners():
    return SimpleNamespace(
        argo_namespace="argo",
        run_namespace="kubeflow-user-labclip-example-com",
        enable_tailscale=False,
        cache_claims={"labclip-cache-vis-lab", "labclip-cache-ubuntu"},
        minio_roles={"minio-code", "minio-ml-assets"},
    )


def load_apply_tools():
    if "apply_kubeflow" in sys.modules:
        return sys.modules["apply_kubeflow"]
    script = SCRIPTS_ROOT / "apply_kubeflow.py"
    if not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location("apply_kubeflow", script)
    if spec is None or spec.loader is None:
        raise AssertionError("Kubeflow apply module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_check_tools():
    if "check_kubeflow" in sys.modules:
        return sys.modules["check_kubeflow"]
    script = SCRIPTS_ROOT / "check_kubeflow.py"
    if not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location("check_kubeflow", script)
    if spec is None or spec.loader is None:
        raise AssertionError("Kubeflow drift module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_render(root: Path, extra_documents=()) -> tuple[Path, Path, Path, Path, str]:
    manifest_path = root / "rendered.yaml"
    receipt_path = root / "receipt.json"
    inventory_path = root / "inventory.json"
    approval_path = root / "approval.json"
    email = "test@lab.example"
    documents = [
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "istio-ingressgateway", "namespace": "istio-system"},
            "spec": {"type": "ClusterIP"},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "dex", "namespace": "auth"},
            "data": {
                "config.yaml": yaml.safe_dump(
                    {
                        "staticPasswords": [
                            {"email": email, "username": "test", "hashFromEnv": "DEX_USER_PASSWORD"}
                        ]
                    }
                )
            },
        },
        {
            "apiVersion": "kubeflow.org/v1beta1",
            "kind": "Profile",
            "metadata": {"name": "kubeflow-user-test-lab-example"},
            "spec": {"owner": {"name": email}},
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "dex-passwords", "namespace": "auth"},
            "stringData": {"DEX_USER_PASSWORD": "$2b$12$" + "a" * 53},
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {"name": "kubeflow-tailnet", "namespace": "istio-system"},
            "spec": {
                "ingressClassName": "tailscale",
                "tls": [{"hosts": ["labclip-kubeflow"]}],
                "rules": [
                    {
                        "host": "labclip-kubeflow",
                        "http": {
                            "paths": [
                                {
                                    "path": "/",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "istio-ingressgateway",
                                            "port": {"number": 80},
                                        }
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        },
    ]
    documents.extend(extra_documents)
    manifest_text = "---\n".join(yaml.safe_dump(item, sort_keys=False) for item in documents)
    manifest_path.write_text(manifest_text, encoding="utf-8")
    manifest_path.chmod(0o600)
    inventory = sorted(
        (
            item["apiVersion"],
            item["kind"],
            item.get("metadata", {}).get("namespace", ""),
            item["metadata"]["name"],
        )
        for item in documents
    )
    inventory_text = json.dumps([list(row) for row in inventory], indent=2) + "\n"
    inventory_path.write_text(inventory_text, encoding="utf-8")
    inventory_path.chmod(0o600)
    digest = hashlib.sha256(manifest_text.encode()).hexdigest()
    receipt = {
        "schema_version": 1,
        "source_ref": "github.com/kubeflow/community-distribution/example?ref=f09f3eeaa25cc852665f460497a42b7fc68639ac",
        "source_tag": "26.03.1",
        "source_commit": "f09f3eeaa25cc852665f460497a42b7fc68639ac",
        "sha256": digest,
        "inventory_sha256": hashlib.sha256(inventory_text.encode()).hexdigest(),
        "object_count": len(inventory),
        "manifest": manifest_path.name,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path.chmod(0o600)
    return manifest_path, receipt_path, inventory_path, approval_path, digest


class KubeflowApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = load_apply_tools()
        self.owner_patch = mock.patch.object(
            self.tools,
            "terraform_owner_inventory",
            return_value=default_terraform_owners(),
            create=True,
        )
        self.owner_patch.start()
        self.temp = tempfile.TemporaryDirectory(prefix="kubeflow-apply-tests-")
        self.root = Path(self.temp.name)
        self.manifest, self.receipt, self.inventory, self.approval, self.digest = make_render(
            self.root
        )
        approve_render(
            self.digest,
            self.manifest,
            self.receipt,
            self.inventory,
            self.approval,
        )

    def tearDown(self) -> None:
        self.owner_patch.stop()
        self.temp.cleanup()

    def require_implementation(self):
        self.assertIsNotNone(self.tools, "Kubeflow apply tooling is not implemented")

    def test_apply_rejects_digest_mismatch(self) -> None:
        self.require_implementation()
        self.manifest.write_text(self.manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                self.tools.apply_distribution(self.manifest, self.receipt)
        run.assert_not_called()

    def test_apply_rejects_a_self_generated_receipt_without_approval(self) -> None:
        self.require_implementation()
        self.approval.unlink()
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                self.tools.apply_distribution(self.manifest, self.receipt)
        run.assert_not_called()

    def test_apply_retries_missing_crd_only(self) -> None:
        self.require_implementation()
        crd = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "profiles.kubeflow.org"},
            "spec": {},
        }
        manifest, receipt, inventory_path, approval, digest = make_render(
            self.root,
            extra_documents=(crd,),
        )
        approve_render(digest, manifest, receipt, inventory_path, approval)
        results = [
            mock.Mock(returncode=0, stdout="crd applied", stderr=""),
            mock.Mock(returncode=0, stdout="crd established", stderr=""),
            mock.Mock(returncode=1, stdout="", stderr='no matches for kind "Profile" in version "kubeflow.org/v1beta1"'),
            mock.Mock(returncode=0, stdout="customresourcedefinition.apiextensions.k8s.io/profiles.kubeflow.org condition met", stderr=""),
            mock.Mock(returncode=0, stdout="applied", stderr=""),
        ]
        with mock.patch.object(self.tools.subprocess, "run", side_effect=results) as run:
            self.tools.apply_distribution(manifest, receipt)
        commands = [call.args[0] for call in run.call_args_list]
        apply_commands = [command for command in commands if command[1] == "apply"]
        wait_commands = [command for command in commands if command[1] == "wait"]
        self.assertEqual(3, len(apply_commands))
        self.assertEqual(2, len(wait_commands))
        self.assertNotIn("--force-conflicts", [argument for command in commands for argument in command])

    def test_apply_stops_on_field_conflict(self) -> None:
        self.require_implementation()
        result = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Apply failed with conflict: field manager owns spec.selector",
        )
        with mock.patch.object(self.tools.subprocess, "run", return_value=result) as run:
            with self.assertRaises(self.tools.KubeflowApplyError):
                self.tools.apply_distribution(self.manifest, self.receipt)
        self.assertEqual(1, run.call_count)
        command = run.call_args.args[0]
        self.assertEqual(["kubectl", "apply", "-f"], command[:3])
        self.assertNotIn("--force-conflicts", command)

    def test_apply_never_exceeds_six_attempts(self) -> None:
        self.require_implementation()
        crd = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "profiles.kubeflow.org"},
            "spec": {},
        }
        manifest, receipt, inventory_path, approval, digest = make_render(
            self.root,
            extra_documents=(crd,),
        )
        approve_render(digest, manifest, receipt, inventory_path, approval)
        missing_crd = mock.Mock(
            returncode=1,
            stdout="",
            stderr='no matches for kind "Profile" in version "kubeflow.org/v1beta1"',
        )
        established = mock.Mock(returncode=0, stdout="Established", stderr="")
        responses = [
            mock.Mock(returncode=0, stdout="crd applied", stderr=""),
            established,
        ]
        for attempt in range(5):
            responses.append(missing_crd)
            if attempt < 4:
                responses.append(established)
        with mock.patch.object(self.tools.subprocess, "run", side_effect=responses) as run:
            with self.assertRaises(self.tools.KubeflowApplyError):
                self.tools.apply_distribution(manifest, receipt, max_attempts=6)
        apply_count = sum(call.args[0][1] == "apply" for call in run.call_args_list)
        self.assertEqual(6, apply_count)
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                self.tools.apply_distribution(self.manifest, self.receipt, max_attempts=7)
        run.assert_not_called()

    def test_apply_rejects_terraform_owned_objects(self) -> None:
        self.require_implementation()
        self.manifest.write_text(
            self.manifest.read_text(encoding="utf-8")
            + "---\napiVersion: v1\nkind: Namespace\nmetadata:\n  name: argo\n",
            encoding="utf-8",
        )
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(self.tools.KubeflowApplyError):
                self.tools.apply_distribution(self.manifest, self.receipt)
        run.assert_not_called()

    def test_apply_establishes_crds_before_the_distribution(self) -> None:
        self.require_implementation()
        crd = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "profiles.kubeflow.org"},
            "spec": {},
        }
        second_crd = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "experiments.kubeflow.org"},
            "spec": {},
        }
        manifest, receipt, inventory, approval, digest = make_render(
            self.root,
            extra_documents=(crd, second_crd),
        )
        approve_render(digest, manifest, receipt, inventory, approval)
        responses = [
            mock.Mock(returncode=0, stdout="crd applied", stderr=""),
            mock.Mock(returncode=0, stdout="crd established", stderr=""),
            mock.Mock(returncode=0, stdout="distribution applied", stderr=""),
        ]
        with mock.patch.object(self.tools.subprocess, "run", side_effect=responses) as run:
            self.tools.apply_distribution(manifest, receipt)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual("apply", commands[0][1])
        self.assertNotEqual(str(manifest), commands[0][-1])
        self.assertEqual("wait", commands[1][1])
        self.assertIn("crd/profiles.kubeflow.org", commands[1])
        self.assertIn("crd/experiments.kubeflow.org", commands[1])
        self.assertNotIn("--all", commands[1])
        self.assertEqual(str(manifest), commands[2][-1])


class KubeflowDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.apply_tools = load_apply_tools()
        self.tools = load_check_tools()
        self.owner_patch = mock.patch.object(
            self.tools,
            "terraform_owner_inventory",
            return_value=default_terraform_owners(),
            create=True,
        )
        self.owner_patch.start()
        self.temp = tempfile.TemporaryDirectory(prefix="kubeflow-drift-tests-")
        self.root = Path(self.temp.name)
        self.manifest, self.receipt, self.inventory, self.approval, self.digest = make_render(
            self.root
        )
        approve_render(
            self.digest,
            self.manifest,
            self.receipt,
            self.inventory,
            self.approval,
        )

    def tearDown(self) -> None:
        self.owner_patch.stop()
        self.temp.cleanup()

    def test_drift_check_is_read_only(self) -> None:
        self.assertIsNotNone(self.tools, "Kubeflow drift tooling is not implemented")
        output = "diff -u -N live/deployment.yaml desired/deployment.yaml\n- secret-data-sentinel"
        result = mock.Mock(returncode=1, stdout=output, stderr="")
        with mock.patch.object(self.tools.subprocess, "run", return_value=result) as run:
            report = self.tools.check_distribution(self.manifest, self.receipt)
        command = run.call_args.args[0]
        self.assertEqual(["kubectl", "diff", "-f"], command[:3])
        self.assertTrue(report.has_drift)
        self.assertEqual(1, report.changed_objects)
        self.assertNotIn("secret-data-sentinel", report.summary)
        self.assertNotIn("apply", command)

    def test_drift_check_rejects_an_unapproved_render(self) -> None:
        self.assertIsNotNone(self.tools, "Kubeflow drift tooling is not implemented")
        self.approval.unlink()
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                self.tools.check_distribution(self.manifest, self.receipt)
        run.assert_not_called()

    def test_readiness_reports_unbound_claim_and_unavailable_deployment(self) -> None:
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "dashboard-test", "namespace": "kubeflow"},
            "spec": {"replicas": 2},
        }
        claim = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "cache-test", "namespace": "kubeflow"},
            "spec": {},
        }
        manifest, receipt, inventory, approval, digest = make_render(
            self.root,
            extra_documents=(deployment, claim),
        )
        approve_render(digest, manifest, receipt, inventory, approval)
        live = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"name": "dashboard-test", "namespace": "kubeflow"},
                    "spec": {"replicas": 2},
                    "status": {"availableReplicas": 1},
                },
                {
                    "kind": "PersistentVolumeClaim",
                    "metadata": {"name": "cache-test", "namespace": "kubeflow"},
                    "status": {"phase": "Pending"},
                },
            ]
        }
        result = mock.Mock(returncode=0, stdout=json.dumps(live), stderr="")
        with mock.patch.object(self.tools.subprocess, "run", return_value=result) as run:
            report = self.tools.check_readiness(manifest, receipt)
        self.assertFalse(report.ready)
        self.assertEqual(2, report.checked_objects)
        self.assertEqual(
            ("Deployment/kubeflow/dashboard-test", "PersistentVolumeClaim/kubeflow/cache-test"),
            report.pending_objects,
        )
        self.assertEqual("get", run.call_args.args[0][1])

    def test_readiness_requires_observed_generation(self) -> None:
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "dashboard-generation-test", "namespace": "kubeflow"},
            "spec": {"replicas": 1},
        }
        statefulset = {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {"name": "database-generation-test", "namespace": "kubeflow"},
            "spec": {"replicas": 1},
        }
        manifest, receipt, inventory, approval, digest = make_render(
            self.root,
            extra_documents=(deployment, statefulset),
        )
        approve_render(digest, manifest, receipt, inventory, approval)
        live_items = [
            {
                "kind": "Deployment",
                "metadata": {
                    "name": "dashboard-generation-test",
                    "namespace": "kubeflow",
                    "generation": 4,
                },
                "spec": {"replicas": 1},
                "status": {"availableReplicas": 1},
            },
            {
                "kind": "StatefulSet",
                "metadata": {
                    "name": "database-generation-test",
                    "namespace": "kubeflow",
                    "generation": 7,
                },
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1},
            },
        ]
        result = mock.Mock(returncode=0, stdout=json.dumps({"items": live_items}), stderr="")
        with mock.patch.object(self.tools.subprocess, "run", return_value=result):
            pending = self.tools.check_readiness(manifest, receipt)
        self.assertFalse(pending.ready)
        self.assertEqual(2, len(pending.pending_objects))
        for live in live_items:
            live["status"]["observedGeneration"] = live["metadata"]["generation"]
        result.stdout = json.dumps({"items": live_items})
        with mock.patch.object(self.tools.subprocess, "run", return_value=result):
            ready = self.tools.check_readiness(manifest, receipt)
        self.assertTrue(ready.ready)


class TerraformOwnerInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = load_apply_tools()
        self.temp = tempfile.TemporaryDirectory(prefix="kubeflow-owner-tests-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_inventory_reads_effective_names_without_emitting_secrets(self) -> None:
        self.assertTrue(
            hasattr(self.tools, "terraform_owner_inventory"),
            "Terraform-owned identities must come from the active Terraform inputs",
        )
        configured = {
            "argo_namespace": "labclip-runtime",
            "labclip_run_namespace": "kubeflow-user-labclip-example-com",
            "enable_tailscale": True,
            "nodes": {
                "gpu-a": {"minio_role": "minio-a", "cache_claim": "cache-a"},
                "gpu-b": {"minio_role": "minio-b", "cache_claim": "cache-b"},
            },
        }
        result = mock.Mock(returncode=0, stdout=json.dumps(json.dumps(configured)), stderr="")
        with mock.patch.object(self.tools.subprocess, "run", return_value=result) as run:
            owners = self.tools.terraform_owner_inventory(
                terraform_dir=REPOSITORY_ROOT / "terraform",
                variable_files=(Path("private.tfvars"),),
                variables=("argo_namespace=labclip-runtime",),
            )
        command = run.call_args.args[0]
        expression = run.call_args.kwargs["input"]
        self.assertTrue(command[-2].endswith("private.tfvars"))
        self.assertIn("-var=argo_namespace=labclip-runtime", command)
        self.assertIn("var.argo_namespace", expression)
        self.assertIn("var.nodes", expression)
        self.assertNotIn("tailscale_oauth_client_secret", expression)
        self.assertEqual("labclip-runtime", owners.argo_namespace)
        self.assertEqual("kubeflow-user-labclip-example-com", owners.run_namespace)
        self.assertEqual({"cache-a", "cache-b"}, owners.cache_claims)
        self.assertEqual({"minio-a", "minio-b"}, owners.minio_roles)

    def test_console_expression_is_submitted_as_one_line(self) -> None:
        self.assertTrue(hasattr(self.tools, "terraform_owner_inventory"))
        configured = {
            "argo_namespace": "argo",
            "labclip_run_namespace": "kubeflow-user-labclip-example-com",
            "enable_tailscale": True,
            "nodes": {"gpu-a": {"minio_role": "minio-a", "cache_claim": "cache-a"}},
        }
        result = mock.Mock(returncode=0, stdout=json.dumps(json.dumps(configured)), stderr="")
        with mock.patch.object(self.tools.subprocess, "run", return_value=result) as run:
            self.tools.terraform_owner_inventory(terraform_dir=REPOSITORY_ROOT / "terraform")
        expression = run.call_args.kwargs["input"]
        self.assertEqual(
            "jsonencode({ argo_namespace = var.argo_namespace, labclip_run_namespace = var.labclip_run_namespace, enable_tailscale = var.enable_tailscale, nodes = { for node_name, node in var.nodes : node_name => { minio_role = node.minio_role, cache_claim = node.cache_claim } } })\n",
            expression,
        )

    def test_inventory_fails_closed_without_terraform_inputs(self) -> None:
        self.assertTrue(hasattr(self.tools, "terraform_owner_inventory"))
        result = mock.Mock(returncode=1, stdout="", stderr="sensitive diagnostic text")
        with mock.patch.object(self.tools.subprocess, "run", return_value=result):
            with self.assertRaises(self.tools.KubeflowApplyError) as raised:
                self.tools.terraform_owner_inventory(terraform_dir=REPOSITORY_ROOT / "terraform")
        self.assertNotIn("sensitive diagnostic text", str(raised.exception))

    def test_inventory_rejects_sensitive_cli_variables(self) -> None:
        self.assertTrue(hasattr(self.tools, "terraform_owner_inventory"))
        for variable in (
            "tailscale_oauth_client_secret=secret-value",
            "minio_root_password=secret-value",
            "wandb_entity=private-team",
            "wandb_entity = private-team",
            "wandb_project=private-project",
        ):
            with self.subTest(variable=variable.partition("=")[0]):
                with mock.patch.object(self.tools.subprocess, "run") as run:
                    with self.assertRaises(self.tools.KubeflowApplyError):
                        self.tools.terraform_owner_inventory(
                            terraform_dir=REPOSITORY_ROOT / "terraform",
                            variables=(variable,),
                        )
                run.assert_not_called()

    def test_guard_uses_configured_argo_and_nvidia_namespaces(self) -> None:
        self.assertIsNotNone(self.tools)
        inventory = SimpleNamespace(
            argo_namespace="labclip-runtime",
            run_namespace="kubeflow-user-labclip-example-com",
            cache_claims={"cache-custom"},
            minio_roles={"minio-custom"},
        )
        documents = (
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "labclip-runtime"}},
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "nvidia-device-plugin"}},
        )
        for document in documents:
            with self.subTest(name=document["metadata"]["name"]):
                destination = self.root / document["metadata"]["name"]
                destination.mkdir(mode=0o700)
                manifest, receipt, inventory_path, approval, digest = make_render(
                    destination, (document,)
                )
                approve_render(digest, manifest, receipt, inventory_path, approval)
                with mock.patch.object(
                    self.tools,
                    "terraform_owner_inventory",
                    return_value=inventory,
                    create=True,
                ):
                    with mock.patch.object(
                        self.tools.subprocess,
                        "run",
                        return_value=mock.Mock(returncode=0, stdout="", stderr=""),
                    ) as run:
                        with self.assertRaises(self.tools.KubeflowApplyError):
                            self.tools.apply_distribution(manifest, receipt)
                run.assert_not_called()

    def test_guard_uses_configured_cache_and_minio_identities(self) -> None:
        self.assertIsNotNone(self.tools)
        inventory = SimpleNamespace(
            argo_namespace="labclip-runtime",
            run_namespace="kubeflow-user-labclip-example-com",
            cache_claims={"cache-custom"},
            minio_roles={"minio-custom"},
        )
        documents = (
            {"apiVersion": "v1", "kind": "PersistentVolume", "metadata": {"name": "cache-custom"}},
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": "cache-custom", "namespace": "kubeflow-user-labclip-example-com"},
            },
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "minio-custom-secret", "namespace": "kubeflow-user-labclip-example-com"},
            },
        )
        for index, document in enumerate(documents):
            with self.subTest(identity=document):
                destination = self.root / f"identity-{index}"
                destination.mkdir(mode=0o700)
                manifest, receipt, inventory_path, approval, digest = make_render(
                    destination, (document,)
                )
                approve_render(digest, manifest, receipt, inventory_path, approval)
                with self.assertRaises(self.tools.KubeflowApplyError):
                    self.tools._validate_terraform_ownership(manifest, inventory)

    def test_guard_does_not_claim_profile_managed_run_namespace(self) -> None:
        self.assertIsNotNone(self.tools)
        inventory = SimpleNamespace(
            argo_namespace="argo",
            run_namespace="kubeflow-user-test-lab-example",
            cache_claims={"labclip-cache-vis-lab"},
            minio_roles={"minio-code"},
        )
        namespace = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": inventory.run_namespace},
        }
        destination = self.root / "profile-managed-namespace"
        destination.mkdir(mode=0o700)
        manifest, receipt, inventory_path, approval, digest = make_render(
            destination, (namespace,)
        )
        approve_render(digest, manifest, receipt, inventory_path, approval)
        self.tools._validate_terraform_ownership(manifest, inventory)
