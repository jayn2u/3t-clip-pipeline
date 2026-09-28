import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile
import uuid

import yaml


UPSTREAM_TAG = "26.03.1"
UPSTREAM_COMMIT = "f09f3eeaa25cc852665f460497a42b7fc68639ac"
UPSTREAM_SOURCE_REF = (
    "github.com/kubeflow/community-distribution/example?ref=" + UPSTREAM_COMMIT
)
UPSTREAM_GIT_URL = "https://github.com/kubeflow/community-distribution.git"
UPSTREAM_DEFAULT_EMAIL = "@".join(("user", "example.com"))
DEFAULT_EMAIL = "labclip@example.com"
DEFAULT_TAILNET_HOSTNAME = "labclip-kubeflow"
IDENTITY_KEYS = {
    "email",
    "password",
    "password_hash",
    "tailnet_hostname",
    "user_id",
}
OVERLAY_ROOT = Path(__file__).resolve().parents[1] / "kubeflow/overlays/labclip"


@dataclass(frozen=True)
class IdentityMaterial:
    email: str
    username: str
    profile_name: str
    tailnet_hostname: str
    user_id: str = field(repr=False)
    password: str = field(repr=False)
    password_hash: str = field(repr=False)


@dataclass(frozen=True)
class RenderReceipt:
    source_ref: str
    source_tag: str
    source_commit: str
    sha256: str
    approved: bool
    inventory: tuple[tuple[str, str, str, str], ...]
    output_path: Path
    receipt_path: Path
    inventory_path: Path
    approval_path: Path


class RenderApprovalError(ValueError):
    pass


def _bcrypt():
    try:
        import bcrypt
    except ImportError as exc:
        raise RuntimeError(
            "Install the pinned identity dependency with `uv run --with bcrypt==4.2.1`."
        ) from exc
    if getattr(bcrypt, "__version__", None) != "4.2.1":
        raise RuntimeError("Kubeflow identity preparation requires bcrypt==4.2.1.")
    return bcrypt


def _validate_email(email: str) -> tuple[str, str]:
    if email == UPSTREAM_DEFAULT_EMAIL:
        raise ValueError("The upstream sample Dex identity is not allowed.")
    match = re.fullmatch(
        r"([a-z0-9]+(?:[.-][a-z0-9]+)*)@([a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+(?:-[a-z0-9]+)*)+)",
        email,
    )
    if match is None:
        raise ValueError("The Dex email must be lowercase and form a DNS-safe Profile name.")
    username = match.group(1)
    profile_name = "kubeflow-user-" + email.replace("@", "-").replace(".", "-")
    if len(profile_name) > 63:
        raise ValueError("The email-derived Kubeflow Profile namespace exceeds 63 characters.")
    return username, profile_name


def _validate_tailnet_hostname(hostname: str) -> str:
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname) is None:
        raise ValueError("The Tailscale Ingress host must be a DNS-safe hostname label.")
    return hostname


