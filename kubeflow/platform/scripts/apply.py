import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Sequence

import yaml

from prepare import RenderApprovalError, verify_render_approval


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATED_ROOT = REPOSITORY_ROOT / "generated"
TERRAFORM_ROOT = REPOSITORY_ROOT.parent / "infra" / "terraform"
MAX_APPLY_ATTEMPTS = 6
FOREIGN_STACK_NAME = "argo"
OWNER_LABEL_JSONPATH = "{.metadata.labels.labclip\\.io/iac-stack}"
TERRAFORM_CONSOLE_EXPRESSION = """jsonencode({
  argo_namespace = var.argo_namespace,
  labclip_run_namespace = var.labclip_run_namespace,
  enable_tailscale = var.enable_tailscale,
  nodes = { for node_name, node in var.nodes : node_name => {
    minio_role = node.minio_role,
    cache_claim = node.cache_claim
  } }
})
"""
SENSITIVE_TERRAFORM_VARIABLES = {
    "ghcr_dockerconfigjson",
    "minio_credentials",
    "minio_root_password",
    "minio_root_user",
    "tailscale_oauth_client_id",
    "tailscale_oauth_client_secret",
    "wandb_api_key",
    "wandb_entity",
    "wandb_project",
}
RETRYABLE_ERROR_PATTERNS = (
    re.compile(r"no matches for kind", re.IGNORECASE),
    re.compile(r"resource mapping not found", re.IGNORECASE),
    re.compile(r"the server could not find the requested resource", re.IGNORECASE),
    re.compile(r"no endpoints available for service", re.IGNORECASE),
    re.compile(r"failed calling webhook.*service.*not found", re.IGNORECASE | re.DOTALL),
)


class KubeflowApplyError(RuntimeError):
    pass


@dataclass(frozen=True)
class TerraformOwnerInventory:
    argo_namespace: str
    run_namespace: str
    enable_tailscale: bool
    cache_claims: frozenset[str]
    minio_roles: frozenset[str]


