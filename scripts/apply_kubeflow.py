import argparse
from pathlib import Path
import re
import subprocess
import tempfile
import time

import yaml

from prepare_kubeflow_overlay import RenderApprovalError, verify_render_approval


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATED_ROOT = REPOSITORY_ROOT / "kubeflow/generated"
MAX_APPLY_ATTEMPTS = 6
TERRAFORM_OWNED_IDENTITIES = {
    ("v1", "Namespace", "", "argo"),
    ("v1", "Namespace", "", "tailscale"),
    ("v1", "StorageClass", "", "labclip-local-cache"),
    ("v1", "PersistentVolume", "", "labclip-cache-vis-lab"),
    ("v1", "PersistentVolume", "", "labclip-cache-ubuntu"),
    ("v1", "PersistentVolumeClaim", "argo", "labclip-cache-vis-lab"),
    ("v1", "PersistentVolumeClaim", "argo", "labclip-cache-ubuntu"),
    ("v1", "Secret", "argo", "ghcr-secret"),
    ("v1", "Secret", "argo", "ghcr-pull-secret"),
    ("v1", "Secret", "argo", "wandb-secret"),
    ("v1", "Secret", "argo", "minio-code-secret"),
    ("v1", "Secret", "argo", "minio-ml-assets-secret"),
    ("v1", "Secret", "argo", "minio-code-researcher-secret"),
    ("v1", "Secret", "argo", "minio-ml-assets-researcher-secret"),
    ("apps/v1", "Deployment", "argo", "minio-code"),
    ("apps/v1", "Deployment", "argo", "minio-ml-assets"),
    ("v1", "Service", "argo", "minio-code"),
    ("v1", "Service", "argo", "minio-ml-assets"),
    ("v1", "Secret", "tailscale", "operator-oauth"),
}
TERRAFORM_PROFILE_SECRET_NAMES = {
    "ghcr-secret",
    "wandb-secret",
    "minio-code-secret",
    "minio-ml-assets-secret",
}
TERRAFORM_PROFILE_CLAIM_NAMES = {"labclip-cache-vis-lab", "labclip-cache-ubuntu"}
RETRYABLE_ERROR_PATTERNS = (
    re.compile(r"no matches for kind", re.IGNORECASE),
    re.compile(r"resource mapping not found", re.IGNORECASE),
    re.compile(r"the server could not find the requested resource", re.IGNORECASE),
    re.compile(r"no endpoints available for service", re.IGNORECASE),
    re.compile(r"failed calling webhook.*service.*not found", re.IGNORECASE | re.DOTALL),
)


class KubeflowApplyError(RuntimeError):
    pass


def _render_paths(receipt: Path) -> tuple[Path, Path]:
    return Path(receipt).with_name("inventory.json"), Path(receipt).with_name("approval.json")


def _read_documents(manifest: Path) -> list[dict]:
    documents = [item for item in yaml.safe_load_all(Path(manifest).read_text(encoding="utf-8")) if item]
    if not documents:
        raise ValueError("The Kubeflow manifest contains no Kubernetes objects.")
    return documents


def _validate_terraform_ownership(manifest: Path) -> None:
    documents = _read_documents(manifest)
    profiles = {
        item.get("metadata", {}).get("name", "")
        for item in documents
        if item.get("kind") == "Profile"
    }
    conflicts = []
    for item in documents:
        metadata = item.get("metadata", {})
        identity = (
            str(item.get("apiVersion", "")),
            str(item.get("kind", "")),
            str(metadata.get("namespace", "")),
            str(metadata.get("name", "")),
        )
        if identity in TERRAFORM_OWNED_IDENTITIES:
            conflicts.append("/".join(value for value in identity[1:] if value))
        if metadata.get("namespace") in profiles:
            if item.get("kind") == "Secret" and metadata.get("name") in TERRAFORM_PROFILE_SECRET_NAMES:
                conflicts.append("/".join(value for value in identity[1:] if value))
            if item.get("kind") == "PersistentVolumeClaim" and metadata.get("name") in TERRAFORM_PROFILE_CLAIM_NAMES:
                conflicts.append("/".join(value for value in identity[1:] if value))
    if conflicts:
        names = ", ".join(sorted(set(conflicts)))
        raise KubeflowApplyError(f"The manifest overlaps Terraform-owned Kubernetes objects: {names}.")


