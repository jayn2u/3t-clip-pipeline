import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
RENDERER = ROOT / "scripts/render-platform.sh"
CHARTS = ROOT / ".cache/helm/charts"


def _render_environment(home: Path) -> dict[str, str]:
    environment = {
        **os.environ,
        "HOME": str(home),
        "HELM_CONFIG_HOME": str(ROOT / ".cache/helm/config"),
        "HELM_CACHE_HOME": str(ROOT / ".cache/helm/cache"),
        "HELM_DATA_HOME": str(ROOT / ".cache/helm/data"),
    }
    _ = environment.pop("KUBECONFIG", None)
    _ = environment.pop("KUBECTL_KUBERC", None)
    return environment


@pytest.mark.skipif(shutil.which("strace") is None, reason="strace unavailable")
def test_cached_render_does_not_open_home_client_config(tmp_path: Path) -> None:
    # Given: readable HOME kubeconfig and kuberc sentinels plus cached charts.
    home = tmp_path / "home"
    kube = home / ".kube"
    kube.mkdir(parents=True)
    config = kube / "config"
    kuberc = kube / "kuberc"
    _ = config.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    _ = kuberc.write_text(
        "apiVersion: kubectl.config.k8s.io/v1beta1\nkind: Preference\n",
        encoding="utf-8",
    )
    trace = tmp_path / "render.strace"

    # When: the real cached renderer runs under a file-open trace.
    result = subprocess.run(
        [
            "strace",
            "-f",
            "-e",
            "trace=openat",
            "-o",
            str(trace),
            str(RENDERER),
            "--overlay",
            "example",
            "--output",
            str(tmp_path / "platform.yaml"),
            "--inventory",
            str(tmp_path / "resources.json"),
        ],
        cwd=ROOT,
        env=_render_environment(home),
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: rendering succeeds without opening either HOME client-config file.
    assert result.returncode == 0
    opened = trace.read_text(encoding="utf-8")
    assert str(config) not in opened
    assert str(kuberc) not in opened


@pytest.mark.skipif(shutil.which("strace") is None, reason="strace unavailable")
def test_success_publishes_each_artifact_by_atomic_rename(tmp_path: Path) -> None:
    # Given: final artifacts on the workspace filesystem and an explicit rename trace.
    with tempfile.TemporaryDirectory(prefix=".task8-atomic-", dir=ROOT) as directory:
        destination = Path(directory)
        output = destination / "platform.yaml"
        inventory = destination / "resources.json"
        trace = tmp_path / "rename.strace"

        # When: the real renderer publishes both validated artifacts.
        result = subprocess.run(
            [
                "strace",
                "-f",
                "-e",
                "trace=rename,renameat,renameat2",
                "-o",
                str(trace),
                str(RENDERER),
                "--overlay",
                "example",
                "--output",
                str(output),
                "--inventory",
                str(inventory),
            ],
            cwd=ROOT,
            env=_render_environment(tmp_path / "home"),
            check=False,
            capture_output=True,
            text=True,
        )

        # Then: each final path is reached by a successful rename without fallback.
        assert result.returncode == 0
        assert "Summary: 52 resources found in 1 file" in result.stdout
        assert "PLATFORM_RENDER_VALID resources=52" in result.stdout
        calls = trace.read_text(encoding="utf-8").splitlines()
        assert "EXDEV" not in "\n".join(calls)
        for final_path in (output, inventory):
            matching = [line for line in calls if str(final_path) in line]
            assert matching
            assert any(line.endswith("= 0") for line in matching)
        assert output.exists()
        assert inventory.exists()
        assert not list(destination.glob(".*.tmp.*"))


def test_checksum_failure_removes_stale_outputs(tmp_path: Path) -> None:
    # Given: stale requested artifacts and a checksum-drifted local archive.
    output = tmp_path / "platform.yaml"
    inventory = tmp_path / "resources.json"
    _ = output.write_text("stale manifest\n", encoding="utf-8")
    _ = inventory.write_text("stale inventory\n", encoding="utf-8")
    archive = CHARTS / "nvidia-device-plugin-0.17.1.tgz"
    original = archive.read_bytes()

    # When: rendering stops at archive verification.
    try:
        _ = archive.write_bytes(original + b"drift")
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
            cwd=ROOT,
            env=_render_environment(tmp_path / "home"),
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        _ = archive.write_bytes(original)

    # Then: failure cannot expose either stale requested artifact.
    assert result.returncode != 0
    assert not output.exists()
    assert not inventory.exists()
    assert not list(tmp_path.glob(".*.tmp.*"))


def test_policy_failure_removes_stale_inventory(tmp_path: Path) -> None:
    # Given: a stale inventory and a policy-invalid manifest.
    inventory = tmp_path / "resources.json"
    _ = inventory.write_text("stale inventory\n", encoding="utf-8")
    malformed = ROOT / "tests/deploy/fixtures/mutable-image.yaml"

    # When: validate-only policy processing fails.
    result = subprocess.run(
        [
            str(RENDERER),
            "--validate-only",
            str(malformed),
            "--inventory",
            str(inventory),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: failure removes the stale requested inventory.
    assert result.returncode == 2
    assert "image_not_immutable" in result.stderr
    assert not inventory.exists()
    assert not list(tmp_path.glob(".*.tmp.*"))


def test_malformed_destination_fails_without_publication(tmp_path: Path) -> None:
    # Given: an output parent that is a regular file rather than a directory.
    malformed_parent = tmp_path / "not-a-directory"
    _ = malformed_parent.write_text("blocked\n", encoding="utf-8")
    output = malformed_parent / "platform.yaml"
    inventory = tmp_path / "resources.json"

    # When: the real renderer attempts to prepare destination-local stages.
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
        cwd=ROOT,
        env=_render_environment(tmp_path / "home"),
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: no success or final/temp artifact is exposed.
    assert result.returncode != 0
    assert "PLATFORM_RENDER_VALID" not in result.stdout
    assert not output.exists()
    assert not inventory.exists()
    assert not list(tmp_path.glob(".*.tmp.*"))


def test_repeated_interruption_leaves_no_publication() -> None:
    # Given: three independent destination directories on the workspace filesystem.
    for attempt in range(3):
        with tempfile.TemporaryDirectory(
            prefix=f".task8-interrupt-{attempt}-",
            dir=ROOT,
        ) as directory:
            destination = Path(directory)
            output = destination / "platform.yaml"
            inventory = destination / "resources.json"

            # When: each real render is terminated before it can complete publication.
            process = subprocess.Popen(
                [
                    str(RENDERER),
                    "--overlay",
                    "example",
                    "--output",
                    str(output),
                    "--inventory",
                    str(inventory),
                ],
                cwd=ROOT,
                env=_render_environment(destination / "home"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            process.terminate()
            stdout, _ = process.communicate(timeout=10)

            # Then: no success marker, final artifact, or destination temp survives.
            assert process.returncode != 0
            assert "PLATFORM_RENDER_VALID" not in stdout
            assert not output.exists()
            assert not inventory.exists()
            assert not list(destination.glob(".*.tmp.*"))
