# PV and MinIO cutover readiness

This runbook is read-only until a separately authorized maintenance window begins. `AUTHORIZATION_FILE` must already exist, be owned by the operator, name the exact context and window, and have mode `0600`. This repository and its QA never create that artifact.

## Read-only evidence package

Set explicit inputs and capture the legacy resource without changing it. The PV annotations
`three-t.dev/owner`, `three-t.dev/intended-device`, and
`three-t.dev/max-backup-age-seconds` are the independently fetched expectations used by the
evaluator; a value copied into the bundle itself is not evidence.

```bash
export CONTEXT=lab-a
export PV_NAME=three-t-local-data
export HOST_PATH=/srv/three-t-pipeline
export INTENDED_DEVICE=/dev/nvme0n1p1
export AUTHORIZATION_FILE=.omo/authorizations/pv-minio-cutover.json
kubectl --context "$CONTEXT" get pv "$PV_NAME" -o json > pv-current.json
jq '{apiVersion,kind,metadata:{name:.metadata.name,uid:.metadata.uid,annotations:{"three-t.dev/owner":.metadata.annotations["three-t.dev/owner"],"three-t.dev/intended-device":.metadata.annotations["three-t.dev/intended-device"],"three-t.dev/max-backup-age-seconds":(.metadata.annotations["three-t.dev/max-backup-age-seconds"]|tonumber)},managedFields:(.metadata.managedFields|map({manager,operation}))},spec:{local:{path:.spec.local.path}}}' pv-current.json > kubernetes-pv.json
test -d "$HOST_PATH"
findmnt --target "$HOST_PATH" --output SOURCE,TARGET,FSTYPE --json > findmnt.json
stat --format=%F "$HOST_PATH" > directory-stat.txt
# Normalize a successful exact "directory" result to
# {"path":"/srv/three-t-pipeline","fileType":"Directory","exists":true}.
```

The `findmnt` source must equal `INTENDED_DEVICE`; a Kubernetes `Retain` policy is not backup proof. The path must already be a directory. `DirectoryOrCreate` is forbidden because it can hide a missing or wrong mount.

Use an independently administered MinIO alias to produce a sorted object inventory. Record object count, total bytes, and the SHA-256 of that inventory. Restore it into an isolated rehearsal target, independently inventory the restored objects, and require the same count, bytes, and SHA-256. Record completion time so `ageSeconds <= maxAgeSeconds` is testable.

```bash
mc find legacy/results --json | jq -Sc . | sort > backup-objects.jsonl
mc find rehearsal/results --json | jq -Sc . | sort > restore-objects.jsonl
# Convert each stream to the strict collector shape:
# {"capturedAt":"RFC3339","objects":[{"key":"...","size":1,"sha256":"..."}]}
# and save it as backup-inventory.json / restore-inventory.json.
```

Freeze writers under the existing service owner, record a unique freeze marker, and prove the
active writer count is zero from the Kubernetes API and MinIO audit surface. Store the machine
record as `quiescence.json` with `resourceUid`, `activeWriters`, and `marker`. Workload logs alone
are not proof.

Before authorization, compare the captured UID/spec/managedFields/owner with the proposed manifests and run only server-side dry-run:

```bash
kubectl --context "$CONTEXT" apply --server-side --dry-run=server -f proposed-pv-minio.yaml -o json > server-dry-run.json
.venv/bin/python -m three_t_clip_pipeline.readiness collector-bundle.json > cutover-readiness.json
jq -e '.ready == true and .mutationCalls == 0 and (.failedChecks | length == 0)' cutover-readiness.json
```

The immutable/ownership comparator writes `manifest-diff.json` with `resourceUid`,
`immutableChanges`, and `ownershipConflicts`. A separate operator-owned `rollback.json` binds
`resourceUid` and `freezeMarker` to a non-empty owner and command list.

`collector-bundle.json` uses schema `three-t-pipeline/cutover-readiness-v2` and contains exactly
nine source kinds: `kubernetes-pv`, `findmnt`, `directory-stat`, `backup-inventory`,
`restore-inventory`, `quiescence`, `manifest-diff`, `server-dry-run`, and `rollback`. The exact
approved descriptors are exported by `three_t_clip_pipeline.readiness.models.EXPECTED_DESCRIPTORS`;
generate bundle records from that frozen map instead of retyping them. Every record contains
`sourceKind`, its exact descriptor, the collector `exitCode`, `contentSha256`, and a relative
`capturedFile`. Compute each SHA-256 after capture. The evaluator never executes descriptors: it
confines each file to the bundle directory, recomputes its hash, parses its strict machine schema,
and derives every check from the payload. `verifiedSources` lists accepted descriptors and hashes.

Missing, duplicate, failed, unapproved, unreadable, hash-mismatched, or malformed evidence makes
`ready=false` and `mutationCalls=0`. So do absent UID/spec/managedFields/owner, wrong mount, stale
or mismatched backup/restore inventories, active writers, immutable changes, ownership conflicts,
failed server dry-run, or absent rollback ownership. A provenance-free v1 snapshot and asserted
`ready` booleans are rejected. Stop; do not apply anything.

Evidence payloads must contain only the documented operational fields. Never capture Secret
objects, kubeconfigs, environment dumps, access keys, secret keys, passwords, tokens, signed URLs,
or MinIO credentials. Unknown payload and bundle fields are rejected and never echoed.

## Separately authorized window

The maintenance owner validates `AUTHORIZATION_FILE`, its `0600` mode, context, expiry, and signatures out of band. Only after the readiness JSON passes may that owner execute the pre-approved apply commands stored in the authorization artifact. Recheck the PV UID and frozen marker immediately before apply. Abort on any drift, nonzero writers, partial publication, or ownership mismatch. Handoff is complete only after API-observed Ready state and independent MinIO object/marker verification.
