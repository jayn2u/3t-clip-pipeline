import os
import re
import subprocess
from pathlib import Path

import pytest
from pydantic import RootModel

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class JsonDocument(RootModel[JsonValue]):
    pass


REPOSITORY_ROOT = Path(__file__).parents[2]
RENDERER = REPOSITORY_ROOT / "scripts/render-platform.sh"
EXPECTED_SCHEMA_FILES = {
    "clusterrole-rbac-v1.json",
    "clusterrolebinding-rbac-v1.json",
    "configmap-v1.json",
    "daemonset-apps-v1.json",
    "deployment-apps-v1.json",
    "job-batch-v1.json",
    "namespace-v1.json",
    "networkpolicy-networking-v1.json",
    "persistentvolume-v1.json",
    "persistentvolumeclaim-v1.json",
    "role-rbac-v1.json",
    "rolebinding-rbac-v1.json",
    "service-v1.json",
    "serviceaccount-v1.json",
    "storageclass-storage-v1.json",
}


def _inventory(path: Path) -> list[dict[str, JsonValue]]:
    root = JsonDocument.model_validate_json(path.read_text(encoding="utf-8")).root
    if not isinstance(root, list):
        pytest.fail("inventory_not_list")
    items: list[dict[str, JsonValue]] = []
    for item in root:
        if not isinstance(item, dict):
            pytest.fail("inventory_item_not_mapping")
        items.append(item)
    return items


def _string_field(item: dict[str, JsonValue], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str):
        pytest.fail(f"inventory_{field}_not_string")
    return value


def _run_policy(manifest: Path, inventory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(RENDERER),
            "--validate-only",
            str(manifest),
            "--inventory",
            str(inventory),
        ],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "KUBECONFIG": str(manifest.parent / "must-not-be-read")},
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("duplicate-owner.yaml", "ownership_conflict"),
        ("mutable-image.yaml", "image_not_immutable"),
        ("floating-remote.yaml", "floating_reference"),
    ],
    ids=["duplicate_owner", "mutable_image", "floating_remote"],
)
def test_policy_rejects_unsafe_render(
    fixture: str,
    code: str,
    tmp_path: Path,
) -> None:
    # Given: one malformed manifest fixture and no usable kubeconfig.
    manifest = REPOSITORY_ROOT / "tests/deploy/fixtures" / fixture
    inventory = tmp_path / "inventory.json"

    # When: the render policy boundary validates it.
    result = _run_policy(manifest, inventory)

    # Then: the stable machine error is returned without an inventory artifact.
    assert result.returncode == 2
    assert code in result.stderr
    assert not inventory.exists()


def test_example_overlay_inventory_has_one_owner(tmp_path: Path) -> None:
    # Given: the checked-in example overlay.
    output = tmp_path / "platform.yaml"
    inventory = tmp_path / "resources.json"

    # When: it is rendered twice.
    command = [
        str(RENDERER),
        "--overlay",
        "example",
        "--output",
        str(output),
        "--inventory",
        str(inventory),
    ]
    first = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    first_manifest = output.read_bytes() if output.exists() else b""
    second = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: output is deterministic and every identity occurs once with one owner.
    assert first.returncode == second.returncode == 0
    assert output.read_bytes() == first_manifest
    resources = _inventory(inventory)
    identities = {
        (
            _string_field(item, "apiVersion"),
            _string_field(item, "kind"),
            _string_field(item, "namespace"),
            _string_field(item, "name"),
        )
        for item in resources
    }
    assert len(identities) == len(resources)
    assert all(_string_field(item, "owner") in {"helm", "kustomize"} for item in resources)


def test_lock_contains_exact_frozen_versions_and_digests() -> None:
    lock_path = REPOSITORY_ROOT / "deploy/versions.lock.yaml"
    lock = lock_path.read_text(encoding="utf-8")
    assert 'version: "1.0.19"' in lock
    assert "sourceCommit: 9aeb47ce10339f4a14819335c6a00027353ba0df" in lock
    assert 'version: "0.18.3"' in lock
    assert "sourceCommit: f39ef6267b20ddeb79e717940b71bb98979c49a0" in lock
    assert 'version: "0.17.1"' in lock
    assert "sourceCommit: 3c378193fcebf6e955f0d65bd6f2aeed099ad8ea" in lock
    assert len(re.findall(r"@sha256:[0-9a-f]{64}", lock)) == 7


