# Operator CLI verification and authorization

The CLI exposes explicit verification levels. It never selects the current Kubernetes context and
never infers an environment class from a context name. A context must also be nonblank and free of
control characters; invalid context input exits 2 with `context_required` before client creation.

| Command | External boundary | Persistent mutation |
|---|---|---|
| `contract validate WORKLOAD` | Offline contract parsing | None |
| `plan WORKLOAD --output -` | Offline deterministic render | None |
| `validate WORKLOAD --client` | Packaged local validator | None |
| `preflight --context X --environment-class CLASS` | Authenticated Kubernetes GET/LIST | None |
| `validate WORKLOAD --server --context X --environment-class CLASS` | Kubernetes server-side dry-run PATCH | None |
| `status NAME --context X --environment-class CLASS` | Authenticated Workflow GET | None |
| `evidence NAME --context X --environment-class CLASS --output FILE` | Authenticated Workflow GET | None |
| `submit WORKLOAD --context X --environment-class CLASS [--watch]` | Workflow CREATE, then optional bounded watch GET | One Workflow |

`CLASS` is exactly one of `ephemeral`, `development`, or `production`. Production permits
preflight, status, evidence, and server-side dry-run. It rejects `submit` with exit code 3 and
`production_mutation_forbidden` before constructing a Kubernetes client. There are no apply or
smoke commands in this interface.

Secret-bearing flags are not part of the public help surface. Attempts to pass `--secret`,
`--password`, `--token`, or `--access-key` are rejected eagerly with exit code 3 and
`secret_flag_forbidden`; supplied values are discarded and never echoed.

Exit codes are stable: 2 means malformed or incomplete input, 3 means authorization rejection,
4 means an external client failure, and 5 means a watched Workflow reached a non-success terminal
phase or produced no authoritative terminal phase. A successful `submit --watch` requires the
Workflow phase `Succeeded`; workload logs alone never establish success.

Evidence uses a permit list. Output may contain only Workflow UID and phase, digest-only images,
the selected node and PVC, object size/SHA-256, and marker status. Raw object keys are never
persisted because opaque key text may itself contain credentials. Kubernetes Secret data,
environment dumps, credential values, signed URLs, and mutable image tags are discarded.
