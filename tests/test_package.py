"""Observable package-foundation tests."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

import three_t_clip_pipeline

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ProjectManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    requires_python: str = Field(alias="requires-python")
    dependencies: tuple[str, ...]


class PackageManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    project: ProjectManifest
    dependency_groups: dict[str, tuple[str, ...]] = Field(alias="dependency-groups")


def test_distribution_import_and_console_names_match() -> None:
    # Given: the installed standalone distribution
    # When: its metadata and import package are queried
    distribution_version = importlib.metadata.version("3t-clip-pipeline")

    # Then: the public version and console entry point agree
    assert distribution_version == three_t_clip_pipeline.__version__ == "0.1.0"
    result = subprocess.run(
        [str(Path(sys.executable).with_name("3t-pipeline")), "--help"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "3t-pipeline" in result.stdout


def test_python_range_and_dependency_groups_are_declared() -> None:
    # Given: the repository package manifest
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
        manifest = PackageManifest.model_validate(tomllib.load(stream))

    # When: its package boundary is read
    project = manifest.project
    development = manifest.dependency_groups["dev"]

    # Then: Python and required runtime/development surfaces are explicit
    assert project.requires_python == ">=3.12,<3.13"
    runtime_names = {item.split(">", 1)[0] for item in project.dependencies}
    assert {"pydantic", "pyyaml", "boto3", "botocore", "kubernetes"} <= runtime_names
    development_names = {item.split(">", 1)[0] for item in development}
    assert {
        "pytest",
        "pytest-cov",
        "coverage",
        "ruff",
        "basedpyright",
        "jsonschema",
        "ansible-core",
        "ansible-lint",
        "yamllint",
    } <= development_names


def test_package_has_no_labclip_or_training_imports() -> None:
    # Given: every Python source file in the platform package
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPOSITORY_ROOT / "src/three_t_clip_pipeline").rglob("*.py"))
    )

    # When: forbidden consumer-stack imports are searched
    forbidden = ("lab_clip", "labclip", "open_clip", "torch", "wandb", "pandas")

    # Then: the clean-room package has none
    assert all(name not in source.lower() for name in forbidden)
