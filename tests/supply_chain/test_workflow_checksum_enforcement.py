import re
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest
import yaml
from pydantic import JsonValue, TypeAdapter

ROOT = Path(__file__).parents[2]
WORKFLOWS = ("ci.yml", "runtime-image.yml")
MAPPING_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
UV_DOWNLOAD_PATTERN: Final = (
    r"curl --fail --location --retry 3 \\\n+"
    r'\s+"https://github\.com/astral-sh/uv/releases/download/'
    r'[^\"]+" \\\n+'
    r"\s+--output \.cache/ci/downloads/uv\.tar\.gz"
)
UV_CHECKSUM_PATTERN: Final = (
    r"printf '%s  %s\\n' "
    r'"\$UV_TAR_SHA256" \.cache/ci/downloads/uv\.tar\.gz '
    r"\| sha256sum --check --strict"
)
UV_INSTALL_PATTERN: Final = (
    r"install -m 0755 \.cache/ci/extract/uv-x86_64-unknown-linux-gnu/uv "
    r"\.cache/ci/bin/uv"
)
BUILDX_DOWNLOAD_PATTERN: Final = (
    r"curl --fail --location --retry 3 \\\n+"
    r'\s+"https://github\.com/docker/buildx/releases/download/'
    r'[^\"]+" \\\n+'
    r"\s+--output \.cache/ci/downloads/buildx"
)
BUILDX_CHECKSUM_PATTERN: Final = (
    r"printf '%s  %s\\n' "
    r'"\$BUILDX_SHA256" \.cache/ci/downloads/buildx '
    r"\| sha256sum --check --strict"
)
BUILDX_INSTALL_PATTERN: Final = (
    r'install -m 0755 \.cache/ci/downloads/buildx "\$DOCKER_CONFIG/cli-plugins/docker-buildx"'
)


def _steps(workflow_name: str) -> list[dict[str, JsonValue]]:
    text = (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
    workflow = MAPPING_ADAPTER.validate_python(yaml.safe_load(text.replace("\non:\n", '\n"on":\n')))
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return [MAPPING_ADAPTER.validate_python(step) for step in steps]


def _field(step: dict[str, JsonValue], name: str) -> str:
    value = step.get(name, "")
    assert isinstance(value, str)
    return value


def _step_index(
    steps: list[dict[str, JsonValue]], predicate: Callable[[dict[str, JsonValue]], bool]
) -> int:
    return next(index for index, step in enumerate(steps) if predicate(step))


def _line_index(command: str, pattern: str) -> int:
    match = re.search(pattern, command, flags=re.MULTILINE)
    assert match is not None, f"missing workflow command: {pattern}"
    return match.start()


@pytest.mark.parametrize("workflow_name", WORKFLOWS)
def test_checksum_verified_local_uv_and_buildx_guard_first_execution(workflow_name: str) -> None:
    # Given: the ordered steps of one CI execution workflow.
    steps = _steps(workflow_name)

    # When: its uv and Buildx acquisition-to-use dataflows are traced.
    action_references = "\n".join(_field(step, "uses") for step in steps)
    install_index = _step_index(
        steps, lambda step: ".cache/ci/downloads/uv.tar.gz" in _field(step, "run")
    )
    install = _field(steps[install_index], "run")
    first_uv_use = _step_index(
        steps,
        lambda step: bool(re.search(r"(?m)^\s*uv\s+", _field(step, "run"))),
    )
    first_buildx_use = min(
        _step_index(
            steps,
            lambda step: "docker buildx" in _field(step, "run"),
        ),
        _step_index(
            steps,
            lambda step: "docker/build-push-action" in _field(step, "uses"),
        ),
    )

    # Then: unverified setup actions cannot execute either tool, and one task-local
    # download is checked before it becomes the binary or Docker CLI plugin later used.
    assert "astral-sh/setup-uv" not in action_references
    assert "docker/setup-buildx-action" not in action_references
    assert (
        _line_index(install, UV_DOWNLOAD_PATTERN)
        < _line_index(install, UV_CHECKSUM_PATTERN)
        < _line_index(install, UV_INSTALL_PATTERN)
    )
    assert (
        _line_index(install, BUILDX_DOWNLOAD_PATTERN)
        < _line_index(install, BUILDX_CHECKSUM_PATTERN)
        < _line_index(install, BUILDX_INSTALL_PATTERN)
    )
    assert 'printf \'%s\\n\' "$PWD/.cache/ci/bin" >> "$GITHUB_PATH"' in install
    assert install_index < first_uv_use
    assert install_index < first_buildx_use
