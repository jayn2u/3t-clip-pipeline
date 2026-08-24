#!/usr/bin/env bash
set -euo pipefail

readonly REGISTRY_NAME="three-t-pipeline-registry"
readonly CLUSTER_NAME="three-t-pipeline-smoke"
readonly CONTEXT="kind-three-t-pipeline-smoke"
readonly TASK_LABEL="three-t.dev/task=14-isolated-live"
readonly REGISTRY_IMAGE="docker.io/library/registry:2.8.3@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
readonly NODE_IMAGE="docker.io/kindest/node:v1.35.0@sha256:4613778f3cfcd10e615029370f5786704559103cf27bef934597ba562b269661"
readonly KIND_VERSION="v0.31.0"
readonly KIND_SHA256="eb244cbafcc157dff60cf68693c14c9a75c4e6e6fedaf9cd71c58117cb93e3fa"
readonly ARGO_CONTROLLER_SOURCE="quay.io/argoproj/workflow-controller:v4.0.7@sha256:8e3ca93350c18348e50cdb1899f37d672f9995d9bf51412d86dee52da22fff19"
readonly ARGO_SERVER_SOURCE="quay.io/argoproj/argocli:v4.0.7@sha256:8c141b1acd26df3de70724aedaae0ee5a7d361cdf744a1daa6a1596f205cee50"
readonly ARGO_EXECUTOR_SOURCE="quay.io/argoproj/argoexec:v4.0.7@sha256:eb2a7ca4d678a0c8c4f2de44f815f02d9eb12ac4609855f897744139aef220b4"
readonly KUBECTL_SOURCE="registry.k8s.io/kubectl:v1.36.2@sha256:b0d792e0d8dfb9bb1b922b78b23137e2a34bb6f9667640353a9d2aadd1fd7761"
readonly NFD_SOURCE="registry.k8s.io/nfd/node-feature-discovery:v0.18.3@sha256:f9ef2ebee55141a1758d3c0a87bb701f5db2adf6856f7218b11bc2bac7b63862"
readonly NVIDIA_SOURCE="nvcr.io/nvidia/k8s-device-plugin:v0.17.1@sha256:af31e2b7c7f89834c4e5219860def7ac2e49a207b3d4e8610d5a26772b7738e5"
readonly MINIO_SOURCE="docker.io/minio/minio:RELEASE.2025-04-22T22-12-26Z@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e"
readonly MINIO_LOCAL="localhost:5001/minio"
readonly ARGO_CONTROLLER_LOCAL="localhost:5001/argo-controller"
readonly ARGO_SERVER_LOCAL="localhost:5001/argo-server"
readonly ARGO_EXECUTOR_LOCAL="localhost:5001/argo-executor"
readonly KUBECTL_LOCAL="localhost:5001/kubectl"
readonly NFD_LOCAL="localhost:5001/node-feature-discovery"
readonly NVIDIA_LOCAL="localhost:5001/nvidia-device-plugin"

registry=""
collect=""
create=false
build_push=false
run=false
cleanup_requested=false
registry_created=false
cluster_created=false
kind_network_only_owned=false
registry_container_id=""
registry_task_token=""
kind_network_id=""
kind_network_member_ids=""
declare -a kind_node_ids=()
task_dir=""
task_kubeconfig=""
user_kubeconfig_path=""
user_kubeconfig_fingerprint_before=""
kind_bin=""
kind_child_pid=""
kind_child_started=false
kind_creation_started_epoch=""
port_forward_pid=""
cleanup_task_dir=""
cleanup_task_kubeconfig=""
cleanup_port_forward_pid=""
cleanup_registry_container_id=""
cleanup_kind_network_id=""
declare -a cleanup_kind_node_ids=()
phase="preflight"
push_records="[]"
image_records="[]"
workflow_json="{}"
pod_json="{}"
pods_json="{}"
pvc_json="{}"
object_json="{}"
marker_json="{}"
baseline_containers=""
baseline_networks=""
baseline_captured=false
verify_pods_json=""
verify_cleanup_ownership_json=""
verify_cleanup_evidence_json=""
verification_only=false

reject() {
  printf 'ISOLATED_LIVE_REJECTED %s\n' "$1" >&2
  exit 2
}

set_phase() {
  phase=$1
  printf 'ISOLATED_LIVE_PHASE %s\n' "$phase" >&2
}

cleanup_target_is_owned() {
  local captured_id=$1
  local current_id=$2
  [[ -n "$captured_id" && "$captured_id" == "$current_id" ]]
}

cleanup_id_set_is_owned() {
  local captured_ids=$1
  local current_ids=$2
  [[ -n "$captured_ids" && "$captured_ids" == "$current_ids" ]]
}

kind_node_id_set() {
  docker ps -aq --no-trunc --filter "label=io.x-k8s.kind.cluster=$CLUSTER_NAME" | sort
}

kind_network_member_id_set() {
  docker network inspect --format '{{range $id, $_ := .Containers}}{{println $id}}{{end}}' "$1" | sed '/^$/d' | sort
}

docker_created_epoch() {
  local created_at=$1
  if [[ "$created_at" =~ ^(.*[[:space:]][+-][0-9]{4})[[:space:]][[:alpha:]]+$ ]]; then
    created_at="${BASH_REMATCH[1]}"
  fi
  LC_ALL=C date -u --date="$created_at" +%s%N 2>/dev/null
}