def _run(command: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise KubeflowApplyError(f"Command timed out: {' '.join(command)}") from exc
    except OSError as exc:
        raise KubeflowApplyError(f"Unable to execute {command[0]}.") from exc


def _result_text(result: subprocess.CompletedProcess) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part).strip()


def _is_retryable_error(message: str) -> bool:
    return any(pattern.search(message) for pattern in RETRYABLE_ERROR_PATTERNS)


def _is_missing_crd_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "no matches for kind",
            "resource mapping not found",
            "the server could not find the requested resource",
        )
    )


def _verify_approved(manifest: Path, receipt: Path) -> dict:
    inventory, approval = _render_paths(receipt)
    return verify_render_approval(manifest, receipt, approval, inventory)


def _wait_for_crds() -> None:
    command = [
        "kubectl",
        "wait",
        "--for=condition=Established",
        "crd",
        "--all",
        "--timeout=120s",
    ]
    result = _run(command, timeout=130)
    if result.returncode != 0:
        message = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        raise KubeflowApplyError(message or "Kubeflow CRDs did not become Established.")


def _apply_once(manifest: Path, receipt: Path, apply_path: Path) -> subprocess.CompletedProcess:
    command = ["kubectl", "apply", "-f", str(apply_path)]
    _verify_approved(manifest, receipt)
    return _run(command)


def _apply_crds(manifest: Path, receipt: Path, documents: list[dict]) -> bool:
    custom_resources = [item for item in documents if item.get("kind") == "CustomResourceDefinition"]
    if not custom_resources:
        return False
    with tempfile.TemporaryDirectory(prefix="kubeflow-crds-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        crd_manifest = directory / "crds.yaml"
        crd_manifest.write_text(
            "---\n".join(yaml.safe_dump(item, sort_keys=False) for item in custom_resources),
            encoding="utf-8",
        )
        crd_manifest.chmod(0o600)
        result = _apply_once(manifest, receipt, crd_manifest)
    if result.returncode != 0:
        message = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        raise KubeflowApplyError(message or "Kubeflow CRD apply failed.")
    _wait_for_crds()
    return True


def apply_distribution(
    manifest: Path,
    receipt: Path,
    *,
    max_attempts: int = MAX_APPLY_ATTEMPTS,
) -> None:
    if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_APPLY_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {MAX_APPLY_ATTEMPTS}.")
    manifest_path = Path(manifest).expanduser().absolute()
    receipt_path = Path(receipt).expanduser().absolute()
    _validate_terraform_ownership(manifest_path)
    documents = _read_documents(manifest_path)
    has_crds = any(item.get("kind") == "CustomResourceDefinition" for item in documents)
    if has_crds and max_attempts < 2:
        raise ValueError("max_attempts must leave one attempt for the complete Kubeflow manifest.")
    crds_applied = _apply_crds(manifest_path, receipt_path, documents)
    first_attempt = 2 if crds_applied else 1
    for attempt in range(first_attempt, max_attempts + 1):
        result = _apply_once(manifest_path, receipt_path, manifest_path)
        if result.returncode == 0:
            return
        message = _result_text(result)
        if "conflict" in message.lower() or not _is_retryable_error(message):
            raise KubeflowApplyError(message or "Kubeflow apply failed without diagnostic output.")
        if attempt == max_attempts:
            raise KubeflowApplyError(
                f"Kubeflow apply still reports a missing CRD or unavailable dependency after the bounded retry window of at most {max_attempts} apply attempts: {message}"
            )
        if _is_missing_crd_error(message):
            _wait_for_crds()
        else:
            time.sleep(min(2 ** (attempt - first_attempt), 4))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=GENERATED_ROOT / "rendered.yaml")
    parser.add_argument("--receipt", type=Path, default=GENERATED_ROOT / "receipt.json")
    parser.add_argument("--max-attempts", type=int, default=MAX_APPLY_ATTEMPTS)
    args = parser.parse_args()
    try:
        apply_distribution(args.manifest, args.receipt, max_attempts=args.max_attempts)
    except (KubeflowApplyError, RenderApprovalError, OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print("Kubeflow distribution applied.")


if __name__ == "__main__":
    main()
