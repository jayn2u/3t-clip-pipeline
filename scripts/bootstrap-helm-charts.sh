#!/usr/bin/env bash
set -euo pipefail

readonly EXPECTED_CONFIG_HOME="$PWD/.cache/helm/config"
readonly EXPECTED_CACHE_HOME="$PWD/.cache/helm/cache"
readonly EXPECTED_DATA_HOME="$PWD/.cache/helm/data"
readonly CHART_DIR="$PWD/.cache/helm/charts"

export HELM_CONFIG_HOME="${HELM_CONFIG_HOME:-$EXPECTED_CONFIG_HOME}"
export HELM_CACHE_HOME="${HELM_CACHE_HOME:-$EXPECTED_CACHE_HOME}"
export HELM_DATA_HOME="${HELM_DATA_HOME:-$EXPECTED_DATA_HOME}"

if [[ "$HELM_CONFIG_HOME" != "$EXPECTED_CONFIG_HOME" || "$HELM_CACHE_HOME" != "$EXPECTED_CACHE_HOME" || "$HELM_DATA_HOME" != "$EXPECTED_DATA_HOME" ]]; then
  printf 'HELM_BOOTSTRAP_INVALID task_local_home_required\n' >&2
  exit 2
fi

mkdir -p "$HELM_CONFIG_HOME" "$HELM_CACHE_HOME" "$HELM_DATA_HOME" "$CHART_DIR"
helm repo add argo https://argoproj.github.io/argo-helm --force-update
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin --force-update
helm repo update argo nvdp
helm pull argo/argo-workflows --version 1.0.19 --destination "$CHART_DIR"
helm pull oci://registry.k8s.io/nfd/charts/node-feature-discovery --version 0.18.3 --destination "$CHART_DIR"
helm pull nvdp/nvidia-device-plugin --version 0.17.1 --destination "$CHART_DIR"

sha256sum --check --strict <<EOF
11910d3586c6737df04dbb2e48c373bc88cf5673d6bb17d7d421565495897da8  $CHART_DIR/argo-workflows-1.0.19.tgz
83a327ad61545bc89b319e94f5175ea4d265c9b6ffaa2832af2a0a85389409de  $CHART_DIR/node-feature-discovery-0.18.3.tgz
542ce451ce5d4611df00959e61c5638b7deeaf65c13daf03914d4688f94ae555  $CHART_DIR/nvidia-device-plugin-0.17.1.tgz
EOF
printf 'HELM_CHARTS_READY\n'
