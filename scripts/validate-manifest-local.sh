#!/usr/bin/env bash
set -euo pipefail

readonly MANIFEST="${1:?usage: validate-manifest-local.sh MANIFEST}"
readonly VALIDATOR="$PWD/.cache/tools/kubeconform-v0.7.0"
readonly BINARY_SHA256=dd5273bdbf08531bf230f4eba8359984b7beec25a679424ff90911a2591b8b0d

if [[ ! -f "$VALIDATOR" ]]; then
  printf 'CLIENT_INVALID validator_missing: %s\n' "$VALIDATOR" >&2
  exit 2
fi
if [[ "$(sha256sum "$VALIDATOR" | cut -d' ' -f1)" != "$BINARY_SHA256" ]]; then
  printf 'CLIENT_INVALID validator_hash_drift: %s\n' "$VALIDATOR" >&2
  exit 2
fi

"$PWD/.cache/tools/kubeconform-v0.7.0" -strict -summary -exit-on-error -ignore-missing-schemas=false -kubernetes-version 1.36.2 -schema-location "$PWD/schemas/kubernetes/v1.36.2-standalone-strict/{{.ResourceKind}}{{.KindSuffix}}.json" -schema-location "$PWD/schemas/argo/v4.0.7/{{.ResourceKind}}-{{.ResourceAPIVersion}}.json" "$MANIFEST"
printf 'CLIENT_VALID\n'
