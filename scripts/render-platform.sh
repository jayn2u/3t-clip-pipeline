#!/usr/bin/env bash
set -euo pipefail

readonly EXPECTED_CONFIG_HOME="$PWD/.cache/helm/config"
readonly EXPECTED_CACHE_HOME="$PWD/.cache/helm/cache"
readonly EXPECTED_DATA_HOME="$PWD/.cache/helm/data"
readonly CHART_DIR="$PWD/.cache/helm/charts"
readonly PYTHON="$PWD/.venv/bin/python"
readonly PUBLISHER="$PWD/scripts/publish-platform-artifact.py"

overlay="example"
output=""
inventory=""
validate_only=""
work_dir=""
output_stage=""
inventory_stage=""
publication_complete=false
cleanup() {
  if [[ "$publication_complete" != true ]]; then
    [[ -z "$output" ]] || rm -f -- "$output"
    rm -f -- "$inventory"
  fi
  [[ -z "$output_stage" ]] || rm -f -- "$output_stage"
  [[ -z "$inventory_stage" ]] || rm -f -- "$inventory_stage"
  [[ -z "$work_dir" ]] || rm -rf -- "$work_dir"
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --overlay) overlay="${2:?missing overlay}"; shift 2 ;;
    --output) output="${2:?missing output}"; shift 2 ;;
    --inventory) inventory="${2:?missing inventory}"; shift 2 ;;
    --validate-only) validate_only="${2:?missing manifest}"; shift 2 ;;
    *) printf 'PLATFORM_RENDER_INVALID unknown_argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

[[ -n "$inventory" ]] || { printf 'PLATFORM_RENDER_INVALID inventory_required\n' >&2; exit 2; }

if [[ -n "$validate_only" ]]; then
  rm -f -- "$inventory"
  mkdir -p "$(dirname "$inventory")"
  inventory_stage="$(mktemp --tmpdir="$(dirname "$inventory")" ".$(basename "$inventory").tmp.XXXXXX")"
  "$PYTHON" scripts/platform_manifest.py \
    --source "kustomize:$validate_only" \
    --inventory "$inventory_stage" \
    --ledger config/resource-ownership.yaml
  "$PYTHON" "$PUBLISHER" "$inventory_stage" "$inventory"
  publication_complete=true
  printf 'PLATFORM_POLICY_VALID\n'
  exit 0
fi

[[ "$overlay" == "example" ]] || { printf 'PLATFORM_RENDER_INVALID unknown_overlay: %s\n' "$overlay" >&2; exit 2; }
[[ -n "$output" ]] || { printf 'PLATFORM_RENDER_INVALID output_required\n' >&2; exit 2; }
[[ "$output" != "$inventory" ]] || { printf 'PLATFORM_RENDER_INVALID distinct_outputs_required\n' >&2; exit 2; }
rm -f -- "$output" "$inventory"
mkdir -p "$(dirname "$output")" "$(dirname "$inventory")"
output_stage="$(mktemp --tmpdir="$(dirname "$output")" ".$(basename "$output").tmp.XXXXXX.yaml")"
inventory_stage="$(mktemp --tmpdir="$(dirname "$inventory")" ".$(basename "$inventory").tmp.XXXXXX")"

export HELM_CONFIG_HOME="${HELM_CONFIG_HOME:-$EXPECTED_CONFIG_HOME}"
export HELM_CACHE_HOME="${HELM_CACHE_HOME:-$EXPECTED_CACHE_HOME}"
export HELM_DATA_HOME="${HELM_DATA_HOME:-$EXPECTED_DATA_HOME}"
export KUBECONFIG=/dev/null
export KUBECTL_KUBERC=false
if [[ "$HELM_CONFIG_HOME" != "$EXPECTED_CONFIG_HOME" || "$HELM_CACHE_HOME" != "$EXPECTED_CACHE_HOME" || "$HELM_DATA_HOME" != "$EXPECTED_DATA_HOME" ]]; then
  printf 'PLATFORM_RENDER_INVALID task_local_helm_home_required\n' >&2
  exit 2
fi

sha256sum --check --strict <<EOF
11910d3586c6737df04dbb2e48c373bc88cf5673d6bb17d7d421565495897da8  $CHART_DIR/argo-workflows-1.0.19.tgz
83a327ad61545bc89b319e94f5175ea4d265c9b6ffaa2832af2a0a85389409de  $CHART_DIR/node-feature-discovery-0.18.3.tgz
542ce451ce5d4611df00959e61c5638b7deeaf65c13daf03914d4688f94ae555  $CHART_DIR/nvidia-device-plugin-0.17.1.tgz
EOF

work_dir="$(mktemp -d)"
helm template argo-workflows "$CHART_DIR/argo-workflows-1.0.19.tgz" --kubeconfig "$KUBECONFIG" --namespace argo --include-crds -f deploy/helm/argo-workflows-values.yaml >"$work_dir/argo.yaml"
helm template nfd "$CHART_DIR/node-feature-discovery-0.18.3.tgz" --kubeconfig "$KUBECONFIG" --namespace node-feature-discovery -f deploy/helm/nfd-values.yaml >"$work_dir/nfd.yaml"
helm template nvidia-device-plugin "$CHART_DIR/nvidia-device-plugin-0.17.1.tgz" --kubeconfig "$KUBECONFIG" --namespace kube-system -f deploy/helm/nvidia-device-plugin-values.yaml >"$work_dir/nvdp.yaml"
kubectl --kubeconfig="$KUBECONFIG" kustomize "deploy/kustomize/overlays/$overlay" >"$work_dir/kustomize.yaml"

"$PYTHON" scripts/platform_manifest.py \
  --source "helm-argo:$work_dir/argo.yaml" \
  --source "helm-gpu:$work_dir/nfd.yaml" \
  --source "helm-gpu:$work_dir/nvdp.yaml" \
  --source "kustomize:$work_dir/kustomize.yaml" \
  --output "$output_stage" \
  --inventory "$inventory_stage" \
  --ledger config/resource-ownership.yaml

./scripts/validate-manifest-local.sh "$output_stage"
resource_count="$("$PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1], encoding="utf-8"))))' "$inventory_stage")"
"$PYTHON" "$PUBLISHER" "$inventory_stage" "$inventory"
"$PYTHON" "$PUBLISHER" "$output_stage" "$output"
publication_complete=true
printf 'PLATFORM_RENDER_VALID resources=%s\n' "$resource_count"
