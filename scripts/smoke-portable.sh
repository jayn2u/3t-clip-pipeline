#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$repo_root/.venv/bin/python"
smoke_case="success"
expect_failure="0"
append_json="0"
json_path=""

while (($#)); do
  case "$1" in
    --case)
      smoke_case="$2"
      shift 2
      ;;
    --expect-failure)
      expect_failure="1"
      shift
      ;;
    --json)
      json_path="$2"
      shift 2
      ;;
    --append-json)
      json_path="$2"
      append_json="1"
      shift 2
      ;;
    *)
      echo "unsupported argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -x "$python_bin" ]]; then
  echo "locked environment missing: run uv sync --locked --all-groups" >&2
  exit 2
fi

cd "$repo_root"
SMOKE_CASE="$smoke_case" \
SMOKE_EXPECT_FAILURE="$expect_failure" \
SMOKE_APPEND="$append_json" \
SMOKE_JSON="$json_path" \
"$python_bin" -m three_t_clip_pipeline.portable_smoke