capture_partial_kind_resources() {
  local candidate_id=""
  local candidate_network_id=""
  local candidate_network_name=""
  local candidate_created_at=""
  local candidate_created_epoch=""
  local current_id=""
  local current_cluster=""
  local current_name=""
  local member_id=""
  local member_is_owned=false
  declare -a candidate_node_ids=()
  [[ "$kind_child_started" == true ]] || return 1
  if ((${#kind_node_ids[@]} > 0)) && [[ -n "$kind_network_id" && -n "$kind_network_member_ids" ]]; then
    return 0
  fi
  mapfile -t candidate_node_ids < <(kind_node_id_set)
  ((${#candidate_node_ids[@]} <= 1)) || return 1
  candidate_network_id="$(docker network inspect --format '{{.Id}}' kind 2>/dev/null || true)"
  [[ -n "$candidate_network_id" ]] || return 1
  candidate_network_name="$(docker network inspect --format '{{.Name}}' "$candidate_network_id" 2>/dev/null || true)"
  [[ "$candidate_network_name" == "kind" ]] || return 1
  if ! grep -Fxq "$candidate_network_id" <<<"$baseline_networks"; then
    candidate_created_at="$(docker network inspect --format '{{.Created}}' "$candidate_network_id" 2>/dev/null || true)"
    candidate_created_epoch="$(docker_created_epoch "$candidate_created_at" || true)"
    [[ -n "$candidate_created_epoch" ]] || return 1
    ((candidate_created_epoch >= kind_creation_started_epoch)) || return 1
  fi
  kind_network_member_ids="$(kind_network_member_id_set "$candidate_network_id" 2>/dev/null || true)"
  if ((${#candidate_node_ids[@]} == 0)); then
    grep -Fxq "$candidate_network_id" <<<"$baseline_networks" && return 1
    [[ -z "$kind_network_member_ids" ]] || return 1
    kind_network_id="$candidate_network_id"
    kind_network_only_owned=true
    return 0
  fi
  for candidate_id in "${candidate_node_ids[@]}"; do
    grep -Fxq "$candidate_id" <<<"$baseline_containers" && return 1
    current_id="$(docker container inspect --format '{{.Id}}' "$candidate_id" 2>/dev/null || true)"
    current_cluster="$(docker container inspect --format '{{ index .Config.Labels "io.x-k8s.kind.cluster" }}' "$candidate_id" 2>/dev/null || true)"
    current_name="$(docker container inspect --format '{{.Name}}' "$candidate_id" 2>/dev/null || true)"
    candidate_created_at="$(docker container inspect --format '{{.Created}}' "$candidate_id" 2>/dev/null || true)"
    cleanup_target_is_owned "$candidate_id" "$current_id" || return 1
    [[ "$current_cluster" == "$CLUSTER_NAME" ]] || return 1
    [[ "$current_name" == "/${CLUSTER_NAME}-control-plane" ]] || return 1
    candidate_created_epoch="$(docker_created_epoch "$candidate_created_at" || true)"
    [[ -n "$candidate_created_epoch" ]] || return 1
    ((candidate_created_epoch >= kind_creation_started_epoch)) || return 1
  done
  [[ -n "$kind_network_member_ids" ]] || return 1
  while IFS= read -r member_id; do
    member_is_owned=false
    [[ "$member_id" == "$registry_container_id" ]] && member_is_owned=true
    grep -Fxq "$member_id" <<<"$baseline_containers" && member_is_owned=true
    printf '%s\n' "${candidate_node_ids[@]}" | grep -Fxq "$member_id" && member_is_owned=true
    [[ "$member_is_owned" == true ]] || return 1
  done <<<"$kind_network_member_ids"
  kind_node_ids=("${candidate_node_ids[@]}")
  kind_network_id="$candidate_network_id"
  cluster_created=true
}

capture_partial_kind_resources_bounded() {
  local attempt=0
  for attempt in $(seq 1 20); do
    capture_partial_kind_resources && return 0
    sleep 0.1
  done
  return 1
}

stop_kind_child() {
  local attempt=0
  [[ -n "$kind_child_pid" ]] || return 0
  if kill -0 "$kind_child_pid" 2>/dev/null; then
    kill -TERM "$kind_child_pid" 2>/dev/null || true
    for attempt in $(seq 1 20); do
      kill -0 "$kind_child_pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$kind_child_pid" 2>/dev/null; then
      kill -KILL "$kind_child_pid" 2>/dev/null || true
    fi
  fi
  wait "$kind_child_pid" 2>/dev/null || true
  kind_child_pid=""
}

kubeconfig_fingerprint() {
  local kubeconfig_path=$1
  if [[ -e "$kubeconfig_path" ]]; then
    sha256sum "$kubeconfig_path" | awk '{print $1}'
  else
    printf 'absent\n'
  fi
}

port_is_occupied() {
  local port=$1
  "$PWD/.venv/bin/python" -c 'import socket,sys;s=socket.socket();s.settimeout(.2);sys.exit(0 if s.connect_ex(("127.0.0.1",int(sys.argv[1]))) == 0 else 1)' "$port"
}

captured_container_ids_absent() {
  local container_id=""
  for container_id in "$@"; do
    [[ -n "$container_id" ]] && docker container inspect "$container_id" >/dev/null 2>&1 && return 1
  done
  return 0
}

captured_process_absent() {
  [[ -z "$1" ]] || ! kill -0 "$1" 2>/dev/null
}

pod_image_pairs_json() {
  jq -ce '
    [
      .items[]
      | . as $pod
      | (($pod.status.initContainerStatuses? // []) + ($pod.status.containerStatuses? // []))[]
      | . as $status
      | [(($pod.spec.initContainers? // []) + ($pod.spec.containers? // []))[]
         | select(.name == $status.name)] as $declared
      | {
          podUid: $pod.metadata.uid,
          name: $status.name,
          declaredImage: (if ($declared | length) == 1 then $declared[0].image else null end),
          image: $status.image,
          imageID: $status.imageID
        }
    ]
  '
}

verify_pod_image_pairs() {
  local pairs_json=$1
  jq -e 'length > 0' <<<"$pairs_json" >/dev/null || reject "pod_image_statuses_missing"
  jq -e 'all(.[]; (.declaredImage | type) == "string")' <<<"$pairs_json" >/dev/null || reject "pod_image_declared_mapping_missing"
  jq -e 'all(.[]; .declaredImage | test("^localhost:5001/[a-z0-9-]+@sha256:[0-9a-f]{64}$"))' <<<"$pairs_json" >/dev/null || reject "pod_image_declared_digest_invalid"
  jq -e 'all(.[]; .imageID | (type == "string" and test("@sha256:[0-9a-f]{64}$")))' <<<"$pairs_json" >/dev/null || reject "pod_image_digest_mismatch"
  jq -e 'all(.[]; (.declaredImage | capture("@(?<digest>sha256:[0-9a-f]{64})$").digest) == (.imageID | capture("@(?<digest>sha256:[0-9a-f]{64})$").digest))' <<<"$pairs_json" >/dev/null || reject "pod_image_digest_mismatch"
}

verify_pods_json_file() {
  local pods_file=$1
  local pairs_json=""
  jq -e '.items | type == "array"' "$pods_file" >/dev/null 2>&1 || reject "pod_json_invalid"
  pairs_json="$(pod_image_pairs_json <"$pods_file")" || reject "pod_json_invalid"
  verify_pod_image_pairs "$pairs_json"
  printf 'ISOLATED_LIVE_PODS_VERIFIED comparisons=%s\n' "$(jq -r 'length' <<<"$pairs_json")"
}

verify_cleanup_ownership_json_file() {
  local ownership_file=$1
  local ownership_json=""
  local captured_registry_id=""
  local captured_network_id=""
  local current_registry_id=""
  local current_network_id=""
  local captured_node_ids=""
  local current_node_ids=""
  local captured_network_member_ids=""
  local current_network_member_ids=""
  ownership_json="$(jq -ce '{captured:{registryContainerId:.captured.registryContainerId,registryTaskToken:.captured.registryTaskToken,kindNetworkId:.captured.kindNetworkId,kindNodeIds:(.captured.kindNodeIds | sort),kindNetworkMemberIds:(.captured.kindNetworkMemberIds | sort)},current:{registryContainerId:.current.registryContainerId,registryTaskToken:.current.registryTaskToken,kindNetworkId:.current.kindNetworkId,kindNodeIds:(.current.kindNodeIds | sort),kindNetworkMemberIds:(.current.kindNetworkMemberIds | sort)}} | if ([.captured.registryContainerId,.captured.registryTaskToken,.captured.kindNetworkId,.current.registryContainerId,.current.registryTaskToken,.current.kindNetworkId] | all(.[]; type == "string" and length > 0)) and ([.captured.kindNodeIds,.captured.kindNetworkMemberIds,.current.kindNodeIds,.current.kindNetworkMemberIds] | all(.[]; type == "array" and length > 0 and all(.[]; type == "string" and length > 0))) then . else error("invalid cleanup ownership") end' "$ownership_file")" || reject "cleanup_ownership_json_invalid"
  captured_registry_id="$(jq -r '.captured.registryContainerId' <<<"$ownership_json")"
  captured_network_id="$(jq -r '.captured.kindNetworkId' <<<"$ownership_json")"
  current_registry_id="$(jq -r '.current.registryContainerId' <<<"$ownership_json")"
  current_network_id="$(jq -r '.current.kindNetworkId' <<<"$ownership_json")"
  captured_node_ids="$(jq -r '.captured.kindNodeIds | join("\\n")' <<<"$ownership_json")"
  current_node_ids="$(jq -r '.current.kindNodeIds | join("\\n")' <<<"$ownership_json")"
  captured_network_member_ids="$(jq -r '.captured.kindNetworkMemberIds | join("\\n")' <<<"$ownership_json")"
  current_network_member_ids="$(jq -r '.current.kindNetworkMemberIds | join("\\n")' <<<"$ownership_json")"
  cleanup_target_is_owned "$captured_registry_id" "$current_registry_id" && cleanup_target_is_owned "$captured_network_id" "$current_network_id" && [[ "$(jq -r '.captured.registryTaskToken' <<<"$ownership_json")" == "$(jq -r '.current.registryTaskToken' <<<"$ownership_json")" ]] && cleanup_id_set_is_owned "$captured_node_ids" "$current_node_ids" && cleanup_id_set_is_owned "$captured_network_member_ids" "$current_network_member_ids" || reject "cleanup_ownership_mismatch"
  printf 'ISOLATED_LIVE_CLEANUP_OWNERSHIP_VERIFIED\n'
}

verify_cleanup_evidence_json() {
  local cleanup_json=$1
  jq -e '[.port5001Free,.port19000Free,.registryAbsent,.registryContainerIdAbsent,.clusterAbsent,.kindNodeIdsAbsent,.kindNetworkIdAbsent,.containersUnchanged,.networksUnchanged,.tempAbsent,.pidAbsent,.kubeconfigIsolated,.userKubeconfigUnchanged] | all(. == true)' <<<"$cleanup_json" >/dev/null || reject "cleanup_evidence_incomplete"
}

verify_cleanup_evidence_json_file() {
  local evidence_file=$1
  local cleanup_json=""
  cleanup_json="$(jq -ce . "$evidence_file")" || reject "cleanup_evidence_invalid_json"
  verify_cleanup_evidence_json "$cleanup_json"
  printf 'ISOLATED_LIVE_CLEANUP_VERIFIED\n'
}

port_occupied() {
  [[ "${ISOLATED_LIVE_TEST_OCCUPIED_PORT:-}" == "1" ]] && return 0
  port_is_occupied 5001
}

cleanup_resources() {
  local status=$?
  local current_registry_id=""
  local current_network_id=""
  local current_registry_task_token=""
  local current_kind_node_ids=""
  local current_kind_network_member_ids=""
  set +e
  [[ "$verification_only" == true ]] && return "$status"
  if [[ -n "$port_forward_pid" ]]; then
    kill "$port_forward_pid" 2>/dev/null
    wait "$port_forward_pid" 2>/dev/null
    port_forward_pid=""
  fi
  if [[ "$registry_created" == true ]]; then
    current_registry_id="$(docker container inspect --format '{{.Id}}' "$registry_container_id" 2>/dev/null || true)"
    current_registry_task_token="$(docker container inspect --format '{{ index .Config.Labels "three-t.dev/task-run" }}' "$registry_container_id" 2>/dev/null || true)"
    if ! cleanup_target_is_owned "$registry_container_id" "$current_registry_id" || [[ "$registry_task_token" != "$current_registry_task_token" ]]; then
      printf 'ISOLATED_LIVE_REJECTED cleanup_ownership_mismatch\n' >&2
      return 2
    fi
  fi
  if [[ "$cluster_created" == true ]]; then
    current_kind_node_ids="$(kind_node_id_set)"
    current_network_id="$(docker network inspect --format '{{.Id}}' "$kind_network_id" 2>/dev/null || true)"
    current_kind_network_member_ids="$(kind_network_member_id_set "$kind_network_id" 2>/dev/null || true)"
    if ! cleanup_id_set_is_owned "$(printf '%s\n' "${kind_node_ids[@]}" | sort)" "$current_kind_node_ids" || ! cleanup_target_is_owned "$kind_network_id" "$current_network_id" || ! cleanup_id_set_is_owned "$kind_network_member_ids" "$current_kind_network_member_ids"; then
      printf 'ISOLATED_LIVE_REJECTED cleanup_ownership_mismatch\n' >&2
      return 2
    fi
  elif [[ "$kind_network_only_owned" == true ]]; then
    current_network_id="$(docker network inspect --format '{{.Id}}' "$kind_network_id" 2>/dev/null || true)"
    current_kind_network_member_ids="$(kind_network_member_id_set "$kind_network_id" 2>/dev/null || true)"
    if ! cleanup_target_is_owned "$kind_network_id" "$current_network_id" || [[ -n "$kind_network_member_ids" || -n "$current_kind_network_member_ids" ]]; then
      printf 'ISOLATED_LIVE_REJECTED cleanup_ownership_mismatch\n' >&2
      return 2
    fi
  fi
  if [[ "$registry_created" == true ]]; then
    docker rm -f "$registry_container_id" >/dev/null 2>&1
    registry_created=false
    registry_container_id=""
  fi
  if [[ "$cluster_created" == true ]]; then
    docker rm -f "${kind_node_ids[@]}" >/dev/null 2>&1
    cluster_created=false
  fi
  if [[ -n "$task_kubeconfig" && -e "$task_kubeconfig" ]]; then
    KUBECONFIG="$task_kubeconfig" kubectl config delete-context "$CONTEXT" >/dev/null 2>&1
    KUBECONFIG="$task_kubeconfig" kubectl config delete-cluster "$CONTEXT" >/dev/null 2>&1
    KUBECONFIG="$task_kubeconfig" kubectl config delete-user "$CONTEXT" >/dev/null 2>&1
  fi
  current_network_id="$(docker network inspect --format '{{.Id}}' "$kind_network_id" 2>/dev/null || true)"
  if cleanup_target_is_owned "$kind_network_id" "$current_network_id" && [[ "$baseline_captured" == true ]] && ! grep -Fxq "$kind_network_id" <<<"$baseline_networks"; then
    docker network rm "$kind_network_id" >/dev/null 2>&1
  fi
  kind_network_id=""
  [[ -z "$task_dir" ]] || rm -rf -- "$task_dir"
  return "$status"
}
interrupt() {
  trap '' INT TERM
  stop_kind_child
  capture_partial_kind_resources_bounded || true
  exit 130
}
trap cleanup_resources EXIT
trap interrupt INT TERM

while (($#)); do
  case "$1" in
    --create) create=true; shift ;;
    --registry) registry="${2:?missing registry}"; shift 2 ;;
    --build-push-local) build_push=true; shift ;;
    --run) run=true; shift ;;
    --collect) collect="${2:?missing evidence path}"; shift 2 ;;
    --cleanup) cleanup_requested=true; shift ;;
    --verify-pods-json) verify_pods_json="${2:?missing Pod JSON path}"; shift 2 ;;
    --verify-cleanup-ownership-json) verify_cleanup_ownership_json="${2:?missing cleanup ownership JSON path}"; shift 2 ;;
    --verify-cleanup-evidence-json) verify_cleanup_evidence_json="${2:?missing cleanup evidence JSON path}"; shift 2 ;;
    *) reject "unknown_argument" ;;
  esac
done

if [[ -n "$verify_pods_json" || -n "$verify_cleanup_ownership_json" || -n "$verify_cleanup_evidence_json" ]]; then
  verification_only=true
  verification_mode_count=0
  [[ -z "$verify_pods_json" ]] || verification_mode_count=$((verification_mode_count + 1))
  [[ -z "$verify_cleanup_ownership_json" ]] || verification_mode_count=$((verification_mode_count + 1))
  [[ -z "$verify_cleanup_evidence_json" ]] || verification_mode_count=$((verification_mode_count + 1))
  [[ "$verification_mode_count" == 1 ]] || reject "verification_mode_conflict"
  [[ "$create" == false && "$build_push" == false && "$run" == false && "$cleanup_requested" == false && -z "$registry" && -z "$collect" ]] || reject "verification_mode_conflict"
  if [[ -n "$verify_pods_json" ]]; then
    verify_pods_json_file "$verify_pods_json"
  elif [[ -n "$verify_cleanup_ownership_json" ]]; then
    verify_cleanup_ownership_json_file "$verify_cleanup_ownership_json"
  else
    verify_cleanup_evidence_json_file "$verify_cleanup_evidence_json"
  fi
  exit 0
fi

[[ "$registry" == "127.0.0.1:5001" || "$registry" == "localhost:5001" ]] || reject "registry_not_loopback"
[[ -z "${GHCR_TOKEN:-}" && -z "${GITHUB_TOKEN:-}" && -z "${DOCKER_AUTH_CONFIG:-}" ]] || reject "registry_credentials_present"
[[ -z "${ISOLATED_LIVE_SOURCE_OVERRIDE:-}" ]] || reject "source_digest_mismatch"
[[ "${ISOLATED_LIVE_TEST_PREEXISTING:-}" != "registry" ]] || reject "preexisting_registry"
[[ "${ISOLATED_LIVE_TEST_PREEXISTING:-}" != "cluster" ]] || reject "preexisting_cluster"
port_occupied && reject "occupied_port"
docker container inspect "$REGISTRY_NAME" >/dev/null 2>&1 && reject "preexisting_registry"
docker ps -aq --filter "label=$TASK_LABEL" | grep -q . && reject "preexisting_labeled_resource"

task_dir="$(mktemp -d -t three-t-task14.XXXXXX)"
task_kubeconfig="$task_dir/kubeconfig"
user_kubeconfig_path="${KUBECONFIG:-${HOME}/.kube/config}"
user_kubeconfig_fingerprint_before="$(kubeconfig_fingerprint "$user_kubeconfig_path")"
export KUBECONFIG="$task_kubeconfig"
kind_bin="$(command -v kind || true)"
if [[ -z "$kind_bin" ]]; then
  kind_bin="$task_dir/kind"
  curl --fail --location --silent --show-error \
    "https://github.com/kubernetes-sigs/kind/releases/download/$KIND_VERSION/kind-linux-amd64" \
    --output "$kind_bin"
  printf '%s  %s\n' "$KIND_SHA256" "$kind_bin" | sha256sum --check --strict >/dev/null
  chmod 0755 "$kind_bin"
fi
"$kind_bin" get clusters | grep -Fxq "$CLUSTER_NAME" && reject "preexisting_cluster"

[[ "$create" == true && "$build_push" == true && "$run" == true ]] || reject "complete_workflow_required"
[[ -n "$collect" ]] || reject "collect_required"
baseline_containers="$(docker ps -aq --no-trunc | sort)"
baseline_networks="$(docker network ls -q --no-trunc | sort)"
baseline_captured=true

set_phase "registry"
registry_created=true
registry_task_token="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
registry_container_id="$(docker run --detach --name "$REGISTRY_NAME" --label "$TASK_LABEL" \
  --label "three-t.dev/task-run=$registry_task_token" \
  --publish 127.0.0.1:5001:5000 "$REGISTRY_IMAGE")"
for _ in $(seq 1 60); do
  curl --fail --silent http://127.0.0.1:5001/v2/ >/dev/null && break
  sleep 1
done
curl --fail --silent http://127.0.0.1:5001/v2/ >/dev/null

set_phase "cluster"
kind_creation_started_epoch="$(docker_created_epoch "$(docker info --format '{{.SystemTime}}')" || true)"
[[ -n "$kind_creation_started_epoch" ]] || reject "docker_clock_unavailable"
kind_child_started=true
"$kind_bin" create cluster --name "$CLUSTER_NAME" --image "$NODE_IMAGE" \
  --config tests/isolated_live/fixtures/kind-local-registry.yaml --wait 180s &
kind_child_pid=$!
set +e
wait "$kind_child_pid"
kind_status=$?
set -e
kind_child_pid=""
if [[ "$kind_status" != 0 ]]; then
  capture_partial_kind_resources_bounded || true
  exit "$kind_status"
fi
kind_network_id="$(docker network inspect --format '{{.Id}}' kind)"
[[ -n "$kind_network_id" ]] || reject "kind_network_unavailable"
docker network connect kind "$REGISTRY_NAME"
mapfile -t kind_node_ids < <(kind_node_id_set)
((${#kind_node_ids[@]} > 0)) || reject "kind_nodes_unavailable"
kind_network_member_ids="$(kind_network_member_id_set "$kind_network_id")"
[[ -n "$kind_network_member_ids" ]] || reject "kind_network_members_unavailable"
cluster_created=true
set_phase "cluster-configure"
for node in $("$kind_bin" get nodes --name "$CLUSTER_NAME"); do
  docker exec "$node" mkdir -p /etc/containerd/certs.d/localhost:5001 /srv/three-t-pipeline
  docker exec -i "$node" sh -c 'cat > /etc/containerd/certs.d/localhost:5001/hosts.toml' <<'EOF'
server = "http://three-t-pipeline-registry:5000"
[host."http://three-t-pipeline-registry:5000"]
  capabilities = ["pull", "resolve"]
EOF
  docker exec "$node" systemctl restart containerd
done

registry_digest() {
  local repository=$1
  local headers="$task_dir/$repository.headers"
  curl --fail --silent --dump-header "$headers" --output /dev/null \
    --header 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' \
    "http://127.0.0.1:5001/v2/$repository/manifests/smoke"
  tr -d '\r' <"$headers" | awk 'tolower($1)=="docker-content-digest:" {print $2}'
}

record_image() {
  local repository=$1
  local source=$2
  local digest
  digest="$(registry_digest "$repository")"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || reject "mutable_digest"
  docker buildx imagetools inspect "localhost:5001/$repository@$digest" >/dev/null
  push_records="$(jq -c --arg target "localhost:5001/$repository" --arg digest "$digest" '. + [{target:$target,digest:$digest}]' <<<"$push_records")"
  image_records="$(jq -c --arg repository "$repository" --arg source "$source" --arg digest "$digest" '. + [{repository:$repository,source:$source,digest:$digest,reference:("localhost:5001/"+$repository+"@"+$digest)}]' <<<"$image_records")"
}

set_phase "images"
docker buildx build --push --platform linux/amd64 --provenance=false --sbom=false \
  --tag localhost:5001/three-t-platform:smoke --metadata-file "$task_dir/platform-metadata.json" \
  --file Containerfile.runtime .
record_image "three-t-platform" "local-build"
platform_ref="$(jq -r '.[] | select(.repository=="three-t-platform") | .reference' <<<"$image_records")"
docker buildx build --push --platform linux/amd64 --provenance=false --sbom=false \
  --build-arg "PLATFORM_IMAGE=$platform_ref" --tag localhost:5001/three-t-hello:smoke \
  --metadata-file "$task_dir/hello-metadata.json" \
  --file tests/isolated_live/fixtures/Containerfile.hello tests/isolated_live/fixtures
record_image "three-t-hello" "local-build"

set_phase "mirror"
while IFS='|' read -r source repository; do
  platform_digest="$(docker buildx imagetools inspect --raw "$source" | jq -r '.manifests[]? | select(.platform.os=="linux" and .platform.architecture=="amd64" and (.platform.variant // "") == "") | .digest' | head -n1)"
  mirror_source="$source"
  [[ -z "$platform_digest" ]] || mirror_source="${source%%@*}@$platform_digest"
  docker buildx imagetools create --prefer-index=false \
    --tag "localhost:5001/$repository:smoke" "$mirror_source"
  record_image "$repository" "$source"
done <<EOF
$MINIO_SOURCE|${MINIO_LOCAL#localhost:5001/}
$ARGO_CONTROLLER_SOURCE|${ARGO_CONTROLLER_LOCAL#localhost:5001/}
$ARGO_SERVER_SOURCE|${ARGO_SERVER_LOCAL#localhost:5001/}
$ARGO_EXECUTOR_SOURCE|${ARGO_EXECUTOR_LOCAL#localhost:5001/}
$KUBECTL_SOURCE|${KUBECTL_LOCAL#localhost:5001/}
$NFD_SOURCE|${NFD_LOCAL#localhost:5001/}
$NVIDIA_SOURCE|${NVIDIA_LOCAL#localhost:5001/}
EOF

image_ref() {
  jq -r --arg repository "$1" '.[] | select(.repository==$repository) | .reference' <<<"$image_records"
}
minio_ref="$(image_ref minio)"
controller_ref="$(image_ref argo-controller)"
server_ref="$(image_ref argo-server)"
executor_ref="$(image_ref argo-executor)"
kubectl_ref="$(image_ref kubectl)"
hello_ref="$(image_ref three-t-hello)"

set_phase "render"
helm template argo-workflows .cache/helm/charts/argo-workflows-1.0.19.tgz \
  --namespace argo --include-crds -f deploy/helm/argo-workflows-values.yaml >"$task_dir/argo.yaml"
sed -i \
  -e "s#${ARGO_CONTROLLER_SOURCE}#${controller_ref}#g" \
  -e "s#${ARGO_SERVER_SOURCE}#${server_ref}#g" \
  -e "s#${ARGO_EXECUTOR_SOURCE}#${executor_ref}#g" \
  -e "s#${KUBECTL_SOURCE}#${kubectl_ref}#g" "$task_dir/argo.yaml"
for source in "$ARGO_CONTROLLER_SOURCE" "$ARGO_SERVER_SOURCE" "$ARGO_EXECUTOR_SOURCE" "$KUBECTL_SOURCE"; do
  ! grep -Fq "$source" "$task_dir/argo.yaml" || reject "partial_publication"
done

cat >"$task_dir/smoke.yaml" <<EOF
apiVersion: v1
kind: Namespace
metadata: {name: argo, labels: {three-t.dev/task: 14-isolated-live}}
---
apiVersion: v1
kind: Secret
metadata: {name: minio-credentials, namespace: argo}
type: Opaque
stringData: {accessKey: task14access, secretKey: task14secretkey}
---
apiVersion: v1
kind: ServiceAccount
metadata: {name: smoke-runner, namespace: argo}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: smoke-runner, namespace: argo}
rules:
  - apiGroups: [""]
    resources: [pods, pods/log]
    verbs: [get, list, watch, create, patch, delete]
  - apiGroups: [argoproj.io]
    resources: [workflowtaskresults]
    verbs: [create, patch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: smoke-runner, namespace: argo}
subjects: [{kind: ServiceAccount, name: smoke-runner, namespace: argo}]
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: Role, name: smoke-runner}
---
apiVersion: v1
kind: PersistentVolume
metadata: {name: three-t-smoke-pv, labels: {three-t.dev/task: 14-isolated-live}}
spec:
  capacity: {storage: 1Gi}
  accessModes: [ReadWriteOnce]
  storageClassName: three-t-smoke
  persistentVolumeReclaimPolicy: Delete
  hostPath:
    path: /srv/three-t-pipeline
    type: Directory
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: three-t-smoke-pvc, namespace: argo}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: three-t-smoke
  volumeName: three-t-smoke-pv
  resources: {requests: {storage: 1Gi}}
---
apiVersion: v1
kind: Service
metadata: {name: minio, namespace: argo}
spec:
  selector: {app: three-t-smoke-minio}
  ports: [{name: api, port: 9000, targetPort: 9000}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: minio, namespace: argo}
spec:
  replicas: 1
  selector: {matchLabels: {app: three-t-smoke-minio}}
  template:
    metadata: {labels: {app: three-t-smoke-minio, three-t.dev/task: 14-isolated-live}}
    spec:
      containers:
        - name: minio
          image: $minio_ref
          args: [server, /data]
          env:
            - {name: MINIO_ROOT_USER, valueFrom: {secretKeyRef: {name: minio-credentials, key: accessKey}}}
            - {name: MINIO_ROOT_PASSWORD, valueFrom: {secretKeyRef: {name: minio-credentials, key: secretKey}}}
          volumeMounts: [{name: data, mountPath: /data}]
      volumes: [{name: data, persistentVolumeClaim: {claimName: three-t-smoke-pvc}}]
---
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  name: three-t-hello-smoke
  namespace: argo
  labels: {three-t.dev/task: 14-isolated-live}
spec:
  entrypoint: pipeline
  serviceAccountName: smoke-runner
  volumes: [{name: data, persistentVolumeClaim: {claimName: three-t-smoke-pvc}}]
  templates:
    - name: pipeline
      steps:
        - - {name: platform-preflight, template: platform-preflight}
        - - {name: hello, template: hello}
    - name: platform-preflight
      container:
        image: $platform_ref
        command: [3t-pipeline]
        args: [runtime, init, --phase, preflight]
    - name: hello
      container:
        image: $hello_ref
        command: [python]
        args: [/opt/three-t-smoke/hello.py]
        env:
          - {name: AWS_ENDPOINT_URL, value: "http://minio.argo.svc:9000"}
          - {name: AWS_ACCESS_KEY_ID, valueFrom: {secretKeyRef: {name: minio-credentials, key: accessKey}}}
          - {name: AWS_SECRET_ACCESS_KEY, valueFrom: {secretKeyRef: {name: minio-credentials, key: secretKey}}}
          - {name: AWS_DEFAULT_REGION, value: us-east-1}
          - {name: OUTPUT_BUCKET, value: task14-results}
          - {name: OUTPUT_PREFIX, value: runs/hello}
          - {name: RUN_UID, value: task14-run}
          - {name: WORKFLOW_UID, value: "{{workflow.uid}}"}
        volumeMounts: [{name: data, mountPath: /cache}]
EOF
csplit --quiet --prefix="$task_dir/smoke-" "$task_dir/smoke.yaml" \
  '/^apiVersion: argoproj.io/' '{*}'
grep -E '^ *image:' "$task_dir/argo.yaml" "$task_dir/smoke.yaml" | grep -vE '@sha256:[0-9a-f]{64}"?$' && reject "mutable_rendered_image"

set_phase "apply"
kubectl --context "$CONTEXT" create namespace argo
kubectl --context "$CONTEXT" apply -f "$task_dir/argo.yaml"
kubectl --context "$CONTEXT" -n argo wait --for=condition=complete \
  job/argo-workflows-crd-install --timeout=180s
kubectl --context "$CONTEXT" wait --for=condition=Established \
  crd/workflows.argoproj.io --timeout=60s
kubectl --context "$CONTEXT" apply -f "$task_dir/smoke-00"
kubectl --context "$CONTEXT" apply -f "$task_dir/smoke-01"
kubectl --context "$CONTEXT" -n argo rollout status deployment/minio --timeout=180s
kubectl --context "$CONTEXT" -n argo rollout status deployment/argo-workflows-workflow-controller --timeout=180s
set_phase "workflow"
for _ in $(seq 1 300); do
  workflow_phase="$(kubectl --context "$CONTEXT" -n argo get workflow three-t-hello-smoke -o jsonpath='{.status.phase}')"
  case "$workflow_phase" in
    Succeeded) break ;;
    Failed|Error) reject "workflow_terminal_${workflow_phase,,}" ;;
    *) sleep 1 ;;
  esac
done
[[ "$workflow_phase" == "Succeeded" ]] || reject "workflow_timeout"

set_phase "collect"
workflow_json="$(kubectl --context "$CONTEXT" -n argo get workflow three-t-hello-smoke -o json)"
pod_name="$(kubectl --context "$CONTEXT" -n argo get pods -l workflows.argoproj.io/workflow=three-t-hello-smoke -o jsonpath='{.items[0].metadata.name}')"
pod_json="$(kubectl --context "$CONTEXT" -n argo get pod "$pod_name" -o json)"
pods_json="$(kubectl --context "$CONTEXT" -n argo get pods -o json)"
pvc_json="$(kubectl --context "$CONTEXT" -n argo get pvc three-t-smoke-pvc -o json)"
pod_image_pairs="$(pod_image_pairs_json <<<"$pods_json")" || reject "pod_json_invalid"
verify_pod_image_pairs "$pod_image_pairs"
set_phase "port-forward"
kubectl --context "$CONTEXT" -n argo port-forward service/minio 19000:9000 >"$task_dir/port-forward.log" 2>&1 &
port_forward_pid=$!
for _ in $(seq 1 30); do
  curl --fail --silent http://127.0.0.1:19000/minio/health/ready >/dev/null && break
  sleep 1
done
object_json="$(AWS_ACCESS_KEY_ID=task14access AWS_SECRET_ACCESS_KEY=task14secretkey AWS_DEFAULT_REGION=us-east-1 AWS_ENDPOINT_URL=http://127.0.0.1:19000 "$PWD/.venv/bin/python" -c 'import boto3,json; c=boto3.client("s3"); h=c.head_object(Bucket="task14-results",Key="runs/hello/result.json"); print(json.dumps({"key":"runs/hello/result.json","size":h["ContentLength"],"sha256":h["Metadata"]["sha256"]},separators=(",",":")))')" || reject "object_query_failed"
marker_json="$(AWS_ACCESS_KEY_ID=task14access AWS_SECRET_ACCESS_KEY=task14secretkey AWS_DEFAULT_REGION=us-east-1 AWS_ENDPOINT_URL=http://127.0.0.1:19000 "$PWD/.venv/bin/python" -c 'import boto3,sys; c=boto3.client("s3"); sys.stdout.write(c.get_object(Bucket="task14-results",Key="runs/hello/_COMMITTED.json")["Body"].read().decode())')" || reject "marker_query_failed"

if [[ "$cleanup_requested" == true ]]; then
  set_phase "cleanup"
  cleanup_task_dir="$task_dir"
  cleanup_task_kubeconfig="$task_kubeconfig"
  cleanup_port_forward_pid="$port_forward_pid"
  cleanup_registry_container_id="$registry_container_id"
  cleanup_kind_network_id="$kind_network_id"
  cleanup_kind_node_ids=("${kind_node_ids[@]}")
  cleanup_resources
fi
cleanup_json="$(jq -nc \
  --argjson portFree "$(port_occupied && printf false || printf true)" \
  --argjson portForwardFree "$(port_is_occupied 19000 && printf false || printf true)" \
  --argjson registryAbsent "$(docker container inspect "$REGISTRY_NAME" >/dev/null 2>&1 && printf false || printf true)" \
  --argjson registryContainerIdAbsent "$(captured_container_ids_absent "$cleanup_registry_container_id" && printf true || printf false)" \
  --argjson clusterAbsent "$(docker ps -aq --filter "name=${CLUSTER_NAME}-" | grep -q . && printf false || printf true)" \
  --argjson kindNodeIdsAbsent "$(captured_container_ids_absent "${cleanup_kind_node_ids[@]}" && printf true || printf false)" \
  --argjson kindNetworkIdAbsent "$(docker network inspect "$cleanup_kind_network_id" >/dev/null 2>&1 && printf false || printf true)" \
  --argjson containersUnchanged "$( [[ "$(docker ps -aq --no-trunc | sort)" == "$baseline_containers" ]] && printf true || printf false )" \
  --argjson networksUnchanged "$( [[ "$(docker network ls -q --no-trunc | sort)" == "$baseline_networks" ]] && printf true || printf false )" \
  --argjson tempAbsent "$( [[ -n "$cleanup_task_dir" && ! -e "$cleanup_task_dir" ]] && printf true || printf false)" \
  --argjson pidAbsent "$(captured_process_absent "$cleanup_port_forward_pid" && printf true || printf false)" \
  --argjson kubeconfigIsolated "$( [[ "$cleanup_task_kubeconfig" == "$cleanup_task_dir/kubeconfig" && ! -e "$cleanup_task_kubeconfig" ]] && printf true || printf false)" \
  --argjson userKubeconfigUnchanged "$( [[ "$(kubeconfig_fingerprint "$user_kubeconfig_path")" == "$user_kubeconfig_fingerprint_before" ]] && printf true || printf false)" \
  '{port5001Free:$portFree,port19000Free:$portForwardFree,registryAbsent:$registryAbsent,registryContainerIdAbsent:$registryContainerIdAbsent,clusterAbsent:$clusterAbsent,kindNodeIdsAbsent:$kindNodeIdsAbsent,kindNetworkIdAbsent:$kindNetworkIdAbsent,containersUnchanged:$containersUnchanged,networksUnchanged:$networksUnchanged,tempAbsent:$tempAbsent,pidAbsent:$pidAbsent,kubeconfigIsolated:$kubeconfigIsolated,userKubeconfigUnchanged:$userKubeconfigUnchanged}')"
verify_cleanup_evidence_json "$cleanup_json"
mkdir -p "$(dirname "$collect")"
jq -n \
  --arg schema "three-t-pipeline/isolated-live-v1" \
  --arg context "$CONTEXT" \
  --arg phase "$(jq -r '.status.phase' <<<"$workflow_json")" \
  --arg workflowUid "$(jq -r '.metadata.uid' <<<"$workflow_json")" \
  --arg podUid "$(jq -r '.metadata.uid' <<<"$pod_json")" \
  --arg node "$(jq -r '.spec.nodeName' <<<"$pod_json")" \
  --arg pvcUid "$(jq -r '.metadata.uid' <<<"$pvc_json")" \
  --argjson pushes "$push_records" --argjson images "$image_records" \
  --argjson pods "$(jq --argjson comparisons "$pod_image_pairs" '[.items[] | .metadata.uid as $uid | {uid:$uid,node:.spec.nodeName,phase:.status.phase,statuses:[$comparisons[] | select(.podUid == $uid) | {name,declaredImage,image,imageID}]}]' <<<"$pods_json")" \
  --argjson object "$object_json" --argjson marker "$marker_json" --argjson cleanup "$cleanup_json" \
  '{schema:$schema,context:$context,namespace:"argo",workflow:{phase:$phase,uid:$workflowUid},pod:{uid:$podUid,node:$node},pods:$pods,pvc:{name:"three-t-smoke-pvc",uid:$pvcUid},pushes:$pushes,images:$images,objects:[$object],marker:$marker,cleanup:$cleanup,externalRegistryWrites:0,ghcrWrites:0}' >"$collect" || reject "evidence_write_failed"
jq -e '.workflow.phase=="Succeeded" and .ghcrWrites==0 and (.pushes|all(.target|startswith("localhost:5001/"))) and .marker.schema=="committed/v1" and .marker.workflowUid==.workflow.uid and .marker.runUid=="task14-run" and .marker.objects[0].sha256==.objects[0].sha256 and (.cleanup|to_entries|all(.value==true))' "$collect" >/dev/null
set_phase "complete"
printf 'ISOLATED_LIVE_SUCCEEDED evidence=%s\n' "$collect"
