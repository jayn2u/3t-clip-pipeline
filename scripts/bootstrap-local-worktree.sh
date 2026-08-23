#!/usr/bin/env bash
set -Eeuo pipefail

SHARED=/mnt/data/3t-clip-pipeline
SOURCE=/mnt/data/3t-clip-pipeline/.omo/plans/next-generation-pipeline-platform.md
TASK=/mnt/data/3t-clip-pipeline/.worktrees/next-generation-pipeline-platform
DERIVED_BRANCH=next-generation-pipeline-platform
BRANCH=codex/next-generation-pipeline-platform
DEST=$TASK/.omo/plans/next-generation-pipeline-platform.md
EXCLUDE="$(git -C "$SHARED" rev-parse --absolute-git-dir)/info/exclude"

if [[ -n "${BOOTSTRAP_SHARED:-}" ]]; then
  SHARED=$BOOTSTRAP_SHARED
  SOURCE=$SHARED/.omo/plans/next-generation-pipeline-platform.md
  TASK=$SHARED/.worktrees/next-generation-pipeline-platform
  DEST=$TASK/.omo/plans/next-generation-pipeline-platform.md
  EXCLUDE="$(git -C "$SHARED" rev-parse --absolute-git-dir)/info/exclude"
fi

SNAPSHOT_DIR=
EXCLUDE_MODE=
WORKTREE_REGISTERED=0

fail_if_injected() {
  if [[ "${BOOTSTRAP_FAIL_AFTER:-}" == "$1" ]]; then
    printf 'injected failure after %s\n' "$1" >&2
    return 97
  fi
}

signal_if_injected() {
  if [[ "${BOOTSTRAP_SIGNAL_AFTER:-}" == "$1" ]]; then
    kill -s "${BOOTSTRAP_SIGNAL:-TERM}" "$$"
  fi
}

remove_snapshot() {
  if [[ -n "$SNAPSHOT_DIR" && -d "$SNAPSHOT_DIR" ]]; then
    find "$SNAPSHOT_DIR" -mindepth 1 -maxdepth 1 -type f -delete
    rmdir "$SNAPSHOT_DIR"
  fi
}

assert_rollback_complete() {
  local listing
  listing=$(git -C "$SHARED" worktree list --porcelain)
  [[ ! -e "$TASK" ]]
  ! git -C "$SHARED" show-ref --verify --quiet "refs/heads/$BRANCH"
  ! git -C "$SHARED" show-ref --verify --quiet "refs/heads/$DERIVED_BRANCH"
  [[ "$listing" != *"worktree $TASK"* ]]
  [[ "$listing" != *"branch refs/heads/$BRANCH"* ]]
  [[ "$listing" != *"branch refs/heads/$DERIVED_BRANCH"* ]]
}

rollback() {
  local original_status=$1
  local cleanup_status=0
  trap - ERR INT TERM
  set +e
  if [[ "$WORKTREE_REGISTERED" == 1 ]]; then
    local unlock_output
    unlock_output=$(git -C "$SHARED" worktree unlock "$TASK" 2>&1)
    local unlock_status=$?
    if [[ "$unlock_status" -ne 0 && "$unlock_output" != *"is not locked"* ]]; then
      printf '%s\n' "$unlock_output" >&2
      cleanup_status=1
    fi
    git -C "$SHARED" worktree remove --force "$TASK" || cleanup_status=1
  fi
  if [[ -n "$SNAPSHOT_DIR" && -f "$SNAPSHOT_DIR/exclude.before" ]]; then
    cp "$SNAPSHOT_DIR/exclude.before" "$EXCLUDE" || cleanup_status=1
    chmod "$EXCLUDE_MODE" "$EXCLUDE" || cleanup_status=1
  fi
  if [[ -d "$SHARED/.worktrees" ]]; then
    rmdir "$SHARED/.worktrees" 2>/dev/null || true
  fi
  git -C "$SHARED" worktree prune || cleanup_status=1
  assert_rollback_complete || cleanup_status=1
  remove_snapshot || cleanup_status=1
  if [[ "$cleanup_status" -ne 0 ]]; then
    printf 'bootstrap cleanup incomplete; inspect path=%s refs/heads/%s refs/heads/%s\n' \
      "$TASK" "$BRANCH" "$DERIVED_BRANCH" >&2
    exit 98
  fi
  exit "$original_status"
}

on_error() {
  local status=$?
  rollback "$status"
}

