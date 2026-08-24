# Recovery and rollback

Recovery starts by preserving evidence and availability, not by deleting or recreating resources.

1. Stop the current operation and record its command, operator, time, context, commit, resource
   identities, and first failing observable.
2. Prevent new submissions. Do not terminate an existing run unless its own authorization names it.
3. Confirm the last accepted ownership state and owner in the handoff record.
4. Verify backups from an independent destination before changing datastore, PV, or MinIO state.
5. Restore only through a separately reviewed procedure with explicit source and destination.
6. Rerun offline validation, read-only preflight, readiness, and checksum comparison.
7. Resume only when the former owner and incoming owner sign the new state.

Rollback is **REQUIRES_SEPARATE_AUTHORIZATION**. Its authorization must name the resources,
rollback owner, checkpoint, allowed mutations, abort criteria, and evidence destination. A rollback
ends in `rolled-back`; returning to `legacy-owned` requires a new acceptance record. Never use PV,
PVC, CRD, Secret, bucket, or host-directory deletion as a reset mechanism.

Abort immediately on an unidentified context or resource, changed backup checksum, missing token,
unmounted backing filesystem, unexpected writer, active workflow, object-count regression,
credential exposure, or a state that disagrees with the signed handoff.
