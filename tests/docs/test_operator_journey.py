from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOURNEY = ROOT / "docs/operator/journey.md"
WORKLOAD = ROOT / "examples/workload-minimal.yaml"


def test_safe_offline_journey_executes_in_documented_order(tmp_path: Path) -> None:
    # Given: the documented workload and an isolated plan destination.
    executable = str(Path(sys.executable).with_name("3t-pipeline"))
    plan = tmp_path / "planned-workflow.yaml"
    commands = (
        [executable, "contract", "validate", str(WORKLOAD)],
        [executable, "plan", str(WORKLOAD), "--output", str(plan)],
        [executable, "validate", str(WORKLOAD), "--client"],
    )

    # When: an operator follows the offline commands in their published order.
    results = [
        subprocess.run(command, check=False, capture_output=True, text=True) for command in commands
    ]

    # Then: every local boundary succeeds and the deterministic plan exists.
    assert [result.returncode for result in results] == [0, 0, 0]
    assert plan.read_text(encoding="utf-8").startswith("apiVersion: argoproj.io/v1alpha1")


def test_cutover_without_authorization_rejected() -> None:
    # Given: the complete operator journey.
    document = JOURNEY.read_text(encoding="utf-8")

    # When: its live cutover boundary is inspected.
    cutover = document.split("## 6. Backup, readiness, cutover, and rollback", maxsplit=1)[1]

    # Then: it exposes no runnable live mutation and requires separate authority.
    assert "REQUIRES_SEPARATE_AUTHORIZATION" in cutover
    forbidden_runnable_commands = ("kubectl apply", "kubectl delete", "kubectl scale", "mc mirror")
    assert not any(command in cutover for command in forbidden_runnable_commands)


def test_journey_names_all_required_boundaries_and_abort_evidence() -> None:
    # Given: the operator journey is the canonical ordered document.
    document = JOURNEY.read_text(encoding="utf-8")

    # When: its machine-significant boundary labels are collected.
    required = (
        "Offline and client validation",
        "Read-only and server preflight",
        "Disposable isolated proof",
        "Bootstrap",
        "Submit, observe, and collect evidence",
        "Backup, readiness, cutover, and rollback",
        "Recovery",
        "Non-goals",
    )

    # Then: the path is complete and documents both stop and evidence behavior.
    assert all(label in document for label in required)
    assert document.count("Abort") >= 4
    assert document.count("Retain") >= 3
