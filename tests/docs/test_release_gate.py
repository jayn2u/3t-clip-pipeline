from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.docs import release_fixtures

AUTHORIZATION = release_fixtures.AUTHORIZATION
MAKEFILE = release_fixtures.MAKEFILE
REQUIREMENTS = release_fixtures.REQUIREMENTS
ROOT = release_fixtures.ROOT
_authorization = release_fixtures.authorization
_commit = release_fixtures.commit
_environment = release_fixtures.environment
_git = release_fixtures.git
_prepare_make_fixture = release_fixtures.prepare_make_fixture
_repository = release_fixtures.repository
_run_release = release_fixtures.run_release
_write_executable = release_fixtures.write_executable


def test_local_mode_has_zero_network_calls(tmp_path: Path) -> None:
    # Given: local tools and a network-command spy.
    environment, network_log = _environment(tmp_path)
    bin_path = Path(environment["PATH"].split(":", maxsplit=1)[0])
    _write_executable(bin_path / "git", f"printf called >> '{network_log}'; exit 99")
    _prepare_make_fixture(tmp_path)

    # When: the local handoff runs.
    result = subprocess.run(
        ["make", "release-handoff", "DELIVERY_MODE=local"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: it completes with the exact marker and never invokes a network-capable tool.
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines().count("LOCAL_COMPLETE_REMOTE_DEFERRED") == 1
    assert not network_log.exists()


def test_local_mode_missing_cache_fails_without_implicit_install(tmp_path: Path) -> None:
    # Given: exact inputs but no task-local collection cache and command spies.
    environment, network_log = _environment(tmp_path)
    bin_path = Path(environment["PATH"].split(":", maxsplit=1)[0])
    uv_log = tmp_path / "uv.log"
    _write_executable(bin_path / "uv", f"printf '%s\\n' \"$*\" >> '{uv_log}'; exit 0")
    _write_executable(bin_path / "git", f"printf called >> '{network_log}'; exit 99")
    _ = shutil.copy2(MAKEFILE, tmp_path / "Makefile")
    requirements = tmp_path / "ansible/requirements.yml"
    requirements.parent.mkdir(parents=True)
    _ = shutil.copy2(REQUIREMENTS, requirements)

    # When: offline local delivery starts from the cold cache.
    result = subprocess.run(
        ["make", "release-handoff", "DELIVERY_MODE=local"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: it reports stable remediation before any install or network-capable command.
    assert result.returncode != 0
    assert (
        "ANSIBLE_CACHE_REQUIRED: run make ansible-sync before offline release handoff"
        in result.stderr
    )
    assert "ansible-galaxy" not in uv_log.read_text(encoding="utf-8")
    assert not network_log.exists()


@pytest.mark.parametrize(
    "case", ["missing", "malformed", "wrong-mode", "extra-field", "duplicate-key"]
)
def test_authorization_rejected_before_remote_probe(tmp_path: Path, case: str) -> None:
    # Given: an invalid external-authority state and a git spy.
    environment, network_log = _environment(tmp_path)
    bin_path = Path(environment["PATH"].split(":", maxsplit=1)[0])
    _write_executable(bin_path / "git", f"printf called >> '{network_log}'; exit 99")
    authorization = tmp_path / "authority.json"
    if case != "missing":
        if case == "malformed":
            payload = "{"
        elif case == "duplicate-key":
            payload = json.dumps(AUTHORIZATION)[:-1] + ',"allowPush":true}'
        else:
            payload = json.dumps(AUTHORIZATION | ({"extra": True} if case == "extra-field" else {}))
        _ = authorization.write_text(payload, encoding="utf-8")
        _ = authorization.chmod(0o644 if case == "wrong-mode" else 0o600)

    # When: authorized delivery is requested.
    result = _run_release(tmp_path, environment, None if case == "missing" else authorization)

    # Then: the gate fails before invoking git or gh.
    assert result.returncode != 0
    assert "AUTHORIZATION_" in result.stderr
    assert not network_log.exists()


def test_dirty_worktree_rejected_before_remote_probe(tmp_path: Path) -> None:
    # Given: exact authority beside an uncommitted task file and a git network spy.
    environment, network_log = _environment(tmp_path)
    task = tmp_path / "task"
    _ = task.mkdir()
    _ = _git(task, "init")
    _ = _git(task, "switch", "-c", "codex/next-generation-pipeline-platform")
    _prepare_make_fixture(task)
    authorization = _authorization(task)
    _ = _commit(task, "clean task")
    _ = (task / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

    # When: authorized delivery is requested from dirty state.
    result = _run_release(task, environment, authorization)

    # Then: it stops locally before ls-remote, push, or PR.
    assert result.returncode != 0
    assert "LOCAL_STATE_NOT_READY" in result.stderr
    assert not network_log.exists()


def test_invalid_delivery_mode_rejected() -> None:
    # Given: a delivery mode outside the finite interface.
    # When: the release target receives it.
    result = subprocess.run(
        ["make", "release-handoff", "DELIVERY_MODE=surprise"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: it rejects the request without claiming success.
    assert result.returncode != 0
    assert "DELIVERY_MODE_INVALID" in result.stderr
    assert "PR_READY" not in result.stdout


def test_unborn_remote_stops_only_before_push_and_pr(tmp_path: Path) -> None:
    # Given: valid authority, clean task history, and a remote with no HEAD/develop.
    environment, network_log = _environment(tmp_path)
    task = tmp_path / "task"
    _ = task.mkdir()
    _ = _git(task, "init")
    _ = _git(task, "switch", "-c", "codex/next-generation-pipeline-platform")
    _prepare_make_fixture(task)
    _ = _commit(task, "local complete")
    empty_remote = tmp_path / "empty.git"
    _ = subprocess.run(
        ["git", "init", "--bare", str(empty_remote)], check=True, capture_output=True
    )
    _ = _git(task, "remote", "add", "origin", str(empty_remote))
    authorization = _authorization(task)
    _ = _git(task, "add", "remote-delivery.json")
    _ = _git(task, "commit", "--amend", "--no-edit", "--no-verify")

    # When: authorized delivery probes the uninitialized remote.
    result = _run_release(task, environment, authorization)

    # Then: exact remediation is reported and no push or PR occurs.
    assert result.returncode != 0
    remediation = (
        "REMOTE_AUTHORITY_REQUIRED: an external owner must initialize origin/develop and remote "
        "HEAD; local task worktree and commits are complete and preserved"
    )
    assert remediation in result.stderr
    assert not network_log.exists()
    assert _git(task, "status", "--porcelain").stdout == ""


def test_external_base_uses_exact_refspec_and_rebases_orphan_root(tmp_path: Path) -> None:
    # Given: compatible orphan-root task history and initialized remote develop.
    environment, network_log = _environment(tmp_path)
    task, remote, before = _repository(tmp_path, conflict=False)
    authorization = _authorization(task)
    _ = _git(task, "add", "remote-delivery.json")
    _ = _git(task, "commit", "--amend", "--no-edit", "--no-verify")

    # When: the fully authorized delivery runs.
    result = _run_release(task, environment, authorization)

    # Then: history is rewritten onto develop and only the named task branch is pushed.
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines().count("PR_READY") == 1
    rewritten = _git(task, "rev-parse", "HEAD").stdout.strip()
    assert rewritten != before
    assert (
        _git(task, "merge-base", "--is-ancestor", "refs/remotes/origin/develop", "HEAD").returncode
        == 0
    )
    refs = _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads").stdout.splitlines()
    assert refs == ["refs/heads/codex/next-generation-pipeline-platform", "refs/heads/develop"]
    assert "--base develop --head codex/next-generation-pipeline-platform" in network_log.read_text(
        encoding="utf-8"
    )
    assert "+refs/heads/develop:refs/remotes/origin/develop" in MAKEFILE.read_text(encoding="utf-8")


def test_failed_pr_creation_never_reports_pr_ready(tmp_path: Path) -> None:
    # Given: an otherwise valid delivery whose PR client fails.
    environment, network_log = _environment(tmp_path)
    bin_path = Path(environment["PATH"].split(":", maxsplit=1)[0])
    _write_executable(bin_path / "gh", f"printf '%s\\n' \"$*\" >> '{network_log}'; exit 1")
    task, _remote, _before = _repository(tmp_path, conflict=False)
    authorization = _authorization(task)
    _ = _git(task, "add", "remote-delivery.json")
    _ = _git(task, "commit", "--amend", "--no-edit", "--no-verify")

    # When: the PR boundary returns failure.
    result = _run_release(task, environment, authorization)

    # Then: delivery fails and its success marker is absent.
    assert result.returncode != 0
    assert "PR_READY" not in result.stdout
    assert network_log.exists()


def test_external_base_conflict_aborts_and_restores(tmp_path: Path) -> None:
    # Given: orphan-root task history that conflicts with remote develop.
    environment, network_log = _environment(tmp_path)
    task, remote, _before_commit = _repository(tmp_path, conflict=True)
    authorization = _authorization(task)
    _ = _git(task, "add", "remote-delivery.json")
    before = _commit(task, "authority fixture")

    # When: authorized delivery attempts the root rebase.
    result = _run_release(task, environment, authorization)

    # Then: rebase is aborted, the exact SHA is restored, and nothing is pushed/opened.
    assert result.returncode != 0
    assert "REMOTE_BASE_CONFLICT" in result.stderr
    assert _git(task, "rev-parse", "HEAD").stdout.strip() == before
    refs = _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads").stdout.splitlines()
    assert refs == ["refs/heads/develop"]
    assert not network_log.exists()