def test_runner_rbac_is_namespace_scoped_and_least_privilege() -> None:
    rbac_path = REPOSITORY_ROOT / "deploy/kustomize/base/runner-rbac.yaml"
    rbac = rbac_path.read_text(encoding="utf-8")
    assert "kind: ClusterRole" not in rbac
    assert "kind: ClusterRoleBinding" not in rbac
    assert 'verbs: ["*"]' not in rbac
    assert 'resources: ["secrets"]' not in rbac


def test_local_schema_subset_exactly_covers_rendered_inventory(tmp_path: Path) -> None:
    output = tmp_path / "platform.yaml"
    inventory = tmp_path / "resources.json"
    result = subprocess.run(
        [
            str(RENDERER),
            "--overlay",
            "example",
            "--output",
            str(output),
            "--inventory",
            str(inventory),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    schema_directory = REPOSITORY_ROOT / "schemas/kubernetes/v1.36.2-standalone-strict"
    assert {path.name for path in schema_directory.glob("*.json")} == EXPECTED_SCHEMA_FILES
    assert "Skipped: 0" in result.stdout
    validator = (REPOSITORY_ROOT / "scripts/validate-manifest-local.sh").read_text(encoding="utf-8")
    assert "-ignore-missing-schemas=false" in validator


def test_render_contains_no_secret_or_enabled_tailscale(tmp_path: Path) -> None:
    output = tmp_path / "platform.yaml"
    inventory = tmp_path / "resources.json"
    result = subprocess.run(
        [
            str(RENDERER),
            "--overlay",
            "example",
            "--output",
            str(output),
            "--inventory",
            str(inventory),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    resources = _inventory(inventory)
    assert all(_string_field(item, "kind") != "Secret" for item in resources)
    assert all(_string_field(item, "kind") != "Ingress" for item in resources)
    assert "tailscale" not in output.read_text(encoding="utf-8").lower()


def test_storage_and_node_inputs_are_overlay_configurable() -> None:
    overlay = REPOSITORY_ROOT / "deploy/kustomize/overlays/example"
    storage = (overlay / "storage-config.yaml").read_text(encoding="utf-8")
    node = (overlay / "minio-node-config.yaml").read_text(encoding="utf-8")
    assert "/srv/three-t-pipeline" in storage
    assert "example-worker" in storage
    assert "example-worker" in node
    assert not re.search(r"labclip|lab-clip|/mnt/data", storage + node, re.IGNORECASE)


def test_adapter_renders_gpu_release_into_kube_system() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")
    assert "--namespace kube-system" in renderer


def test_lock_pins_are_consumed_by_values_and_archive_checks() -> None:
    deploy_files = [path for path in (REPOSITORY_ROOT / "deploy").rglob("*") if path.is_file()]
    deploy_text = "\n".join(path.read_text(encoding="utf-8") for path in deploy_files)
    lock = (REPOSITORY_ROOT / "deploy/versions.lock.yaml").read_text(encoding="utf-8")
    image_pins = {match.group(0) for match in re.finditer(r"[^\s]+@sha256:[0-9a-f]{64}", lock)}
    assert len(image_pins) == 7
    assert all(image in deploy_text for image in image_pins)
    archive_hashes = {
        match.group(1) for match in re.finditer(r"(?m)^\s+sha256: ([0-9a-f]{64})$", lock)
    }
    scripts = "\n".join(
        (REPOSITORY_ROOT / path).read_text(encoding="utf-8")
        for path in ("scripts/render-platform.sh", "scripts/bootstrap-helm-charts.sh")
    )
    assert len(archive_hashes) == 3
    assert all(archive_hash in scripts for archive_hash in archive_hashes)


def test_renderer_has_no_apply_or_cluster_discovery_command() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")
    assert not re.search(r"\bkubectl\s+(apply|get|config|cluster-info)\b", renderer)
    assert not re.search(r"\bhelm\s+(install|upgrade|uninstall|list)\b", renderer)