preflight() {
  [[ "$(git --version)" == "git version 2.43."* ]]
  [[ "$(git -C "$SHARED" symbolic-ref --short HEAD)" == develop ]]
  ! git -C "$SHARED" rev-parse --verify HEAD >/dev/null 2>&1
  ! git -C "$SHARED" show-ref --verify --quiet "refs/heads/$BRANCH"
  ! git -C "$SHARED" show-ref --verify --quiet "refs/heads/$DERIVED_BRANCH"
  [[ ! -e "$TASK" ]]
  local listing
  listing=$(git -C "$SHARED" worktree list --porcelain)
  [[ "$listing" != *"worktree $TASK"* ]]
  [[ "$listing" != *"branch refs/heads/$BRANCH"* ]]
  [[ "$listing" != *"branch refs/heads/$DERIVED_BRANCH"* ]]
}

copy_plan_by_descriptor() {
  python3 - "$SHARED" "$TASK" <<'PY'
from __future__ import annotations

import hashlib
import os
import stat
import sys


def open_directory(parent: int, name: str) -> int:
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        raise RuntimeError(name)
    return descriptor


shared, task = sys.argv[1:]
source_root = os.open(shared, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
source_omo = open_directory(source_root, ".omo")
source_plans = open_directory(source_omo, "plans")
source_file = os.open(
    "next-generation-pipeline-platform.md", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_plans
)
if not stat.S_ISREG(os.fstat(source_file).st_mode):
    raise RuntimeError("source is not a regular file")

task_root = os.open(task, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
task_omo = open_directory(task_root, ".omo")
task_plans = open_directory(task_omo, "plans")
destination_file = os.open(
    "next-generation-pipeline-platform.md",
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o664,
    dir_fd=task_plans,
)

source_digest = hashlib.sha256()
source_count = 0
with os.fdopen(source_file, "rb", closefd=True) as source, os.fdopen(
    destination_file, "wb", closefd=True
) as destination:
    while chunk := source.read(1024 * 1024):
        source_digest.update(chunk)
        source_count += len(chunk)
        destination.write(chunk)
    destination.flush()
    os.fsync(destination.fileno())

destination_file = os.open(
    "next-generation-pipeline-platform.md", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=task_plans
)
if not stat.S_ISREG(os.fstat(destination_file).st_mode):
    raise RuntimeError("destination is not a regular file")
destination_digest = hashlib.sha256()
destination_count = 0
with os.fdopen(destination_file, "rb", closefd=True) as destination:
    while chunk := destination.read(1024 * 1024):
        destination_digest.update(chunk)
        destination_count += len(chunk)

for descriptor in (task_plans, task_omo, task_root, source_plans, source_omo, source_root):
    os.close(descriptor)
if source_digest.digest() != destination_digest.digest() or source_count != destination_count:
    raise RuntimeError("source/destination identity mismatch")
print(f"PLAN_COPY_SHA256={source_digest.hexdigest()} PLAN_COPY_BYTES={source_count}")
PY
}

preflight
SNAPSHOT_DIR=$(mktemp -d "$SHARED/.bootstrap-local-worktree.XXXXXX")
cp "$EXCLUDE" "$SNAPSHOT_DIR/exclude.before"
EXCLUDE_MODE=$(stat -c %a "$EXCLUDE")
trap on_error ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

if ! grep -Fxq '.worktrees/' "$EXCLUDE"; then
  printf '.worktrees/\n' >>"$EXCLUDE"
fi
fail_if_injected exclude
signal_if_injected exclude

mkdir -p "$SHARED/.worktrees"
fail_if_injected directory

git -C "$SHARED" worktree add --lock --reason next-generation-pipeline-platform --orphan "$TASK"
WORKTREE_REGISTERED=1
fail_if_injected worktree

git -C "$TASK" switch --orphan "$BRANCH"
fail_if_injected switch

case "${BOOTSTRAP_TEST_MUTATION:-none}" in
  none) ;;
  destination_omo_symlink)
    mkdir "$TASK/.destination-symlink-target"
    ln -s "$TASK/.destination-symlink-target" "$TASK/.omo"
    ;;
  destination_plans_symlink)
    mkdir -p "$TASK/.omo"
    mkdir "$TASK/.destination-symlink-target"
    ln -s "$TASK/.destination-symlink-target" "$TASK/.omo/plans"
    ;;
  destination_final_symlink)
    mkdir -p "$TASK/.omo/plans"
    ln -s "$SOURCE" "$DEST"
    ;;
  *)
    printf 'unsupported test mutation\n' >&2
    false
    ;;
esac
[[ ! -L "$TASK/.omo" ]]
mkdir -p "$TASK/.omo/plans"
copy_plan_by_descriptor
fail_if_injected copy

trap - ERR INT TERM
remove_snapshot
printf 'BOOTSTRAP_OK branch=%s task=%s\n' "$BRANCH" "$TASK"
