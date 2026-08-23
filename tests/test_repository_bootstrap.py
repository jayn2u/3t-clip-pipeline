"""End-to-end tests for the guarded local bootstrap surface."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPOSITORY_ROOT / "scripts/bootstrap-local-worktree.sh"


def run(*command: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=check, capture_output=True, text=True)


def create_unborn_repository(root: Path) -> tuple[Path, bytes, int]:
    _ = run("git", "init", "-b", "develop", str(root), cwd=root.parent)
    plan = root / ".omo/plans/next-generation-pipeline-platform.md"
    _ = plan.parent.mkdir(parents=True)
    _ = plan.write_bytes(b"approved plan\nwith exact bytes\x00\n")
    exclude = root / ".git/info/exclude"
    before = exclude.read_bytes()
    mode = stat.S_IMODE(exclude.stat().st_mode)
    return plan, before, mode


def invoke(root: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    process_environment = os.environ.copy()
    process_environment.update(environment, BOOTSTRAP_SHARED=str(root))
    return subprocess.run(
        ["bash", str(BOOTSTRAP)],
        cwd=root,
        env=process_environment,
        check=False,
        capture_output=True,
        text=True,
    )


def assert_pristine(root: Path, exclude_bytes: bytes, exclude_mode: int) -> None:
    target = root / ".worktrees/next-generation-pipeline-platform"
    assert not target.exists()
    assert (root / ".git/info/exclude").read_bytes() == exclude_bytes
    assert stat.S_IMODE((root / ".git/info/exclude").stat().st_mode) == exclude_mode
    listing = run("git", "worktree", "list", "--porcelain", cwd=root).stdout
    assert str(target) not in listing
    assert "codex/next-generation-pipeline-platform" not in listing
    assert (
        "next-generation-pipeline-platform"
        not in run("git", "show-ref", cwd=root, check=False).stdout
    )


def test_unborn_develop_creates_locked_orphan_and_identical_plan(tmp_path: Path) -> None:
    # Given: a clean unborn repository and an approved binary plan
    root = tmp_path / "shared"
    plan, _, _ = create_unborn_repository(root)

    # When: the real bootstrap command runs
    result = invoke(root)

    # Then: it creates the locked named worktree and preserves exact plan bytes
    assert result.returncode == 0, result.stderr
    target = root / ".worktrees/next-generation-pipeline-platform"
    assert (
        target / ".omo/plans/next-generation-pipeline-platform.md"
    ).read_bytes() == plan.read_bytes()
    assert run("git", "symbolic-ref", "--short", "HEAD", cwd=target).stdout.strip() == (
        "codex/next-generation-pipeline-platform"
    )
    listing = run("git", "worktree", "list", "--porcelain", cwd=root).stdout
    assert f"worktree {target}" in listing
    assert "locked next-generation-pipeline-platform" in listing
    assert run("git", "rev-parse", "--verify", "HEAD", cwd=root, check=False).returncode != 0


def test_existing_target_refuses_before_mutation(tmp_path: Path) -> None:
    # Given: an occupied target in an otherwise unborn repository
    root = tmp_path / "shared"
    _, exclude_bytes, exclude_mode = create_unborn_repository(root)
    target = root / ".worktrees/next-generation-pipeline-platform"
    target.mkdir(parents=True)
    marker = target / "user-owned"
    _ = marker.write_text("preserve", encoding="utf-8")

    # When: bootstrap is attempted
    result = invoke(root)

    # Then: it refuses before Git metadata or the occupied path changes
    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert (root / ".git/info/exclude").read_bytes() == exclude_bytes
    assert stat.S_IMODE((root / ".git/info/exclude").stat().st_mode) == exclude_mode
    assert (
        "codex/next-generation-pipeline-platform"
        not in run("git", "worktree", "list", "--porcelain", cwd=root).stdout
    )


def test_stale_worktree_record_refuses_before_mutation(tmp_path: Path) -> None:
    # Given: a registered task worktree whose directory was externally removed
    root = tmp_path / "shared"
    _, exclude_bytes, exclude_mode = create_unborn_repository(root)
    target = root / ".worktrees/next-generation-pipeline-platform"
    target.parent.mkdir()
    _ = run("git", "worktree", "add", "--orphan", str(target), cwd=root)
    shutil.rmtree(target)
    listing_before = run("git", "worktree", "list", "--porcelain", cwd=root).stdout

    # When: bootstrap is attempted against the stale registration
    result = invoke(root)

    # Then: it refuses before changing the record or exclude file
    assert result.returncode != 0
    assert run("git", "worktree", "list", "--porcelain", cwd=root).stdout == listing_before
    assert (root / ".git/info/exclude").read_bytes() == exclude_bytes
    assert stat.S_IMODE((root / ".git/info/exclude").stat().st_mode) == exclude_mode


@pytest.mark.parametrize(
    "occupied_branch",
    ["codex/next-generation-pipeline-platform", "next-generation-pipeline-platform"],
)
def test_existing_branch_refuses_before_mutation(tmp_path: Path, occupied_branch: str) -> None:
    # Given: a pre-existing branch ref backed by an unrelated commit
    root = tmp_path / "shared"
    _, exclude_bytes, exclude_mode = create_unborn_repository(root)
    _ = run("git", "switch", "--orphan", occupied_branch, cwd=root)
    commit = run(
        "git",
        "-c",
        "user.name=Bootstrap Test",
        "-c",
        "user.email=bootstrap@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "fixture",
        cwd=root,
    )
    assert commit.returncode == 0
    occupied_commit = run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()
    _ = run("git", "switch", "--orphan", "develop", cwd=root)

    # When: bootstrap is attempted
    result = invoke(root)

    # Then: the original ref and exclude snapshot remain untouched
    assert result.returncode != 0
    assert (
        run("git", "rev-parse", f"refs/heads/{occupied_branch}", cwd=root).stdout.strip()
        == occupied_commit
    )
    assert (root / ".git/info/exclude").read_bytes() == exclude_bytes
    assert stat.S_IMODE((root / ".git/info/exclude").stat().st_mode) == exclude_mode


@pytest.mark.parametrize("failure", ["exclude", "directory", "worktree", "switch", "copy"])
def test_injected_partial_failure_rolls_back_every_mutation(tmp_path: Path, failure: str) -> None:
    # Given: a clean unborn repository and a named partial-failure point
    root = tmp_path / "shared"
    _, exclude_bytes, exclude_mode = create_unborn_repository(root)

    # When: the bootstrap fails after that mutation
    result = invoke(root, BOOTSTRAP_FAIL_AFTER=failure)

    # Then: only pre-existing state remains
    assert result.returncode == 97, result.stderr
    assert_pristine(root, exclude_bytes, exclude_mode)


@pytest.mark.parametrize(("signal_name", "expected_status"), [("INT", 130), ("TERM", 143)])
def test_signal_interrupt_rolls_back_every_mutation(
    tmp_path: Path, signal_name: str, expected_status: int
) -> None:
    # Given: a clean unborn repository and a signal interruption after mutation
    root = tmp_path / "shared"
    _, exclude_bytes, exclude_mode = create_unborn_repository(root)

    # When: the bootstrap process receives the injected signal
    result = invoke(
        root,
        BOOTSTRAP_SIGNAL_AFTER="exclude",
        BOOTSTRAP_SIGNAL=signal_name,
    )

    # Then: its signal status is visible and the mutation is rolled back
    assert result.returncode == expected_status, result.stderr
    assert_pristine(root, exclude_bytes, exclude_mode)


@pytest.mark.parametrize("symlink_part", [".omo", "plans", "next-generation-pipeline-platform.md"])
def test_symlinked_source_chain_is_rejected_and_rolled_back(
    tmp_path: Path, symlink_part: str
) -> None:
    # Given: a source descriptor chain containing a symlink
    root = tmp_path / "shared"
    _, exclude_bytes, exclude_mode = create_unborn_repository(root)
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    if symlink_part == ".omo":
        shutil.rmtree(root / ".omo")
        (root / ".omo").symlink_to(replacement, target_is_directory=True)
    elif symlink_part == "plans":
        shutil.rmtree(root / ".omo/plans")
        (root / ".omo/plans").symlink_to(replacement, target_is_directory=True)
    else:
        source = root / ".omo/plans/next-generation-pipeline-platform.md"
        source.unlink()
        source.symlink_to(replacement / "plan")

    # When: bootstrap reaches descriptor-open copy
    result = invoke(root)

    # Then: it rejects the symlink and restores Git metadata
    assert result.returncode != 0
    assert_pristine(root, exclude_bytes, exclude_mode)


@pytest.mark.parametrize(
    "mutation",
    ["destination_omo_symlink", "destination_plans_symlink", "destination_final_symlink"],
)
def test_symlinked_destination_chain_is_rejected_and_rolled_back(
    tmp_path: Path, mutation: str
) -> None:
    # Given: a clean unborn repository and an injected destination-chain symlink
    root = tmp_path / "shared"
    _, exclude_bytes, exclude_mode = create_unborn_repository(root)

    # When: the destination ancestor is replaced before descriptor opening
    result = invoke(root, BOOTSTRAP_TEST_MUTATION=mutation)

    # Then: bootstrap refuses it and restores owned Git state
    assert result.returncode != 0
    assert_pristine(root, exclude_bytes, exclude_mode)


def test_script_uses_required_local_no_remote_algorithm() -> None:
    # Given: the checked-in bootstrap command
    source = BOOTSTRAP.read_text(encoding="utf-8")

    # When: its Git mutation surface is inspected
    forbidden = (" ls-remote", " fetch", " push", "worktree add -b", "worktree add --orphan -b")

    # Then: exact local orphan and descriptor safeguards are present
    assert (
        "SOURCE=/mnt/data/3t-clip-pipeline/.omo/plans/next-generation-pipeline-platform.md"
        in source
    )
    assert 'show-ref --verify --quiet "refs/heads/$BRANCH"' in source
    assert 'show-ref --verify --quiet "refs/heads/$DERIVED_BRANCH"' in source
    assert (
        'worktree add --lock --reason next-generation-pipeline-platform --orphan "$TASK"' in source
    )
    assert 'switch --orphan "$BRANCH"' in source
    assert 'mkdir -p "$TASK/.omo/plans"' in source
    assert "os.O_NOFOLLOW" in source
    assert "os.fstat" in source
    assert "hashlib.sha256" in source
    assert all(token not in source for token in forbidden)
