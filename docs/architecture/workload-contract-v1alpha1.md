# Workload contract v1alpha1

`three-t-clip-pipeline/v1alpha1` is the portable boundary between a consumer workload and the platform. The checked-in JSON Schema is generated from strict Pydantic models; the models are the source of truth. Every object rejects unknown fields.

## Boundary

The contract identifies an immutable code bundle and workload image, passes an argv array to the image, selects a relative working directory, and declares generic resource, timeout, cache, environment-reference, and publication requirements. It does not interpret training configuration, datasets, metrics, checkpoints, experiment trackers, nodes, or platform helper paths.

`command` is an argv array. The platform passes each element as data and never evaluates it through a shell. Environment entries can use exactly one `secretKeyRef`, `configMapKeyRef`, or `fieldRef`; inline values are rejected. Secret values are never serialized into the workload, canonical JSON, schema, or validation evidence.

All consumer paths use portable relative POSIX syntax. Absolute paths, backslashes, empty paths, and `..` traversal are rejected. Runtime consumers must retain this boundary by opening hydrated paths without following symlinks and by verifying the resolved destination remains inside the task-owned root.

Images must use `repository@sha256:<64 lowercase hex characters>`. Tags, including `latest`, do not identify a workload. Bundle and cache inventory identities are lowercase SHA-256 digests. S3 object references exclude credentials, query strings, and fragments.

## Defaults and limits

Canonical output materializes these defaults:

| Field | Default | Maximum |
| --- | ---: | ---: |
| `resources.cacheProfile` | `auto` | n/a |
| `resources.gpuResource` | `nvidia.com/gpu` | n/a |
| `resources.gpuCount` | `0` | n/a |
| `timeouts.profileAcquireSeconds` | `900` | `7200` |
| `timeouts.activeDeadlineSeconds` | `86400` | `604800` |
| `timeouts.terminationGraceSeconds` | `120` | `600` |
| `timeouts.publicationFinalRetrySeconds` | `300` | `3600` |

Timeouts are positive integer seconds. Resource counts are strict integers, so YAML booleans are not accepted as integer values. Cache destinations, required output paths, and environment names are unique.

## Canonical identity

Canonical serialization is UTF-8 JSON with aliases, all defaults materialized, keys sorted, compact separators, and exactly one trailing newline. The workload identity is the lowercase SHA-256 digest of those exact bytes. Parsing canonical JSON and serializing it again must reproduce the same bytes and digest.

Generate or verify the schema with:

```console
uv run python -m three_t_clip_pipeline.contract.generate_schema schemas/workload-v1alpha1.schema.json
uv run python -m three_t_clip_pipeline.contract.generate_schema --check schemas/workload-v1alpha1.schema.json
```

Validate a workload and write canonical JSON with:

```console
uv run 3t-pipeline contract validate examples/workload-minimal.yaml --canonical-json workload.json
```

## Compatibility policy

Evolution within `v1alpha1` is additive only: a revision may add an optional field with a backward-compatible default. Removing or renaming a field, adding a required field, changing field meaning or validation semantics, or changing a container/runtime boundary requires a new API version. Existing canonical fixtures and the checked-in schema are compatibility snapshots for this version.
