# Operator journey

This is the ordered path from a local workload to an evidence-backed handoff. Stop at the first
failed gate. Every command before an explicitly marked authorization boundary is offline,
read-only, a server dry-run, or confined to the disposable isolated environment.

## 1. Offline and client validation

```bash
uv run 3t-pipeline contract validate examples/workload-minimal.yaml
uv run 3t-pipeline plan examples/workload-minimal.yaml --output .cache/planned-workflow.yaml
uv run 3t-pipeline validate examples/workload-minimal.yaml --client
```

Expected evidence is the validated workload path and deterministic planned Workflow. Abort on a
schema error, mutable image, missing digest, or a diff from the reviewed plan.

## 2. Read-only and server preflight

Choose the kubeconfig, context, and environment class explicitly. Never rely on the current
context. The following reads cluster state and performs a non-persisting server dry-run:

```bash
uv run 3t-pipeline preflight --context staging --environment-class development
uv run 3t-pipeline validate examples/workload-minimal.yaml --server --context staging --environment-class development
```

Record context, cluster identity, resource ownership, server validation, image digests, storage
class, and unresolved warnings. Abort when identity is ambiguous, ownership overlaps, access is
broader than expected, or server validation differs from client validation.

## 3. Disposable isolated proof

```bash
scripts/isolated-live-smoke.sh
```

This is permitted only against the script-created kind cluster and loopback registry. Retain the
scenario log, resource inventory, Workflow UID/phase, marker status, and object checksums. The
script must tear down its context and processes. It is not authority to touch LabCLIP.

## 4. Bootstrap

Check mode is the default and is safe for a reviewed inventory:

```bash
make cluster-bootstrap INVENTORY=ansible/inventory/staging.yml
```

Bootstrap apply changes hosts and is therefore **REQUIRES_SEPARATE_AUTHORIZATION**:

```bash
make cluster-bootstrap INVENTORY=ansible/inventory/staging.yml APPLY=1 CONTEXT=staging
```

Before apply, require named hosts, a maintenance window, verified backups, encryption status, and
an approved inventory diff. Abort on topology, mount, GPU, clock, firewall, digest, or identity
failure. Retain check/apply output, host facts, config diff, fetched kubeconfig mode, and component
versions without credentials.

## 5. Submit, observe, and collect evidence

Submission creates one Workflow and is **REQUIRES_SEPARATE_AUTHORIZATION**. Production submission
is always rejected.

```bash
uv run 3t-pipeline submit examples/workload-minimal.yaml --context staging --environment-class development --watch
uv run 3t-pipeline status WORKFLOW_NAME --context staging --environment-class development
uv run 3t-pipeline evidence WORKFLOW_NAME --context staging --environment-class development --output .cache/run-evidence.json
```

Success requires authoritative phase `Succeeded`, not log text. Retain the permitted evidence
fields described in [CLI verification and authorization](cli.md). Abort publication on a failed or
unknown phase, missing final marker, checksum mismatch, mutable image, or incomplete evidence.

## 6. Backup, readiness, cutover, and rollback

First freeze the legacy owner and create verified backups of the datastore, manifests, MinIO
objects, and required tokens. Run the readiness exporter only in read-only mode, review every
blocking result, and rehearse restore against an isolated destination. The detailed PV/MinIO
cutover and rollback runbooks are maintained as `docs/operator/pv-minio-cutover.md` and
`docs/operator/pv-minio-rollback.md`.

Live freeze, backup, restore, service scaling, ownership annotation changes, cutover, rollback,
credential rotation, and deletion are all **REQUIRES_SEPARATE_AUTHORIZATION**. No command in this
guide performs them. A change ticket must name the old and new owners, exact resources, window,
operator, backup artifacts, abort thresholds, and rollback authority.

## Recovery

If any gate fails, preserve the first failure and evidence, stop dependent steps, and return to the
last accepted ownership state. Do not repair while calling the operation preflight. Follow
[Recovery and rollback](recovery.md) and the [ownership handoff](../migration/ownership-handoff.md).

## Non-goals

This journey does not migrate a consumer, initialize a remote, take over a live cluster, copy or
restore production data, rotate credentials, delete resources, push a branch, or open a pull
request. Those actions have separate owners and authorization gates.
