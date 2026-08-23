# Deterministic offline rendering

`3t-pipeline plan WORKLOAD --output -` parses the frozen
`three-t-clip-pipeline/v1alpha1` contract and emits one deterministic Argo
`argoproj.io/v1alpha1` Workflow. The renderer performs no Kubernetes, S3,
kubeconfig, credential, clock, or network access. The run UID is the SHA-256 of
the canonical workload JSON, so identical validated input produces identical
YAML bytes.

The workflow fixes the DAG order to `preflight -> acquire-profile ->
prepare-cache -> run` and uses `commit-results` as `onExit`. The workload pod
contains regular init container `bundle-init`, consumer container `main`, and
native sidecar init container `artifact-publisher` with `restartPolicy:
Always`. Input and cache mounts are read-only for `main`; output is read-only
for the publisher. All images are digest references. The currently frozen
platform image source is the Python runtime-base digest in `ci/tools.lock.yaml`;
the separately authorized runtime-image supply-chain task owns replacement with
an approved built runtime digest.

## Local client validation

Run `scripts/bootstrap-client-validator.sh` once to install kubeconform `v0.7.0`
at `.cache/tools/kubeconform-v0.7.0`. The bootstrap verifies the frozen release
archive SHA-256 before installation and verifies the extracted binary SHA-256.

`3t-pipeline validate WORKLOAD --client` renders to a private temporary path,
verifies every packaged schema against `schemas/schema-sources.lock.yaml`, and
invokes only `scripts/validate-manifest-local.sh`. The wrapper checks the local
binary digest and supplies only the checked-in Kubernetes `1.36.2` strict and
Argo `v4.0.7` schema locations. Missing schemas fail. Client validation never
uses `kubectl`, a cluster, a default remote schema registry, or kubeconfig.

The Kubernetes schemas are pinned to yannh/kubernetes-json-schema commit
`5a69f8365c9d3ed7de997f5365e22481cf775fa2`. The Workflow schema is extracted
from the Argo Workflows full CRD at commit
`9aeb47ce10339f4a14819335c6a00027353ba0df`. Upgrade either source only by
updating the pin, packaged bytes, and every per-file lock hash together.
