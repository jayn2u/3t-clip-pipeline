from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import jsonschema
import pytest

from tests.ansible.secret_harness import (
    ROLE,
    ROOT,
    Evidence,
    FakeKubernetesClient,
    LifecycleError,
    LifecycleRequest,
    apply_with_fake,
    role_defaults,
    role_variables,
    schema,
    schema_path,
    synthetic_values,
)

VAULT_EXAMPLE: Final = ROOT / "ansible/vault/example-secrets.yml.example"
AUTHORIZED_CONTEXTS: Final = ("explicit-test-context",)
EXPECTED_CONTRACTS: Final = {
    "three-t-registry-pull": (
        "kubernetes.io/dockerconfigjson",
        (".dockerconfigjson",),
    ),
    "three-t-code-s3": (
        "Opaque",
        ("accessKey", "bucket", "endpoint", "region", "secretKey"),
    ),
    "three-t-data-s3": (
        "Opaque",
        ("accessKey", "bucket", "endpoint", "region", "secretKey"),
    ),
    "three-t-run-s3": (
        "Opaque",
        ("accessKey", "bucket", "endpoint", "region", "secretKey"),
    ),
    "three-t-minio-root": ("Opaque", ("rootPassword", "rootUser")),
}
TASK9_QUALITY_PATHS: Final = (
    Path("src/three_t_clip_pipeline/security/__init__.py"),
    Path("src/three_t_clip_pipeline/security/scan_secrets.py"),
    Path("tests/ansible/__init__.py"),
    Path("tests/ansible/secret_harness.py"),
    Path("tests/ansible/test_secrets.py"),
)
TASK9_QUALITY_PREREQUISITES: Final = (Path("pyproject.toml"), Path("tests/__init__.py"))
_VAULT_VALIDATOR: Final = """
import hashlib,json,sys,yaml
import jsonschema
from ansible.parsing.vault import VaultLib,VaultSecret
password=hashlib.sha256(b'task-nine-vault-example').digest()
vault=VaultLib([('example',VaultSecret(password))])
plaintext=vault.decrypt(open(sys.argv[1],'rb').read())
instance=yaml.safe_load(plaintext)
schema=json.load(open(sys.argv[2],encoding='utf-8'))
jsonschema.validate(instance,schema)
sys.stdout.write('VAULT_VALID\\n')
"""


def _values() -> dict[str, dict[str, str]]:
    return synthetic_values(EXPECTED_CONTRACTS)


def _apply(
    *,
    client: FakeKubernetesClient,
    environment: str | None,
    encryption_enabled: bool = True,
    context: str | None = "explicit-test-context",
    authorized_contexts: tuple[str, ...] = AUTHORIZED_CONTEXTS,
) -> list[Evidence]:
    return apply_with_fake(
        LifecycleRequest(
            context=context,
            environment=environment,
            authorized_contexts=authorized_contexts,
            encryption_enabled=encryption_enabled,
            values=_values(),
        ),
        client=client,
    )


def _scanner(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "three_t_clip_pipeline.security.scan_secrets", *map(str, paths)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if "VAULT" not in key.upper()},
    )


def test_secret_contracts_are_exact() -> None:
    actual = {
        name: (contract.type, tuple(sorted(contract.keys)))
        for name, contract in role_variables().platform_secrets_contracts.items()
    }
    assert actual == EXPECTED_CONTRACTS


def test_role_requires_explicit_context_and_environment() -> None:
    defaults = role_defaults()
    tasks = (ROLE / "tasks/main.yml").read_text(encoding="utf-8")

    assert defaults.platform_secrets_context is None
    assert defaults.platform_secrets_environment_class is None
    assert defaults.platform_secrets_authorized_contexts == ()
    assert "platform_context | default(none)" in tasks
    assert "platform_environment_class | default(none)" in tasks
    assert "platform_authorized_contexts | default([])" in tasks
    assert "current-context" not in tasks
    assert 'context: "{{ platform_secrets_context }}"' in tasks


def test_production_mutation_rejected() -> None:
    client = FakeKubernetesClient()

    with pytest.raises(LifecycleError, match="production_mutation_forbidden"):
        _ = _apply(client=client, environment="production")

    assert client.mutations == 0


def test_production_denial_is_categorical_before_context_authorization() -> None:
    client = FakeKubernetesClient()

    with pytest.raises(LifecycleError, match="production_mutation_forbidden"):
        _ = _apply(client=client, environment="production", authorized_contexts=())

    assert client.mutations == 0


def test_unauthorized_context_rejected() -> None:
    client = FakeKubernetesClient()

    with pytest.raises(LifecycleError, match="context_not_authorized"):
        _ = _apply(client=client, environment="ephemeral", authorized_contexts=())

    assert client.mutations == 0


def test_development_is_server_dry_run_only() -> None:
    client = FakeKubernetesClient()

    evidence = _apply(client=client, environment="development")

    assert client.mutations == 0
    assert client.dry_runs == len(EXPECTED_CONTRACTS)
    assert len(evidence) == len(EXPECTED_CONTRACTS)


