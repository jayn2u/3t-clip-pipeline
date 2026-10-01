#!/usr/bin/env python3
"""Prepare private Terraform inputs without displaying credential values."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "terraform" / "terraform.generated.auto.tfvars.json"
DEFAULT_DOCKER_CONFIG = Path.home() / ".docker" / "config.json"
DEFAULT_WANDB_ENV = Path(os.environ.get("LABCLIP_ENV_FILE", "/mnt/data/lab_clip/env/.env"))
MINIO_STORES = ("code", "ml-assets")
MINIO_CREDENTIAL_FIELDS = (
    "access_key",
    "secret_key",
    "researcher_access_key",
    "researcher_secret_key",
)
WANDB_FIELDS = {
    "wandb_api_key": "WANDB_API_KEY",
    "wandb_entity": "WANDB_ENTITY",
    "wandb_project": "WANDB_PROJECT",
}


def _is_ghcr_registry(registry: str) -> bool:
    candidate = registry.strip()
    if not candidate:
        return False
    parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    return parsed.hostname == "ghcr.io" and parsed.path.strip("/") in {"", "v1"}


def _credential_helper(helper_name: str) -> dict[str, str]:
    executable = shutil.which(f"docker-credential-{helper_name}")
    if not executable:
        raise RuntimeError(f"Docker credential helper is unavailable: {helper_name}")
    result = subprocess.run(
        [executable, "get"],
        input="ghcr.io\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Docker credential helper failed with exit code {result.returncode}")
    try:
        credentials = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Docker credential helper returned invalid JSON") from exc
    username = credentials.get("Username")
    password = credentials.get("Secret")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        raise RuntimeError("Docker credential helper returned incomplete GHCR credentials")
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"auth": encoded}


def _new_secret_key(token_bytes: int) -> str:
    """Keep the first character alphanumeric for command-line consumers."""
    return f"x{secrets.token_urlsafe(token_bytes - 1)}"


def _ghcr_auth_entry(config: dict[str, Any]) -> dict[str, str]:
    helpers = config.get("credHelpers", {})
    if not isinstance(helpers, dict):
        raise RuntimeError("Docker credential helper configuration is invalid")
    for registry, helper_name in helpers.items():
        if _is_ghcr_registry(registry):
            if not isinstance(helper_name, str) or not helper_name:
                raise RuntimeError("Docker GHCR credential helper name is invalid")
            return _credential_helper(helper_name)

    auths = config.get("auths", {})
    if not isinstance(auths, dict):
        raise RuntimeError("Docker auth configuration is invalid")
    ghcr_entries = [
        (registry, value)
        for registry, value in auths.items()
        if _is_ghcr_registry(registry)
    ]
    ghcr_entries.sort(key=lambda item: (item[0] != "ghcr.io", item[0]))
    for _, entry in ghcr_entries:
        if not isinstance(entry, dict):
            continue
        auth = entry.get("auth")
        if isinstance(auth, str) and auth:
            return {"auth": auth}
        identity_token = entry.get("identitytoken")
        if isinstance(identity_token, str) and identity_token:
            return {"identitytoken": identity_token}
        username = entry.get("username")
        password = entry.get("password")
        if isinstance(username, str) and username and isinstance(password, str) and password:
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            return {"auth": encoded}

    store = config.get("credsStore")
    if isinstance(store, str) and store:
        return _credential_helper(store)
    raise RuntimeError("No ghcr.io authentication was found in the Docker configuration")


def ghcr_dockerconfigjson(path: Path) -> str:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Docker configuration is invalid JSON") from exc
    if not isinstance(config, dict):
        raise RuntimeError("Docker configuration must be a JSON object")
    auth = _ghcr_auth_entry(config)
    return json.dumps({"auths": {"ghcr.io": auth}}, separators=(",", ":"), sort_keys=True)


def _dotenv_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        try:
            parsed = shlex.split(value, posix=True)
        except ValueError as exc:
            raise RuntimeError("W&B environment file contains an invalid quoted value") from exc
        if len(parsed) != 1:
            raise RuntimeError("W&B environment file contains an invalid quoted value")
        return parsed[0]
    return value.split(" #", 1)[0].strip()


def read_wandb_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        name, raw_value = stripped.split("=", 1)
        name = name.strip()
        if name in WANDB_FIELDS.values():
            values[name] = _dotenv_value(raw_value)
    missing = sorted(source for source in WANDB_FIELDS.values() if not values.get(source))
    if missing:
        raise RuntimeError(f"W&B environment is missing required names: {', '.join(missing)}")
    return {target: values[source] for target, source in WANDB_FIELDS.items()}


def stable_minio_credentials(previous: Any = None) -> dict[str, dict[str, str]]:
    if previous is None:
        return {
            store: {
                "access_key": secrets.token_hex(10),
                "secret_key": _new_secret_key(36),
                "researcher_access_key": secrets.token_hex(10),
                "researcher_secret_key": _new_secret_key(24),
            }
            for store in MINIO_STORES
        }
    if not isinstance(previous, dict) or set(previous) != set(MINIO_STORES):
        raise RuntimeError("Existing MinIO credential input is incomplete; refusing to rotate identities")
    normalized: dict[str, dict[str, str]] = {}
    for store in MINIO_STORES:
        values = previous[store]
        if not isinstance(values, dict) or any(
            not isinstance(values.get(name), str) or not values[name]
            for name in MINIO_CREDENTIAL_FIELDS
        ):
            raise RuntimeError("Existing MinIO credential input is incomplete; refusing to rotate identities")
        if len(values["access_key"]) < 16 or len(values["secret_key"]) < 16:
            raise RuntimeError("Existing pipeline credentials are too short; refusing to rotate identities")
        if len(values["researcher_access_key"]) > 40 or len(values["researcher_secret_key"]) > 40:
            raise RuntimeError("Existing MinIO researcher credentials exceed the supported length")
        normalized[store] = {name: values[name] for name in MINIO_CREDENTIAL_FIELDS}
    return normalized


def stable_root_credentials(
    existing: dict[str, Any],
    minio_credentials: dict[str, dict[str, str]],
    *,
    generate: bool,
) -> dict[str, str]:
    field_names = ("minio_root_user", "minio_root_password")
    present = any(name in existing for name in field_names)
    user = existing.get("minio_root_user")
    password = existing.get("minio_root_password")

    def valid(candidate_user: Any, candidate_password: Any) -> bool:
        if (
            not isinstance(candidate_user, str)
            or not isinstance(candidate_password, str)
            or len(candidate_user) < 16
            or len(candidate_password) < 16
            or candidate_user == candidate_password
            or "minioadmin" in candidate_user
            or "minioadmin" in candidate_password
        ):
            return False
        return all(
            candidate_user != credentials["access_key"]
            and candidate_password != credentials["secret_key"]
            for credentials in minio_credentials.values()
        )

    if valid(user, password):
        return {"minio_root_user": user, "minio_root_password": password}
    if present and not generate:
        raise RuntimeError(
            "Existing generated MinIO root credentials are invalid; "
            "pass --generate-root-credentials to replace them"
        )
    if not generate:
        return {}

    while True:
        candidate_user = secrets.token_hex(10)
        candidate_password = _new_secret_key(36)
        if valid(candidate_user, candidate_password):
            return {
                "minio_root_user": candidate_user,
                "minio_root_password": candidate_password,
            }


def _repair_leading_dash_secrets(payload: dict[str, Any]) -> None:
    for credentials in payload["minio_credentials"].values():
        if credentials["secret_key"].startswith("-"):
            credentials["secret_key"] = _new_secret_key(36)
        if credentials["researcher_secret_key"].startswith("-"):
            credentials["researcher_secret_key"] = _new_secret_key(24)
    root_password = payload.get("minio_root_password")
    if isinstance(root_password, str) and root_password.startswith("-"):
        payload["minio_root_password"] = _new_secret_key(36)


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary_name = temporary.name
            os.fchmod(temporary.fileno(), 0o600)
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def prepare_inputs(
    output_path: Path,
    docker_config_path: Path,
    wandb_env_path: Path,
    *,
    generate_root_credentials: bool = False,
    repair_leading_dash_secrets: bool = False,
) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Existing generated Terraform input is invalid JSON; refusing to replace it") from exc
        if not isinstance(existing, dict):
            raise RuntimeError("Existing generated Terraform input must be a JSON object")

    result = dict(existing)
    result["kubeconfig_path"] = str(ROOT.parent.parent / "ansible" / "generated" / "kubeconfig")
    result["minio_credentials"] = stable_minio_credentials(existing.get("minio_credentials"))
    result.update(
        stable_root_credentials(
            existing,
            result["minio_credentials"],
            generate=generate_root_credentials,
        )
    )
    if repair_leading_dash_secrets:
        _repair_leading_dash_secrets(result)
    result["ghcr_dockerconfigjson"] = ghcr_dockerconfigjson(docker_config_path)
    result.update(read_wandb_environment(wandb_env_path))
    _write_private_json(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare private Terraform variables for the LabCLIP cluster"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--docker-config", type=Path, default=DEFAULT_DOCKER_CONFIG)
    parser.add_argument("--wandb-env", type=Path, default=DEFAULT_WANDB_ENV)
    parser.add_argument(
        "--generate-root-credentials",
        action="store_true",
        help="Generate stable MinIO root credentials in the private auto tfvars file",
    )
    parser.add_argument(
        "--repair-leading-dash-secrets",
        action="store_true",
        help="Replace only generated MinIO secret values that begin with a dash",
    )
    args = parser.parse_args()
    prepare_inputs(
        args.output,
        args.docker_config,
        args.wandb_env,
        generate_root_credentials=args.generate_root_credentials,
        repair_leading_dash_secrets=args.repair_leading_dash_secrets,
    )
    print(f"Prepared private Terraform input at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
