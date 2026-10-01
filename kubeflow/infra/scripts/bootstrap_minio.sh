#!/usr/bin/env bash
set -euo pipefail

labclip_root="${LABCLIP_ROOT:-/mnt/data/lab_clip}"
labclip_script="${labclip_root}/pipeline/scripts/minio_bootstrap.py"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${KUBECONFIG:-}" ]]; then
  KUBECONFIG="${repo_root}/../../ansible/generated/kubeconfig"
  export KUBECONFIG
fi

if [[ ! -r "${KUBECONFIG}" ]]; then
  printf 'Kubeconfig is not readable: %s\n' "${KUBECONFIG}" >&2
  exit 1
fi

if [[ -n "${LABCLIP_PYTHON:-}" ]]; then
  labclip_python="${LABCLIP_PYTHON}"
elif [[ -x "${labclip_root}/.venv/bin/python" ]]; then
  labclip_python="${labclip_root}/.venv/bin/python"
else
  labclip_python="python3"
fi

if [[ ! -f "${labclip_script}" ]]; then
  printf 'LabCLIP MinIO bootstrap CLI not found: %s\n' "${labclip_script}" >&2
  printf 'Set LABCLIP_ROOT to the pipeline checkout.\n' >&2
  exit 1
fi

mc_path="${LABCLIP_MC_PATH:-}"
if [[ -z "${mc_path}" ]]; then
  mc_path="$(command -v mc 2>/dev/null || true)"
fi
mc_args=()
if [[ -n "${mc_path}" ]]; then
  if [[ ! -f "${mc_path}" || ! -x "${mc_path}" ]]; then
    printf 'Pinned MinIO client is not an executable file: %s\n' "${mc_path}" >&2
    exit 1
  fi
  mc_args=(--mc-path "${mc_path}")
fi

for store in code ml-assets; do
  "${labclip_python}" "${labclip_script}" \
    --store "${store}" \
    --namespace argo \
    "${mc_args[@]}"
done
