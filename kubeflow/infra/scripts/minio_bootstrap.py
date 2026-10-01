from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

MC_RELEASE = "RELEASE.2025-08-13T08-35-41Z"
MC_SHA256 = "01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891"
MC_URL = f"https://dl.min.io/client/mc/release/linux-amd64/mc.{MC_RELEASE}"


def _decode_secret_value(value: str) -> str | None:
    if not value:
        return ""
    try:
        return base64.b64decode(value, validate=True).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def read_kubernetes_secret(namespace: str, secret_name: str) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["kubectl", "-n", namespace, "get", "secret", secret_name, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        data = json.loads(result.stdout).get("data") or {}
    except (json.JSONDecodeError, AttributeError):
        return {}
    decoded: dict[str, str] = {}
    for key, raw_value in data.items():
        value = _decode_secret_value(str(raw_value))
        if value is None:
            return {}
        decoded[str(key)] = value
    return decoded


@dataclass(frozen=True)
class BootstrapPlan:
    store: str
    service: str
    secret_name: str
    researcher_secret_name: str
    buckets: tuple[str, ...]
    local_port: int | None


def build_bootstrap_plan(store: str) -> BootstrapPlan:
    if store == "code":
        return BootstrapPlan(
            store="code",
            service="minio-code",
            secret_name="minio-code-secret",
            researcher_secret_name="minio-code-researcher-secret",
            buckets=("lab-code",),
            local_port=19010,
        )
    if store == "ml-assets":
        return BootstrapPlan(
            store="ml-assets",
            service="minio-ml-assets",
            secret_name="minio-ml-assets-secret",
            researcher_secret_name="minio-ml-assets-researcher-secret",
            buckets=("lab-data", "lab-runs", "argo-artifacts"),
            local_port=19000,
        )
    raise ValueError(f"unsupported MinIO store: {store!r}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_mc(path: Path) -> Path:
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
        try:
            urllib.request.urlretrieve(MC_URL, temporary)
            temporary.chmod(0o755)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    digest = _sha256_file(path)
    if digest != MC_SHA256:
        raise RuntimeError(f"mc checksum mismatch: {digest}")
    version = subprocess.run(
        [str(path), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    if MC_RELEASE not in version.stdout:
        raise RuntimeError(f"unexpected mc version: {version.stdout.strip()}")
    return path


def build_policy(buckets: tuple[str, ...]) -> dict:
    resources = []
    for bucket in buckets:
        resources.extend((f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"))
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetBucketLocation",
                    "s3:ListBucket",
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                ],
                "Resource": resources,
            }
        ],
    }


def redact(text: str, secrets: tuple[str, ...]) -> str:
    redacted = str(text)
    for value in secrets:
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def _wait_for_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"port-forward did not open 127.0.0.1:{port}")


@contextmanager
def bootstrap_endpoint(
    plan: BootstrapPlan,
    secret: dict[str, str],
    namespace: str,
) -> Iterator[str]:
    if plan.local_port is None:
        endpoint = secret.get("endpoint-external", "")
        if not endpoint:
            raise RuntimeError(f"{plan.secret_name} has no endpoint-external")
        yield endpoint
        return
    process = subprocess.Popen(
        [
            "kubectl",
            "-n",
            namespace,
            "port-forward",
            f"svc/{plan.service}",
            f"{plan.local_port}:9000",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_port(plan.local_port)
        yield f"http://127.0.0.1:{plan.local_port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _validate_secret(secret: dict[str, str], plan: BootstrapPlan) -> None:
    required = ("root-user", "root-password", "access-key", "secret-key")
    missing = [key for key in required if not secret.get(key)]
    if missing:
        raise RuntimeError(f"{plan.secret_name} is missing keys: {missing}")
    values = tuple(secret[key] for key in required)
    if "minioadmin" in values:
        raise RuntimeError("default MinIO credentials are not allowed")
    if any(len(value) < 16 for value in values):
        raise RuntimeError("MinIO credentials must be at least 16 characters")
    if secret["root-user"] == secret["access-key"]:
        raise RuntimeError("root and pipeline users must be distinct")
    if secret["root-password"] == secret["secret-key"]:
        raise RuntimeError("root and pipeline secrets must be distinct")


def _validate_researcher_secret(
    researcher: dict[str, str],
    secret: dict[str, str],
    plan: BootstrapPlan,
) -> None:
    required = ("access-key", "secret-key")
    missing = [key for key in required if not researcher.get(key)]
    if missing:
        raise RuntimeError(
            f"{plan.researcher_secret_name} is missing keys: {missing}"
        )
    values = tuple(researcher[key] for key in required)
    if "minioadmin" in values:
        raise RuntimeError("default MinIO credentials are not allowed")
    if any(not 8 <= len(value) <= 40 for value in values):
        raise RuntimeError(
            "MinIO researcher credentials must be between 8 and 40 characters"
        )
    if researcher["access-key"] in (secret["root-user"], secret["access-key"]):
        raise RuntimeError("researcher and existing MinIO users must be distinct")
    if researcher["secret-key"] in (
        secret["root-password"],
        secret["secret-key"],
    ):
        raise RuntimeError("researcher and existing MinIO secrets must be distinct")


def _run_mc(
    argv: list[str],
    *,
    environment: dict[str, str],
    secrets: tuple[str, ...],
) -> None:
    result = subprocess.run(
        argv,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = redact(
            f"mc command failed: {result.stdout}\n{result.stderr}",
            secrets,
        )
        raise RuntimeError(message.strip())


def bootstrap_store(
    store: str,
    *,
    namespace: str = "argo",
    mc_path: Path | None = None,
) -> None:
    plan = build_bootstrap_plan(store)
    secret = read_kubernetes_secret(
        namespace=namespace,
        secret_name=plan.secret_name,
    )
    researcher = read_kubernetes_secret(
        namespace=namespace,
        secret_name=plan.researcher_secret_name,
    )
    _validate_secret(secret, plan)
    _validate_researcher_secret(researcher, secret, plan)
    cache_root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    resolved_mc = ensure_mc(
        mc_path
        or cache_root / "3t-clip-pipeline" / f"mc.{MC_RELEASE}"
    )
    sensitive = (
        secret["root-user"],
        secret["root-password"],
        secret["access-key"],
        secret["secret-key"],
        researcher["access-key"],
        researcher["secret-key"],
    )
    alias = f"labclip-{store}"
    policy_name = f"labclip-{store}-rw"
    with tempfile.TemporaryDirectory(prefix="labclip-mc-") as config_dir, tempfile.TemporaryDirectory(
        prefix="labclip-minio-policy-"
    ) as policy_dir, bootstrap_endpoint(plan, secret, namespace) as endpoint:
        environment = dict(os.environ)
        environment["MC_CONFIG_DIR"] = config_dir
        policy_path = Path(policy_dir) / "policy.json"
        policy_path.write_text(
            json.dumps(build_policy(plan.buckets), indent=2) + "\n",
            encoding="utf-8",
        )
        commands = [
            [
                str(resolved_mc),
                "alias",
                "set",
                alias,
                endpoint,
                secret["root-user"],
                secret["root-password"],
            ],
            *[
                [str(resolved_mc), "mb", "--ignore-existing", f"{alias}/{bucket}"]
                for bucket in plan.buckets
            ],
            [
                str(resolved_mc),
                "admin",
                "policy",
                "create",
                alias,
                policy_name,
                str(policy_path),
            ],
            [
                str(resolved_mc),
                "admin",
                "user",
                "add",
                alias,
                secret["access-key"],
                secret["secret-key"],
            ],
            [
                str(resolved_mc),
                "admin",
                "policy",
                "attach",
                alias,
                policy_name,
                "--user",
                secret["access-key"],
            ],
            [
                str(resolved_mc),
                "admin",
                "user",
                "add",
                alias,
                researcher["access-key"],
                researcher["secret-key"],
            ],
            [
                str(resolved_mc),
                "admin",
                "policy",
                "attach",
                alias,
                policy_name,
                "--user",
                researcher["access-key"],
            ],
        ]
        for command in commands:
            _run_mc(command, environment=environment, secrets=sensitive)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap MinIO stores")
    parser.add_argument("--store", choices=("code", "ml-assets"), required=True)
    parser.add_argument("--namespace", default="argo")
    parser.add_argument("--mc-path", type=Path, default=None)
    args = parser.parse_args()
    bootstrap_store(args.store, namespace=args.namespace, mc_path=args.mc_path)
    print(f"MinIO {args.store} bootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