def _private_directory(path: Path) -> None:
    created = not path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Private Kubeflow output directory must be a real directory.")
    if created:
        path.chmod(0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise PermissionError("Private Kubeflow output directory must have mode 0700.")


def _write_private(path: Path, contents: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _private_directory(path.parent)
    if path.is_symlink():
        raise ValueError("Private Kubeflow output may not replace a symbolic link.")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".kubeflow-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PermissionError("Private Kubeflow files must have mode 0600.")


def _load_identity(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Existing private identity input must be a regular file.")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PermissionError("Existing private identity input must have mode 0600.")
    if path.stat().st_nlink != 1:
        raise PermissionError("Existing private identity input must not have additional hard links.")
    material = json.loads(path.read_text(encoding="utf-8"))
    if set(material) != IDENTITY_KEYS or not all(
        isinstance(material[key], str) and material[key] for key in IDENTITY_KEYS
    ):
        raise ValueError("Existing private identity input has an invalid structure.")
    username, profile_name = _validate_email(material["email"])
    _validate_tailnet_hostname(material["tailnet_hostname"])
    if re.fullmatch(r"[0-9a-f-]{36}", material["user_id"]) is None:
        raise ValueError("Existing private identity has an invalid user ID.")
    bcrypt = _bcrypt()
    try:
        password_matches = bcrypt.checkpw(
            material["password"].encode("utf-8"), material["password_hash"].encode("ascii")
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Existing private identity password hash is invalid.") from exc
    if not password_matches or not material["password_hash"].startswith("$2b$12$"):
        raise ValueError("Existing private identity does not match its non-default password hash.")
    return {
        **material,
        "username": username,
        "profile_name": profile_name,
    }


def _identity_material(material: dict[str, str]) -> IdentityMaterial:
    username, profile_name = _validate_email(material["email"])
    return IdentityMaterial(
        email=material["email"],
        username=username,
        profile_name=profile_name,
        tailnet_hostname=material["tailnet_hostname"],
        user_id=material["user_id"],
        password=material["password"],
        password_hash=material["password_hash"],
    )


def prepare_identity(
    path: Path,
    *,
    email: str | None = None,
    tailnet_hostname: str | None = None,
) -> IdentityMaterial:
    identity_path = Path(path).expanduser().absolute()
    if email is not None:
        _validate_email(email)
    if tailnet_hostname is not None:
        _validate_tailnet_hostname(tailnet_hostname)
    _private_directory(identity_path.parent)
    if identity_path.exists() or identity_path.is_symlink():
        material = _load_identity(identity_path)
        if email is not None and material["email"] != email:
            raise ValueError(
                "Requested identity differs from the existing private file; use a new file path to rotate it."
            )
        if (
            tailnet_hostname is not None
            and material["tailnet_hostname"] != tailnet_hostname
        ):
            raise ValueError(
                "Requested identity differs from the existing private file; use a new file path to rotate it."
            )
        return _identity_material(material)
    desired_email = email or DEFAULT_EMAIL
    desired_hostname = tailnet_hostname or DEFAULT_TAILNET_HOSTNAME
    _validate_email(desired_email)
    _validate_tailnet_hostname(desired_hostname)
    bcrypt = _bcrypt()
    password = secrets.token_urlsafe(32)
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
        "ascii"
    )
    material = {
        "email": desired_email,
        "password": password,
        "password_hash": password_hash,
        "tailnet_hostname": desired_hostname,
        "user_id": str(uuid.uuid4()),
    }
    _write_private(identity_path, json.dumps(material, sort_keys=True, indent=2) + "\n")
    return _identity_material(material)


def private_patch_paths(site_dir: Path) -> tuple[Path, ...]:
    generated = Path(site_dir) / "generated"
    return tuple(
        generated / name
        for name in (
            "dex-config-map.yaml",
            "dex-passwords.yaml",
            "profile-identity.yaml",
            "kubeflow-tailnet-ingress.yaml",
        )
    )


def write_site_patches(material: IdentityMaterial, site_dir: Path) -> tuple[Path, ...]:
    site = Path(site_dir)
    generated = site / "generated"
    _private_directory(generated)
    dex_template_path = site / "patches/dex-config-template.yaml"
    ingress_template_path = site / "kubeflow-tailnet-ingress.yaml"
    if not dex_template_path.is_file() or not ingress_template_path.is_file():
        raise FileNotFoundError("The Kubeflow site overlay templates are incomplete.")
    dex_patch = yaml.safe_load(dex_template_path.read_text(encoding="utf-8"))
    config = dex_patch.get("data", {}).get("config.yaml")
    replacements = {
        "__DEX_EMAIL__": material.email,
        "__DEX_USERNAME__": material.username,
        "__DEX_USER_ID__": material.user_id,
    }
    for placeholder, replacement in replacements.items():
        if config.count(placeholder) != 1:
            raise ValueError("The Dex identity template does not match the pinned release layout.")
        config = config.replace(placeholder, replacement)
    dex_patch["data"]["config.yaml"] = config
    dex_password_patch = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "dex-passwords", "namespace": "auth"},
        "stringData": {"DEX_USER_PASSWORD": material.password_hash},
    }
    profile_patch = [
        {"op": "replace", "path": "/metadata/name", "value": material.profile_name},
        {"op": "replace", "path": "/spec/owner/name", "value": material.email},
    ]
    ingress = yaml.safe_load(ingress_template_path.read_text(encoding="utf-8"))
    ingress_text = yaml.safe_dump(ingress, sort_keys=False)
    if ingress_text.count("__TAILNET_HOSTNAME__") != 2:
        raise ValueError("The Tailscale Ingress template does not match the site contract.")
    ingress_text = ingress_text.replace("__TAILNET_HOSTNAME__", material.tailnet_hostname)
    generated_content = {
        "dex-config-map.yaml": yaml.safe_dump(dex_patch, sort_keys=False),
        "dex-passwords.yaml": yaml.safe_dump(dex_password_patch, sort_keys=False),
        "profile-identity.yaml": yaml.safe_dump(profile_patch, sort_keys=False),
        "kubeflow-tailnet-ingress.yaml": ingress_text,
    }
    for path in private_patch_paths(site):
        _write_private(path, generated_content[path.name])
    return private_patch_paths(site)


def validate_source_ref(source_ref: str) -> None:
    if source_ref != UPSTREAM_SOURCE_REF:
        raise ValueError("Kubeflow Community Distribution source must use the pinned release commit.")


def verify_release_commit() -> None:
    tag_ref = f"refs/tags/{UPSTREAM_TAG}"
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", UPSTREAM_GIT_URL, tag_ref, f"{tag_ref}^{{}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out verifying the Kubeflow release tag.") from exc
    if result.returncode != 0:
        raise RuntimeError("Unable to verify the Kubeflow release tag against its pinned commit.")
    references = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2:
            references[fields[1]] = fields[0]
    resolved_commit = references.get(f"{tag_ref}^{{}}", references.get(tag_ref))
    if resolved_commit != UPSTREAM_COMMIT:
        raise RuntimeError("Kubeflow 26.03.1 no longer resolves to the approved source commit.")


def _verify_kubectl_kustomize() -> None:
    try:
        result = subprocess.run(
            ["kubectl", "version", "--client", "-o", "yaml"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out reading the kubectl client version.") from exc
    if result.returncode != 0:
        raise RuntimeError("kubectl client version could not be read.")
    version = yaml.safe_load(result.stdout)
    if version.get("kustomizeVersion") != "v5.8.1":
        raise RuntimeError("Kubeflow rendering requires kubectl's Kustomize v5.8.1 client.")


def _load_yaml_documents(rendered: str) -> list[dict]:
    documents = [item for item in yaml.safe_load_all(rendered) if item]
    if not documents:
        raise ValueError("Kubeflow rendering produced no Kubernetes objects.")
    return documents


def _object_identity(document: dict) -> tuple[str, str, str, str]:
    metadata = document.get("metadata", {})
    identity = (
        str(document.get("apiVersion", "")),
        str(document.get("kind", "")),
        str(metadata.get("namespace", "")),
        str(metadata.get("name", "")),
    )
    if not all(identity[:2]) or not identity[3]:
        raise ValueError("A rendered Kubernetes object has incomplete identity metadata.")
    return identity


def _find_one(documents: list[dict], kind: str, name: str, namespace: str) -> dict:
    matches = [
        document
        for document in documents
        if document.get("kind") == kind
        and document.get("metadata", {}).get("name") == name
        and document.get("metadata", {}).get("namespace", "") == namespace
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one rendered {kind} named {name} in namespace {namespace}.")
    return matches[0]


def _validate_rendered(documents: list[dict], rendered: str) -> None:
    if UPSTREAM_DEFAULT_EMAIL in rendered:
        raise ValueError("The rendered distribution contains the upstream sample Dex credential.")
    identities = [_object_identity(document) for document in documents]
    if len(identities) != len(set(identities)):
        raise ValueError("The rendered distribution contains duplicate Kubernetes object identities.")
    services = [document for document in documents if document.get("kind") == "Service"]
    for service in services:
        spec = service.get("spec", {})
        if spec.get("type", "ClusterIP") in {"NodePort", "LoadBalancer"}:
            raise ValueError("The rendered distribution contains a non-private Service exposure.")
        if any("nodePort" in port for port in spec.get("ports", [])):
            raise ValueError("The rendered distribution contains an explicit NodePort.")
    gateway = _find_one(documents, "Service", "istio-ingressgateway", "istio-system")
    if gateway.get("spec", {}).get("type", "ClusterIP") != "ClusterIP":
        raise ValueError("The Kubeflow Istio ingress gateway must remain ClusterIP.")
    config_map = _find_one(documents, "ConfigMap", "dex", "auth")
    dex_config = yaml.safe_load(config_map.get("data", {}).get("config.yaml", ""))
    users = dex_config.get("staticPasswords", [])
    if len(users) != 1 or users[0].get("hashFromEnv") != "DEX_USER_PASSWORD":
        raise ValueError("The Dex identity patch does not match the pinned release layout.")
    dex_email = users[0].get("email", "")
    dex_username = users[0].get("username", "")
    profile_name = _validate_email(dex_email)[1]
    profile = _find_one(documents, "Profile", profile_name, "")
    if profile.get("spec", {}).get("owner", {}).get("name") != dex_email:
        raise ValueError("The Kubeflow Profile owner identity is missing.")
    password_secret = _find_one(documents, "Secret", "dex-passwords", "auth")
    password_hash = password_secret.get("stringData", {}).get("DEX_USER_PASSWORD", "")
    if not password_hash.startswith("$2b$12$") or len(password_hash) != 60:
        raise ValueError("The rendered Dex Secret does not contain the generated bcrypt credential.")
    if not dex_username:
        raise ValueError("The rendered Dex username is missing.")
    ingresses = [document for document in documents if document.get("kind") == "Ingress"]
    if len(ingresses) != 1:
        raise ValueError("The rendered distribution may contain only the private Tailscale Ingress.")
    ingress = _find_one(documents, "Ingress", "kubeflow-tailnet", "istio-system")
    ingress_spec = ingress.get("spec", {})
    hosts = ingress_spec.get("tls", [{}])[0].get("hosts", [])
    rules = ingress_spec.get("rules", [])
    if ingress_spec.get("ingressClassName") != "tailscale" or len(hosts) != 1:
        raise ValueError("The Kubeflow ingress must use the private Tailscale ingress class.")
    if not hosts[0] or len(rules) != 1 or rules[0].get("host") != hosts[0]:
        raise ValueError("The Kubeflow Tailscale Ingress host is incomplete or inconsistent.")
    annotations = ingress.get("metadata", {}).get("annotations", {})
    if annotations.get("tailscale.com/funnel", "false").lower() == "true":
        raise ValueError("Tailscale Funnel must not be enabled for the Kubeflow Ingress.")
    backend = rules[0].get("http", {}).get("paths", [{}])[0].get("backend", {}).get("service", {})
    if backend.get("name") != "istio-ingressgateway" or backend.get("port", {}).get("number") != 80:
        raise ValueError("The Tailscale Ingress must forward only to the Istio gateway HTTP port.")


def _build_inventory(documents: list[dict]) -> tuple[tuple[str, str, str, str], ...]:
    inventory = tuple(sorted(_object_identity(document) for document in documents))
    if len(inventory) != len(set(inventory)):
        raise ValueError("Kubernetes object inventory is not unique.")
    return inventory


def _write_output(path: Path, contents: str) -> None:
    _private_directory(path.parent)
    if path.is_symlink():
        raise ValueError("Kubeflow render output may not replace a symbolic link.")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".kubeflow-render-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _read_private_artifact(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RenderApprovalError(f"The {label} file must exist as a regular file.")
    file_stat = path.stat()
    if stat.S_IMODE(file_stat.st_mode) != 0o600 or file_stat.st_nlink != 1:
        raise RenderApprovalError(f"The {label} file must be private and have mode 0600.")
    return path.read_bytes()


def _validate_candidate_artifacts(
    manifest_path: Path,
    receipt_path: Path,
    inventory_path: Path,
) -> tuple[dict, tuple[tuple[str, str, str, str], ...], str, str]:
    manifest_bytes = _read_private_artifact(manifest_path, "rendered manifest")
    receipt_bytes = _read_private_artifact(receipt_path, "render receipt")
    inventory_bytes = _read_private_artifact(inventory_path, "object inventory")
    try:
        receipt = json.loads(receipt_bytes)
        rendered = manifest_bytes.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RenderApprovalError("A Kubeflow render artifact is malformed.") from exc
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        raise RenderApprovalError("The Kubeflow render receipt schema is unsupported.")
    try:
        validate_source_ref(receipt.get("source_ref", ""))
    except ValueError as exc:
        raise RenderApprovalError("The candidate render receipt is not pinned to the approved source.") from exc
    if receipt.get("source_tag") != UPSTREAM_TAG or receipt.get("source_commit") != UPSTREAM_COMMIT:
        raise RenderApprovalError("The candidate render receipt has unexpected upstream provenance.")
    if receipt.get("manifest") != manifest_path.name:
        raise RenderApprovalError("The candidate receipt names a different rendered manifest.")
    actual_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if receipt.get("sha256") != actual_digest:
        raise RenderApprovalError("The candidate receipt does not match the current manifest bytes.")
    documents = _load_yaml_documents(rendered)
    try:
        _validate_rendered(documents, rendered)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RenderApprovalError("The candidate manifest failed the Kubeflow safety checks.") from exc
    inventory = _build_inventory(documents)
    expected_inventory = json.dumps([list(identity) for identity in inventory], indent=2) + "\n"
    if inventory_bytes != expected_inventory.encode("utf-8"):
        raise RenderApprovalError("The candidate object inventory does not match the current manifest.")
    inventory_digest = hashlib.sha256(inventory_bytes).hexdigest()
    if receipt.get("inventory_sha256") != inventory_digest:
        raise RenderApprovalError("The candidate receipt does not match the object inventory.")
    if receipt.get("object_count") != len(inventory):
        raise RenderApprovalError("The candidate receipt has an unexpected object count.")
    return receipt, inventory, actual_digest, inventory_digest


def approve_render(
    expected_sha256: str,
    manifest_path: Path,
    receipt_path: Path,
    inventory_path: Path,
    approval_path: Path | None = None,
) -> dict:
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise RenderApprovalError("The explicit approval digest must be 64 lowercase hexadecimal characters.")
    manifest = Path(manifest_path).expanduser().absolute()
    receipt_file = Path(receipt_path).expanduser().absolute()
    inventory_file = Path(inventory_path).expanduser().absolute()
    approval_file = (
        Path(approval_path).expanduser().absolute()
        if approval_path is not None
        else manifest.parent / "approval.json"
    )
    receipt, inventory, actual_digest, inventory_digest = _validate_candidate_artifacts(
        manifest, receipt_file, inventory_file
    )
    if expected_sha256 != actual_digest:
        raise RenderApprovalError(
            "The explicitly approved digest does not match the candidate bytes; no approval was written."
        )
    approval = {
        "schema_version": 1,
        "source_ref": receipt["source_ref"],
        "source_tag": UPSTREAM_TAG,
        "source_commit": UPSTREAM_COMMIT,
        "sha256": actual_digest,
        "inventory_sha256": inventory_digest,
        "object_count": len(inventory),
        "manifest": manifest.name,
    }
    _write_output(approval_file, json.dumps(approval, indent=2, sort_keys=True) + "\n")
    verify_render_approval(manifest, receipt_file, approval_file, inventory_file)
    return approval


def verify_render_approval(
    manifest_path: Path,
    receipt_path: Path,
    approval_path: Path,
    inventory_path: Path,
) -> dict:
    manifest = Path(manifest_path).expanduser().absolute()
    receipt_file = Path(receipt_path).expanduser().absolute()
    approval_file = Path(approval_path).expanduser().absolute()
    inventory_file = Path(inventory_path).expanduser().absolute()
    if not approval_file.is_file():
        raise RenderApprovalError("No operator-approved Kubeflow digest exists; apply is blocked.")
    receipt, inventory, actual_digest, inventory_digest = _validate_candidate_artifacts(
        manifest, receipt_file, inventory_file
    )
    approval_bytes = _read_private_artifact(approval_file, "digest approval")
    try:
        approval = json.loads(approval_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RenderApprovalError("The Kubeflow digest approval file is malformed.") from exc
    expected = {
        "schema_version": 1,
        "source_ref": UPSTREAM_SOURCE_REF,
        "source_tag": UPSTREAM_TAG,
        "source_commit": UPSTREAM_COMMIT,
        "sha256": actual_digest,
        "inventory_sha256": inventory_digest,
        "object_count": len(inventory),
        "manifest": manifest.name,
    }
    if approval != expected:
        raise RenderApprovalError(
            "The current manifest differs from the operator-approved digest; review it and explicitly re-approve before apply."
        )
    if receipt.get("sha256") != approval.get("sha256"):
        raise RenderApprovalError("The candidate receipt differs from the operator-approved digest.")
    return approval


def render_distribution(
    source_ref: str,
    site_dir: Path,
    output: Path,
    *,
    enforce_approval: bool = True,
) -> RenderReceipt:
    validate_source_ref(source_ref)
    site = Path(site_dir)
    output_path = Path(output).expanduser().absolute()
    site_config_path = site / "kustomization.yaml"
    if not site_config_path.is_file():
        raise FileNotFoundError("Kubeflow site Kustomization does not exist.")
    site_config = yaml.safe_load(site_config_path.read_text(encoding="utf-8"))
    resources = site_config.get("resources", [])
    if resources.count(UPSTREAM_SOURCE_REF) != 1:
        raise ValueError("Kubeflow site Kustomization must contain the exact pinned upstream source.")
    for patch_path in private_patch_paths(site):
        if not patch_path.is_file() or patch_path.is_symlink():
            raise FileNotFoundError("Private Kubeflow identity patches must be prepared before rendering.")
        if stat.S_IMODE(patch_path.stat().st_mode) != 0o600:
            raise PermissionError("Generated Kubeflow identity patches must have mode 0600.")
    verify_release_commit()
    _verify_kubectl_kustomize()
    try:
        result = subprocess.run(
            ["kubectl", "kustomize", str(site)],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out rendering the pinned Kubeflow distribution.") from exc
    if result.returncode != 0:
        raise RuntimeError(f"kubectl kustomize failed with exit code {result.returncode}.")
    documents = _load_yaml_documents(result.stdout)
    _validate_rendered(documents, result.stdout)
    inventory = _build_inventory(documents)
    digest = hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()
    _private_directory(output_path.parent)
    _write_output(output_path, result.stdout)
    receipt_path = output_path.parent / "receipt.json"
    inventory_path = output_path.parent / "inventory.json"
    approval_path = output_path.parent / "approval.json"
    inventory_data = [list(identity) for identity in inventory]
    inventory_text = json.dumps(inventory_data, indent=2) + "\n"
    inventory_digest = hashlib.sha256(inventory_text.encode("utf-8")).hexdigest()
    receipt_data = {
        "schema_version": 1,
        "source_ref": source_ref,
        "source_tag": UPSTREAM_TAG,
        "source_commit": UPSTREAM_COMMIT,
        "sha256": digest,
        "inventory_sha256": inventory_digest,
        "object_count": len(inventory),
        "manifest": output_path.name,
    }
    _write_output(receipt_path, json.dumps(receipt_data, indent=2, sort_keys=True) + "\n")
    _write_output(inventory_path, inventory_text)
    approved = False
    if enforce_approval and (approval_path.exists() or approval_path.is_symlink()):
        try:
            verify_render_approval(output_path, receipt_path, approval_path, inventory_path)
        except RenderApprovalError as exc:
            raise RenderApprovalError(
                f"Candidate digest {digest} does not match the approved render. Review and re-approve explicitly with --approve-digest {digest}."
            ) from exc
        approved = True
    return RenderReceipt(
        source_ref=source_ref,
        source_tag=UPSTREAM_TAG,
        source_commit=UPSTREAM_COMMIT,
        sha256=digest,
        approved=approved,
        inventory=inventory,
        output_path=output_path,
        receipt_path=receipt_path,
        inventory_path=inventory_path,
        approval_path=approval_path,
    )


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--identity-file",
        type=Path,
        default=_repository_root() / "kubeflow/generated/identity.json",
    )
    parser.add_argument("--email")
    parser.add_argument("--tailnet-hostname")
    args = parser.parse_args()
    material = prepare_identity(
        args.identity_file,
        email=args.email,
        tailnet_hostname=args.tailnet_hostname,
    )
    patches = write_site_patches(material, OVERLAY_ROOT)
    print(
        f"Prepared private Kubeflow identity and {len(patches)} mode-0600 overlay patches."
    )


if __name__ == "__main__":
    main()