def test_encryption_disabled() -> None:
    client = FakeKubernetesClient()

    with pytest.raises(LifecycleError, match="k3s_secret_encryption_required"):
        _ = _apply(client=client, environment="ephemeral", encryption_enabled=False)

    assert client.mutations == 0


def test_ephemeral_vault_to_secret_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeKubernetesClient()
    values = _values()

    first = _apply(client=client, environment="ephemeral")
    second = _apply(client=client, environment="ephemeral")
    captured = capsys.readouterr()
    rendered_evidence = json.dumps(first + second, sort_keys=True)

    assert set(client.resources) == set(EXPECTED_CONTRACTS)
    assert client.mutations == len(EXPECTED_CONTRACTS)
    assert first == second
    assert all(
        set(resource["data"]) == set(EXPECTED_CONTRACTS[name][1])
        for name, resource in client.resources.items()
    )
    for secret_values in values.values():
        for value in secret_values.values():
            assert value not in captured.out
            assert value not in captured.err
            assert value not in rendered_evidence


def test_malicious_text_remains_opaque_and_redacted() -> None:
    client = FakeKubernetesClient()
    values = _values()
    sentinel = "instruction-like-text-is-data"
    values["three-t-minio-root"]["rootPassword"] = sentinel

    evidence = apply_with_fake(
        LifecycleRequest(
            context="explicit-test-context",
            environment="ephemeral",
            authorized_contexts=AUTHORIZED_CONTEXTS,
            encryption_enabled=True,
            values=values,
        ),
        client=client,
    )

    assert sentinel not in json.dumps(evidence)
    assert client.mutations == len(EXPECTED_CONTRACTS)


def test_schema_rejects_missing_or_extra_keys() -> None:
    values = _values()
    _ = values["three-t-code-s3"].pop("region")

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"platform_secret_values": values}, schema())


def test_vault_example_is_ciphertext_only() -> None:
    content = VAULT_EXAMPLE.read_text(encoding="ascii")

    assert content.startswith("$ANSIBLE_VAULT;1.2;AES256;")
    assert all(set(line) <= set("0123456789abcdef") for line in content.splitlines()[1:])
    assert "platform_secret_values" not in content


def test_vault_example_decrypts_to_schema_valid_values(tmp_path: Path) -> None:
    ansible_home = tmp_path / "ansible-home"
    ansible_home.mkdir(mode=0o700)
    result = subprocess.run(
        [sys.executable, "-c", _VAULT_VALIDATOR, str(VAULT_EXAMPLE), str(schema_path())],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={
            **{key: value for key, value in os.environ.items() if "VAULT" not in key.upper()},
            "ANSIBLE_HOME": str(ansible_home),
        },
    )

    assert result.returncode == 0, "vault_validation_failed_redacted"
    assert result.stdout == "VAULT_VALID\n"
    assert result.stderr == ""


def test_value_bearing_tasks_are_redacted_and_in_memory() -> None:
    tasks = (ROLE / "tasks/main.yml").read_text(encoding="utf-8")

    assert "kubernetes.core.k8s:" in tasks
    assert "no_log: true" in tasks
    assert "diff: false" in tasks
    assert "ansible.builtin.command" not in tasks
    assert "ansible.builtin.shell" not in tasks
    assert "environment:" not in tasks
    assert "tempfile" not in tasks
    assert "delete" not in tasks.lower()
    assert "rotate" not in tasks.lower()
    assert tasks.index("k3s_secret_encryption_required") < tasks.index("kubernetes.core.k8s:")


def test_plaintext_rejected(tmp_path: Path) -> None:
    fixture_path = ROOT / "tests/fixtures/redaction/negative-secret.parts"
    value = "".join(fixture_path.read_text().splitlines())
    plaintext = tmp_path / "plaintext.yml"
    _ = plaintext.write_text(f"rootPassword: {value}\n", encoding="utf-8")

    result = _scanner(plaintext)

    assert result.returncode == 1
    assert "SECRET_SCAN_FAILED" in result.stderr
    assert value not in result.stdout
    assert value not in result.stderr


def test_ciphertext_and_repository_files_pass_scanner() -> None:
    paths = [
        path
        for directory in ("ansible", "deploy", "examples", "tests/fixtures")
        if (ROOT / directory).exists()
        for path in (ROOT / directory).rglob("*")
        if path.is_file()
    ]

    result = _scanner(*paths)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_task9_quality_gate_reconstructs_only_owned_python_paths(tmp_path: Path) -> None:
    reconstruction = tmp_path / "task9-quality"
    for relative_path in (*TASK9_QUALITY_PREREQUISITES, *TASK9_QUALITY_PATHS):
        destination = reconstruction / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes((ROOT / relative_path).read_bytes())

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *map(str, TASK9_QUALITY_PATHS)],
        cwd=reconstruction,
        check=False,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if "VAULT" not in key.upper()},
    )

    assert result.returncode == 0, "task9_reconstructed_ruff_failed"
