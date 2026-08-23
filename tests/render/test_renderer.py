import re
from pathlib import Path

from three_t_clip_pipeline.contract import load_workload
from three_t_clip_pipeline.render import render_workflow_bytes

ROOT = Path(__file__).resolve().parents[2]


def test_minimal_workload_renders_frozen_dag() -> None:
    workload = load_workload(ROOT / "examples/workload-minimal.yaml")

    rendered = render_workflow_bytes(workload)

    assert rendered == render_workflow_bytes(workload)
    positions = tuple(
        rendered.index(f"name: {name}".encode())
        for name in (
            "preflight",
            "acquire-profile",
            "prepare-cache",
            "run",
        )
    )
    assert positions == tuple(sorted(positions))
    assert b"onExit: commit-results\n" in rendered


def test_minimal_workload_matches_golden_bytes() -> None:
    workload = load_workload(ROOT / "examples/workload-minimal.yaml")

    assert (
        render_workflow_bytes(workload)
        == (ROOT / "tests/golden/workflow-v1alpha1.yaml").read_bytes()
    )


def test_render_freezes_lifecycle_mounts_images_and_time_bounds() -> None:
    rendered = render_workflow_bytes(
        load_workload(ROOT / "examples/workload-minimal.yaml")
    ).decode()

    for name in ("bundle-init", "main", "artifact-publisher", "commit-results"):
        assert f"name: {name}\n" in rendered
    for mount in (
        "/workspace/input",
        "/workspace/cache",
        "/workspace/control",
        "/workspace/output",
    ):
        assert f"mountPath: {mount}\n" in rendered
    image_lines = tuple(line.strip() for line in rendered.splitlines() if "image: " in line)
    assert image_lines
    assert all(re.fullmatch(r"image: \S+@sha256:[0-9a-f]{64}", line) for line in image_lines)
    assert "restartPolicy: Always\n" in rendered
    assert "activeDeadlineSeconds: 86400\n" in rendered
    assert "terminationGracePeriodSeconds: 120" in rendered
    assert "publication.json" not in rendered
    assert "COMMITTED.json" not in rendered
    assert rendered.count("name: REQUIRED_PATHS_JSON") == 2


def test_render_contains_no_labclip_specific_tokens() -> None:
    rendered = render_workflow_bytes(
        load_workload(ROOT / "examples/workload-minimal.yaml")
    ).decode()

    forbidden = ("labclip", "/mnt/data/lab_clip", "pipeline.tests", "five-fold")
    assert all(token not in rendered.lower() for token in forbidden)


def test_render_preserves_secret_configmap_and_field_references(tmp_path: Path) -> None:
    source = (ROOT / "examples/workload-minimal.yaml").read_text()
    workload_path = tmp_path / "references.yaml"
    environment = """  environment:
    - name: ACCESS_TOKEN
      valueFrom:
        secretKeyRef:
          name: object-store
          key: access-token
    - name: ENDPOINT
      valueFrom:
        configMapKeyRef:
          name: pipeline-settings
          key: endpoint
    - name: POD_NAME
      valueFrom:
        fieldRef:
          fieldPath: metadata.name
"""
    _ = workload_path.write_text(source.replace("  resources:\n", f"{environment}  resources:\n"))

    rendered = render_workflow_bytes(load_workload(workload_path)).decode()

    assert (
        "secretKeyRef:\n            key: access-token\n            name: object-store" in rendered
    )
    assert (
        "configMapKeyRef:\n            key: endpoint\n            name: pipeline-settings"
        in rendered
    )
    assert "fieldRef:\n            fieldPath: metadata.name" in rendered
    assert "value: access-token" not in rendered
