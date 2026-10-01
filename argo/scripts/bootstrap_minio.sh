#!/usr/bin/env bash
set -euo pipefail

stack_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${KUBECONFIG:-}" ]]; then
  KUBECONFIG="${stack_root}/../ansible/generated/kubeconfig"
  export KUBECONFIG
fi

if [[ ! -r "${KUBECONFIG}" ]]; then
  printf 'Kubeconfig is not readable: %s\n' "${KUBECONFIG}" >&2
  exit 1
fi

bootstrap_python="${LABCLIP_PYTHON:-python3}"

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
  "${bootstrap_python}" "${stack_root}/scripts/minio_bootstrap.py" \
    --store "${store}" \
    --namespace argo \
    "${mc_args[@]}"
done
