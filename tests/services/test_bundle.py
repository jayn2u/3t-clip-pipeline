from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import pytest

from three_t_clip_pipeline.services.bundle import create_bundle


def test_bundle_excludes_private_and_platform_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _ = (source / "train.py").write_text("print('ok')\n", encoding="utf-8")
    for name in (".git", "artifacts", "results", ".cache", ".credentials", ".platform"):
        path = source / name
        path.mkdir()
        _ = (path / "private").write_text("secret", encoding="utf-8")
    (source / "escape").symlink_to(tmp_path / "outside")

    bundle = create_bundle(source, tmp_path / "bundle.tar.gz")

    assert bundle.sha256 == hashlib.sha256(bundle.path.read_bytes()).hexdigest()
    assert bundle.size == bundle.path.stat().st_size
    with tarfile.open(bundle.path, "r:gz") as archive:
        assert archive.getnames() == ["train.py"]


def test_bundle_rejects_output_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(ValueError, match="outside source"):
        _ = create_bundle(source, source / "bundle.tar.gz")
