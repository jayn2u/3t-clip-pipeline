# PV and MinIO rollback

Rollback is a separately authorized maintenance action. `AUTHORIZATION_FILE` must already exist with mode `0600`, exact context, window, rollback owner, and approved commands. Neither this runbook nor automated QA creates it.

Trigger rollback on immutable PV drift, ownership conflict, mount-device mismatch, missing objects, marker mismatch, active writers after the freeze boundary, or a failed API readiness check. The named rollback owner decides and records the trigger.

## Read-only confirmation

```bash
export CONTEXT=lab-a
export AUTHORIZATION_FILE=.omo/authorizations/pv-minio-cutover.json
kubectl --context "$CONTEXT" get pv three-t-local-data -o json > rollback-pv-current.json
kubectl --context "$CONTEXT" -n three-t-pipeline get deploy,minio,pvc -o json > rollback-workloads-current.json
findmnt --target /srv/three-t-pipeline --output SOURCE,TARGET,FSTYPE --json
jq -e '.ready == true and .mutationCalls == 0' cutover-readiness.json
```

Compare the live UIDs, specs, managed fields, owner annotations, device, object count/bytes/SHA-256, and committed markers with the pre-cutover package. `Retain` is not a backup, and an existing path is not proof that the intended device is mounted.

The rollback proof used by readiness is a separately captured `rollback.json` file. It must bind
the same `resourceUid` and quiescence `freezeMarker` to a non-empty rollback owner and exact command
inventory. Its bundle record uses source kind `rollback`, descriptor
`operator-owned rollback artifact JSON`, the real collector exit code, a relative file name, and
the independently computed content SHA-256. Any mismatch or missing source fails readiness with
zero mutation calls. The evaluator reads this artifact but never executes its commands.

## Authorized rollback commands

The authorization artifact must name the rollback owner and contain the exact versioned manifest and scale commands. Under that authority, the owner freezes new writers, scales the new owner down, reapplies the captured legacy manifests using server-side ownership rules, restores the independently verified backup only if the decision record requires it, and then brings the legacy owner up. Never delete or recreate the PV/PVC to resolve an immutable diff.

After each approved command, query Kubernetes and MinIO APIs. Stop on a UID change, ownership conflict, wrong `findmnt` source, count/bytes/SHA mismatch, missing committed marker, or any unapproved writer. Record the final state as `rolled-back`, the rollback owner, command transcript, API UIDs, PV/PVC binding, node, object inventory, markers, and the unchanged authorization artifact checksum.

Do not place credentials, Secret data, kubeconfigs, environment dumps, tokens, passwords, access
keys, secret keys, signed URLs, or MinIO authentication material in rollback or readiness evidence.
