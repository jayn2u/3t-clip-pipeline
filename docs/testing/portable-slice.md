# Portable contract conformance slice

The milestone-one smoke proves the frozen workload boundary without Docker, Kubernetes, a
kubeconfig, network access, or an S3 service. It validates the hello contract, renders canonical
Argo bytes, and executes platform init, consumer main, publisher, finalizer, and committed-reader
semantics in a temporary workspace backed by the deterministic fake S3.

The fake models `head`, `get`, and conditional `put`; SHA-256 metadata; corruption; timeout;
cancellation; and S3 `If-None-Match: *` precondition failures. A 412 is idempotent only when the
existing marker has the same run UID and canonical marker digest. A conflicting marker is a
collision. Readers require the strict `runtime-commit/v1` schema, the expected run UID, unique
records, an exact match to the non-staging immutable object set, matching HEAD metadata, and
independently hashed GET bodies. Unmarked, partial, stale, or corrupt prefixes remain unreadable.

Run the deterministic success smoke:

```bash
env -u KUBECONFIG ./scripts/smoke-portable.sh
```

Capture machine-verifiable success and expected failures:

```bash
./scripts/smoke-portable.sh --case success --json .omo/evidence/task-6-portable-smoke.json
./scripts/smoke-portable.sh --case corrupt-object --expect-failure \
  --json .omo/evidence/task-6-portable-smoke-error.json
./scripts/smoke-portable.sh --case cancel --expect-failure \
  --append-json .omo/evidence/task-6-portable-smoke-error.json
```

The JSON is the observable contract: validated phase flags, canonical workload/workflow/run/marker
digests, required object hashes, conditional marker attempts, marker count, reader status,
diagnostic code, independently queried fake state, and zero network/Kubernetes calls. Temporary
workspaces and fake object maps are process-local and no background process, socket, or service is
created.

The `cancel-resume` matrix case retries the same run after a cancellation manifest was written.
Its immutable publication-manifest create receives 412 and the prefix remains uncommitted.
