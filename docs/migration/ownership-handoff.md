# Ownership handoff

Every managed resource has exactly one recorded state and owner. State transitions are evidence
events; they are not inferred from a successful command.

| State | Meaning | Permitted owner action | Required evidence |
|---|---|---|---|
| `unmanaged` | No accepted controller or human owner | inventory only | identity and absence of ownership |
| `legacy-owned` | Existing LabCLIP owner remains authoritative | normal legacy operations | owner, UID/spec, current health |
| `frozen` | Legacy writes and submissions are stopped | read-only inventory and backup | freeze approval, active-run zero/exception list |
| `ready` | Backup, restore rehearsal, compatibility, and readiness gates pass | await cutover authority | signed gate report, checksums, abort thresholds |
| `new-owned` | New field manager/operator is authoritative | approved new-platform operations | cutover authorization, UIDs, managed fields, health |
| `rolled-back` | Cutover was reversed to its checkpoint | recovery/read-only checks | rollback authority, restored SHA/state, comparisons |

The forward path is `unmanaged -> legacy-owned -> frozen -> ready -> new-owned`. A failed cutover
may move `ready` or `new-owned` to `rolled-back`; it never silently claims `legacy-owned` again.
Each transition requires the outgoing owner, incoming owner, operator, timestamp, resource list,
repository commit, evidence paths, unresolved exceptions, and explicit acceptance.

Abort on an active or unknown writer, unidentified UID, managed-field overlap, stale inventory,
failed backup/restore proof, count or checksum mismatch, failed readiness item, unavailable rollback
owner, or expired change window. Preserve the last accepted state; do not advance partially.

This handoff does not itself freeze workloads, mutate ownership metadata, apply manifests, move
data, rotate secrets, delete resources, or perform rollback.