def terraform_owner_inventory(
    terraform_dir: Path = TERRAFORM_ROOT,
    *,
    variable_files: Sequence[Path | str] = (),
    variables: Sequence[str] = (),
) -> TerraformOwnerInventory:
    directory = Path(terraform_dir).expanduser().absolute()
    command = ["terraform", f"-chdir={directory}", "console", "-no-color"]
    for variable_file in variable_files:
        command.append(f"-var-file={Path(variable_file).expanduser().absolute()}")
    for variable in variables:
        variable_name = variable.partition("=")[0].strip()
        if variable_name in SENSITIVE_TERRAFORM_VARIABLES:
            raise KubeflowApplyError(
                "Sensitive Terraform variables must be read from a private variable file."
            )
        command.append(f"-var={variable}")
    console_expression = " ".join(TERRAFORM_CONSOLE_EXPRESSION.split())
    try:
        result = subprocess.run(
            command,
            input=f"{console_expression}\n",
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KubeflowApplyError(
            "Unable to resolve Terraform-owned resource names; Kubeflow apply is blocked."
        ) from exc
    if result.returncode != 0:
        raise KubeflowApplyError(
            "Unable to resolve Terraform-owned resource names; Kubeflow apply is blocked."
        )
    try:
        payload = json.loads(result.stdout.strip())
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise TypeError
        argo_namespace = payload["argo_namespace"]
        run_namespace = payload["labclip_run_namespace"]
        enable_tailscale = payload["enable_tailscale"]
        nodes = payload["nodes"]
        if not isinstance(argo_namespace, str) or not argo_namespace:
            raise TypeError
        if not isinstance(run_namespace, str) or not run_namespace:
            raise TypeError
        if type(enable_tailscale) is not bool:
            raise TypeError
        if not isinstance(nodes, dict) or not nodes:
            raise TypeError
        cache_claims = set()
        minio_roles = set()
        for node in nodes.values():
            if not isinstance(node, dict):
                raise TypeError
            cache_claim = node.get("cache_claim")
            minio_role = node.get("minio_role")
            if not isinstance(cache_claim, str) or not cache_claim:
                raise TypeError
            if not isinstance(minio_role, str) or not minio_role:
                raise TypeError
            cache_claims.add(cache_claim)
            minio_roles.add(minio_role)
    except (KeyError, TypeError, json.JSONDecodeError):
        raise KubeflowApplyError(
            "Terraform returned an incomplete owner inventory; Kubeflow apply is blocked."
        ) from None
    return TerraformOwnerInventory(
        argo_namespace=argo_namespace,
        run_namespace=run_namespace,
        enable_tailscale=enable_tailscale,
        cache_claims=frozenset(cache_claims),
        minio_roles=frozenset(minio_roles),
    )


def _render_paths(receipt: Path) -> tuple[Path, Path]:
    return Path(receipt).with_name("inventory.json"), Path(receipt).with_name("approval.json")


def _parse_documents(manifest_bytes: bytes) -> list[dict]:
    rendered = manifest_bytes.decode("utf-8")
    documents = [item for item in yaml.safe_load_all(rendered) if item]
    if not documents:
        raise ValueError("The Kubeflow manifest contains no Kubernetes objects.")
    return documents


def _read_documents(manifest: Path) -> list[dict]:
    return _parse_documents(Path(manifest).read_bytes())


def _validate_terraform_ownership(
    manifest: Path,
    owner_inventory: TerraformOwnerInventory,
    *,
    documents: list[dict] | None = None,
) -> None:
    documents = _read_documents(manifest) if documents is None else documents
    reserved_namespaces = {
        owner_inventory.argo_namespace,
        "nvidia-device-plugin",
        "tailscale",
    }
    minio_secret_names = {
        name
        for role in owner_inventory.minio_roles
        for name in (f"{role}-secret", f"{role}-researcher-secret")
    }
    source_secret_names = minio_secret_names | {
        "ghcr-secret",
        "ghcr-pull-secret",
        "wandb-secret",
    }
    run_secret_names = minio_secret_names | {"ghcr-secret", "wandb-secret"}
    conflicts = []
    for item in documents:
        metadata = item.get("metadata", {})
        kind = item.get("kind", "")
        name = str(metadata.get("name", ""))
        namespace = str(metadata.get("namespace", ""))
        identity = (
            str(item.get("apiVersion", "")),
            str(kind),
            namespace,
            name,
        )
        owned = (
            (kind == "Namespace" and name in reserved_namespaces)
            or namespace in reserved_namespaces
            or (kind == "StorageClass" and name == "labclip-local-cache")
            or (kind == "PersistentVolume" and name in owner_inventory.cache_claims)
            or (
                kind == "PersistentVolumeClaim"
                and namespace in {owner_inventory.argo_namespace, owner_inventory.run_namespace}
                and name in owner_inventory.cache_claims
            )
            or (kind == "Secret" and namespace == owner_inventory.argo_namespace and name in source_secret_names)
            or (kind == "Secret" and namespace == owner_inventory.run_namespace and name in run_secret_names)
        )
        if owned:
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


def require_not_owned_by_argo_stack(argo_namespace: str) -> None:
    result = _run(
        [
            "kubectl",
            "get",
            "namespace",
            argo_namespace,
            "--ignore-not-found",
            "-o",
            f"jsonpath={OWNER_LABEL_JSONPATH}",
        ],
        timeout=30,
    )
    if result.returncode == 0 and result.stdout.strip() == FOREIGN_STACK_NAME:
        raise KubeflowApplyError(
            f"The {argo_namespace} namespace belongs to the Argo IaC stack; "
            "remove that stack before applying Kubeflow."
        )


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


def _wait_for_crds(crd_names: Sequence[str]) -> None:
    names = tuple(sorted(set(crd_names)))
    if not names:
        raise KubeflowApplyError(
            "The manifest names no CRDs for the unavailable resource; retry is blocked."
        )
    command = [
        "kubectl",
        "wait",
        "--for=condition=Established",
        *(f"crd/{name}" for name in names),
        "--timeout=120s",
    ]
    result = _run(command, timeout=130)
    if result.returncode != 0:
        message = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        raise KubeflowApplyError(message or "Kubeflow CRDs did not become Established.")


def _apply_once(
    manifest: Path,
    receipt: Path,
    apply_path: Path,
    *,
    server_side: bool,
    expected_manifest_sha256: str,
) -> subprocess.CompletedProcess:
    command = ["kubectl", "apply"]
    if server_side:
        command.append("--server-side")
    command.extend(["-f", str(apply_path)])
    approval = _verify_approved(manifest, receipt)
    if approval.get("sha256") != expected_manifest_sha256:
        raise RenderApprovalError(
            "The approved manifest changed after parsing; apply is blocked."
        )
    current_sha256 = hashlib.sha256(Path(manifest).read_bytes()).hexdigest()
    if current_sha256 != expected_manifest_sha256:
        raise RenderApprovalError(
            "The approved manifest changed after parsing; apply is blocked."
        )
    return _run(command)


def _apply_crds(
    manifest: Path,
    receipt: Path,
    documents: list[dict],
    expected_manifest_sha256: str,
) -> bool:
    custom_resources = [item for item in documents if item.get("kind") == "CustomResourceDefinition"]
    if not custom_resources:
        return False
    crd_names = sorted(
        {
            str(item.get("metadata", {}).get("name", ""))
            for item in custom_resources
        }
    )
    if not all(crd_names):
        raise KubeflowApplyError("A rendered CRD has no metadata.name; apply is blocked.")
    with tempfile.TemporaryDirectory(prefix="kubeflow-crds-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        crd_manifest = directory / "crds.yaml"
        crd_manifest.write_text(
            "---\n".join(yaml.safe_dump(item, sort_keys=False) for item in custom_resources),
            encoding="utf-8",
        )
        crd_manifest.chmod(0o600)
        result = _apply_once(
            manifest,
            receipt,
            crd_manifest,
            server_side=True,
            expected_manifest_sha256=expected_manifest_sha256,
        )
    if result.returncode != 0:
        raise KubeflowApplyError(
            f"Kubeflow CRD server-side apply failed with exit code {result.returncode}; command output was withheld."
        )
    _wait_for_crds(crd_names)
    return True


def _apply_distribution_body(
    manifest: Path,
    receipt: Path,
    documents: list[dict],
    crd_names: Sequence[str],
    *,
    max_attempts: int,
    first_attempt: int,
    expected_manifest_sha256: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="kubeflow-body-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        body_manifest = directory / "distribution.yaml"
        body_manifest.write_text(
            "---\n".join(yaml.safe_dump(item, sort_keys=False) for item in documents),
            encoding="utf-8",
        )
        body_manifest.chmod(0o600)
        for attempt in range(first_attempt, max_attempts + 1):
            result = _apply_once(
                manifest,
                receipt,
                body_manifest,
                server_side=False,
                expected_manifest_sha256=expected_manifest_sha256,
            )
            if result.returncode == 0:
                return
            message = _result_text(result)
            if "conflict" in message.lower():
                raise KubeflowApplyError(
                    f"Kubeflow client-side apply reported a field conflict with exit code {result.returncode}; command output was withheld."
                )
            if not _is_retryable_error(message):
                raise KubeflowApplyError(
                    f"Kubeflow client-side apply failed with exit code {result.returncode}; command output was withheld."
                )
            if attempt == max_attempts:
                raise KubeflowApplyError(
                    f"Kubeflow client-side apply still reports an unavailable dependency after the bounded retry window of at most {max_attempts} apply attempts; last exit code {result.returncode}, command output was withheld."
                )
            if _is_missing_crd_error(message):
                _wait_for_crds(crd_names)
            else:
                time.sleep(min(2 ** (attempt - first_attempt), 4))


def apply_distribution(
    manifest: Path,
    receipt: Path,
    *,
    max_attempts: int = MAX_APPLY_ATTEMPTS,
    terraform_dir: Path = TERRAFORM_ROOT,
    terraform_var_files: Sequence[Path | str] = (),
    terraform_vars: Sequence[str] = (),
) -> None:
    if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_APPLY_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {MAX_APPLY_ATTEMPTS}.")
    manifest_path = Path(manifest).expanduser().absolute()
    receipt_path = Path(receipt).expanduser().absolute()
    initial_approval = _verify_approved(manifest_path, receipt_path)
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if initial_approval.get("sha256") != manifest_sha256:
        raise RenderApprovalError(
            "The approved manifest changed before parsing; apply is blocked."
        )
    documents = _parse_documents(manifest_bytes)
    owner_inventory = terraform_owner_inventory(
        terraform_dir,
        variable_files=terraform_var_files,
        variables=terraform_vars,
    )
    _validate_terraform_ownership(
        manifest_path,
        owner_inventory,
        documents=documents,
    )
    crd_names = tuple(
        sorted(
            {
                str(item.get("metadata", {}).get("name", ""))
                for item in documents
                if item.get("kind") == "CustomResourceDefinition"
            }
        )
    )
    if "" in crd_names:
        raise KubeflowApplyError("A rendered CRD has no metadata.name; apply is blocked.")
    has_crds = any(item.get("kind") == "CustomResourceDefinition" for item in documents)
    if has_crds and max_attempts < 2:
        raise ValueError("max_attempts must leave one attempt for the complete Kubeflow manifest.")
    crds_applied = _apply_crds(
        manifest_path,
        receipt_path,
        documents,
        manifest_sha256,
    )
    distribution_documents = [
        item for item in documents if item.get("kind") != "CustomResourceDefinition"
    ]
    if not distribution_documents:
        return
    first_attempt = 2 if crds_applied else 1
    _apply_distribution_body(
        manifest_path,
        receipt_path,
        distribution_documents,
        crd_names,
        max_attempts=max_attempts,
        first_attempt=first_attempt,
        expected_manifest_sha256=manifest_sha256,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=GENERATED_ROOT / "rendered.yaml")
    parser.add_argument("--receipt", type=Path, default=GENERATED_ROOT / "receipt.json")
    parser.add_argument("--max-attempts", type=int, default=MAX_APPLY_ATTEMPTS)
    parser.add_argument("--terraform-dir", type=Path, default=TERRAFORM_ROOT)
    parser.add_argument("--terraform-var-file", action="append", type=Path, default=[])
    parser.add_argument("--terraform-var", action="append", default=[])
    args = parser.parse_args()
    try:
        owners = terraform_owner_inventory(
            args.terraform_dir,
            variable_files=args.terraform_var_file,
            variables=args.terraform_var,
        )
        require_not_owned_by_argo_stack(owners.argo_namespace)
        apply_distribution(
            args.manifest,
            args.receipt,
            max_attempts=args.max_attempts,
            terraform_dir=args.terraform_dir,
            terraform_var_files=args.terraform_var_file,
            terraform_vars=args.terraform_var,
        )
    except (KubeflowApplyError, RenderApprovalError, OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print("Kubeflow distribution applied.")


if __name__ == "__main__":
    main()
