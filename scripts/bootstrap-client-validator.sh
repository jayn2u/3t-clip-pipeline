#!/usr/bin/env bash
set -euo pipefail

readonly VERSION=v0.7.0
readonly ARCHIVE_SHA256=c31518ddd122663b3f3aa874cfe8178cb0988de944f29c74a0b9260920d115d3
readonly BINARY_SHA256=dd5273bdbf08531bf230f4eba8359984b7beec25a679424ff90911a2591b8b0d
readonly TARGET="$PWD/.cache/tools/kubeconform-v0.7.0"
readonly URL="https://github.com/yannh/kubeconform/releases/download/$VERSION/kubeconform-linux-amd64.tar.gz"

if [[ -f "$TARGET" ]] && [[ "$(sha256sum "$TARGET" | cut -d' ' -f1)" == "$BINARY_SHA256" ]]; then
  exit 0
fi

mkdir -p "$PWD/.cache/tools"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
curl -fsSL "$URL" -o "$scratch/kubeconform.tar.gz"
printf '%s  %s\n' "$ARCHIVE_SHA256" "$scratch/kubeconform.tar.gz" | sha256sum --check --status
tar -xzf "$scratch/kubeconform.tar.gz" -C "$scratch" kubeconform
install -m 0755 "$scratch/kubeconform" "$TARGET"
printf '%s  %s\n' "$BINARY_SHA256" "$TARGET" | sha256sum --check --status
