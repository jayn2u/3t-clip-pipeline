import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Sequence

from apply_kubeflow import (
    KubeflowApplyError,
    TERRAFORM_ROOT,
    TerraformOwnerInventory,
    _read_documents,
    _validate_terraform_ownership,
    _verify_approved,
    require_not_owned_by_argo_stack,
    terraform_owner_inventory,
)
from prepare_kubeflow_overlay import RenderApprovalError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATED_ROOT = REPOSITORY_ROOT / "generated"


@dataclass(frozen=True)
class DriftReport:
    has_drift: bool
    changed_objects: int
    summary: str


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checked_objects: int
    pending_objects: tuple[str, ...]


class KubeflowCheckError(RuntimeError):
    pass


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise KubeflowCheckError(f"Command timed out: {' '.join(command)}") from exc
    except OSError as exc:
        raise KubeflowCheckError(f"Unable to execute {command[0]}.") from exc


def _result_text(result: subprocess.CompletedProcess) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part).strip()


def check_distribution(
    manifest: Path,
    receipt: Path,
    *,
    terraform_dir: Path = TERRAFORM_ROOT,
    terraform_var_files: Sequence[Path | str] = (),
    terraform_vars: Sequence[str] = (),
) -> DriftReport:
    manifest_path = Path(manifest).expanduser().absolute()
    receipt_path = Path(receipt).expanduser().absolute()
    owner_inventory = terraform_owner_inventory(
        terraform_dir,
        variable_files=terraform_var_files,
        variables=terraform_vars,
    )
    _validate_terraform_ownership(manifest_path, owner_inventory)
    command = ["kubectl", "diff", "-f", str(manifest_path)]
    _verify_approved(manifest_path, receipt_path)
    result = _run(command)
    if result.returncode == 0:
        return DriftReport(False, 0, "Kubeflow manifest matches the live cluster.")
    if result.returncode != 1:
        raise KubeflowCheckError(
            f"kubectl diff failed with exit code {result.returncode}; raw output was withheld."
        )
    diff = _result_text(result)
    changed_objects = sum(1 for line in diff.splitlines() if line.startswith("diff "))
    changed_objects = max(changed_objects, 1)
    return DriftReport(
        True,
        changed_objects,
        f"Kubeflow manifest differs from the live cluster for {changed_objects} object(s).",
    )


def _resource_key(document: dict) -> tuple[str, str, str]:
    metadata = document.get("metadata", {})
    return (
        str(document.get("kind", "")),
        str(metadata.get("namespace", "")),
        str(metadata.get("name", "")),
    )


def _workload_ready(desired: dict, current: dict | None) -> bool:
    if current is None:
        return False
    kind = desired.get("kind")
    status = current.get("status", {})
    generation = current.get("metadata", {}).get("generation")
    observed_generation = status.get("observedGeneration")
    if (
        type(generation) is not int
        or type(observed_generation) is not int
        or observed_generation < generation
    ):
        return False
    if kind == "DaemonSet":
        desired_scheduled = int(status.get("desiredNumberScheduled", 0))
        return (
            desired_scheduled > 0
            and int(status.get("numberReady", 0)) >= desired_scheduled
            and int(status.get("numberAvailable", 0)) >= desired_scheduled
        )
    desired_replicas = int(desired.get("spec", {}).get("replicas", 1))
    if desired_replicas == 0:
        return True
    if kind == "Deployment":
        return int(status.get("availableReplicas", 0)) >= desired_replicas
    return int(status.get("readyReplicas", 0)) >= desired_replicas


def check_readiness(
    manifest: Path,
    receipt: Path,
    *,
    terraform_dir: Path = TERRAFORM_ROOT,
    terraform_var_files: Sequence[Path | str] = (),
    terraform_vars: Sequence[str] = (),
) -> ReadinessReport:
    manifest_path = Path(manifest).expanduser().absolute()
    receipt_path = Path(receipt).expanduser().absolute()
    owner_inventory = terraform_owner_inventory(
        terraform_dir,
        variable_files=terraform_var_files,
        variables=terraform_vars,
    )
    _validate_terraform_ownership(manifest_path, owner_inventory)
    _verify_approved(manifest_path, receipt_path)
    command = [
        "kubectl",
        "get",
        "deployments,statefulsets,daemonsets,persistentvolumeclaims",
        "--all-namespaces",
        "-o",
        "json",
    ]
    result = _run(command)
    if result.returncode != 0:
        raise KubeflowCheckError(
            f"kubectl get readiness resources failed with exit code {result.returncode}; raw output was withheld."
        )
    try:
        live_items = json.loads(result.stdout).get("items", [])
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        raise KubeflowCheckError("kubectl returned malformed readiness data.") from exc
    live_by_key = {_resource_key(item): item for item in live_items}
    documents = _read_documents(manifest_path)
    expected = [
        item
        for item in documents
        if item.get("kind") in {"Deployment", "StatefulSet", "DaemonSet", "PersistentVolumeClaim"}
    ]
    pending = []
    for desired in expected:
        key = _resource_key(desired)
        current = live_by_key.get(key)
        if desired.get("kind") == "PersistentVolumeClaim":
            ready = current is not None and current.get("status", {}).get("phase") == "Bound"
        else:
            ready = _workload_ready(desired, current)
        if not ready:
            pending.append("/".join(part for part in key if part))
    return ReadinessReport(not pending, len(expected), tuple(sorted(pending)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=GENERATED_ROOT / "rendered.yaml")
    parser.add_argument("--receipt", type=Path, default=GENERATED_ROOT / "receipt.json")
    parser.add_argument("--drift-only", action="store_true")
    parser.add_argument("--readiness-only", action="store_true")
    parser.add_argument("--terraform-dir", type=Path, default=TERRAFORM_ROOT)
    parser.add_argument("--terraform-var-file", action="append", type=Path, default=[])
    parser.add_argument("--terraform-var", action="append", default=[])
    args = parser.parse_args()
    if args.drift_only and args.readiness_only:
        parser.error("--drift-only and --readiness-only cannot be combined.")
    try:
        owners = terraform_owner_inventory(
            args.terraform_dir,
            variable_files=args.terraform_var_file,
            variables=args.terraform_var,
        )
        require_not_owned_by_argo_stack(owners.argo_namespace)
        if not args.readiness_only:
            report = check_distribution(
                args.manifest,
                args.receipt,
                terraform_dir=args.terraform_dir,
                terraform_var_files=args.terraform_var_file,
                terraform_vars=args.terraform_var,
            )
            print(report.summary)
        if not args.drift_only:
            report = check_readiness(
                args.manifest,
                args.receipt,
                terraform_dir=args.terraform_dir,
                terraform_var_files=args.terraform_var_file,
                terraform_vars=args.terraform_var,
            )
            if report.ready:
                print(f"Kubeflow workloads and claims are ready ({report.checked_objects} checked).")
            else:
                print(f"Kubeflow has {len(report.pending_objects)} resource(s) not ready:")
                for item in report.pending_objects:
                    print(f"  {item}")
                raise KubeflowCheckError("Kubeflow readiness check failed.")
    except (KubeflowApplyError, KubeflowCheckError, RenderApprovalError, OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
