# Immutable publication protocol

Payload objects and sanitized provenance are written to immutable, run-scoped
keys. A completed upload is not itself a committed result: readers trust only a
canonical `COMMITTED.json` matching `schemas/committed-v1.json`. The protocol
does not claim that an S3 prefix is atomic.

The separate Argo `onExit` finalizer starts by recording its wall-clock start
time. It performs a read-only GET of the named Workflow and verifies the
Workflow name and UID. From `status.nodes` it selects exactly one node whose
type is `Pod` and whose template and display name both match the supplied main
node identity (`run`). The node must be `Succeeded`. Its RFC3339 `finishedAt`
must parse and must not be later than finalizer start; the original string is
copied byte-for-byte to `mainFinishedAt`. Top-level Workflow timestamps are
never publication evidence.

Before marker creation, the finalizer HEAD-verifies the exact size and SHA-256
of every declared output and the declared provenance object. It then
sorts `objects` by key and performs one conditional create. HTTP 412 is
idempotent only when the existing marker is canonical, has the same run UID,
and its payload digest equals the candidate payload digest. Every other 412 is
`commit_collision`.

Missing, ambiguous, malformed, non-succeeded, future-dated, partial, corrupt,
cancelled, or timed-out state remains uncommitted. Staged objects and diagnostic
manifests may remain for operators, but committed-run readers cannot expose
them. No runtime service has delete or mirror capability.

Bundle creation omits VCS state, artifacts, results, caches, credentials,
symlinks, and platform helpers. Cache hydration downloads to a uniquely named
file beside the destination, verifies remote metadata plus local size/digest,
and uses same-filesystem atomic replacement. Profile acquisition uses a
monotonic deadline, API calls of at most 15 seconds, waits of at most 5 seconds,
and releases every acquired Lease before waiting and again through `finally`
cleanup.
