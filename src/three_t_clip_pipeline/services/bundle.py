"""Deterministic, path-safe workload bundle creation."""

from __future__ import annotations

import gzip
import hashlib
import tarfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

_EXCLUDED_COMPONENTS: Final = frozenset(
    {
        ".cache",
        ".credentials",
        ".git",
        ".hg",
        ".platform",
        ".svn",
        "__pycache__",
        "artifacts",
        "cache",
        "results",
    }
)
_CREDENTIAL_NAMES: Final = frozenset(
    {".env", ".netrc", "credentials", "credentials.json", "id_ed25519", "id_rsa"}
)


@dataclass(frozen=True, slots=True)
class BundleArtifact:
    """Immutable bundle path and upload manifest identity."""

    path: Path
    size: int
    sha256: str


def _included_files(source: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for candidate in sorted(source.rglob("*")):
        relative = candidate.relative_to(source)
        if any(part in _EXCLUDED_COMPONENTS for part in relative.parts):
            continue
        if candidate.name in _CREDENTIAL_NAMES or candidate.is_symlink():
            continue
        if candidate.is_file():
            files.append(candidate)
    return tuple(files)


def create_bundle(source: Path, destination: Path) -> BundleArtifact:
    """Create a reproducible gzip tarball containing portable source files only."""
    source_root = source.resolve(strict=True)
    destination_path = destination.resolve(strict=False)
    if destination_path.is_relative_to(source_root):
        message = "bundle destination must be outside source"
        raise ValueError(message)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        destination.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        for candidate in _included_files(source_root):
            relative = candidate.relative_to(source_root).as_posix()
            info = archive.gettarinfo(str(candidate), arcname=relative)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            with candidate.open("rb") as body:
                archive.addfile(info, body)
    payload = destination.read_bytes()
    return BundleArtifact(
        path=destination,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
