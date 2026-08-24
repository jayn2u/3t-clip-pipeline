# Consumer adapter boundary

The `v1alpha1` workload is a platform execution contract, not an application configuration schema. A consumer owns its application config, code, output meaning, and any command fan-out. It submits only immutable execution inputs and the generic runtime requirements the platform must enforce.

## Mapping an application

| Consumer-owned concern | Workload adapter hook |
| --- | --- |
| Application settings and domain vocabulary | Keep in the bundle; pass its relative path through `spec.execution.command`. |
| Application entrypoint | Express as argv in `spec.execution.command`; the platform does not interpret it. |
| Immutable input objects | Declare generic `spec.cacheMappings` with S3 URI, destination, inventory digest, and read-only intent. |
| Scheduler needs | Declare `cacheProfile`, CPU, memory, and a generic GPU resource key/count. |
| Credentials and runtime identity | Use Secret, ConfigMap, or field references in `spec.environment`; never inline values. |
| Completion | Declare only required relative paths under `spec.outputs`; the platform verifies and commits them. |

The standalone example at `examples/consumer/python-job/` demonstrates the seam. `app-config.json` is understood only by `job.py`; `workload.yaml` supplies the frozen platform contract.

## Intentionally excluded policy

Reference-only dataset catalogs, evaluation choices, artifact naming, command matrices, object-store deployment identities, storage topology, and ingress hostnames remain outside the platform. The exhaustive classification ledger names an adapter hook for each excluded reference fixture. Consumers can change those policies without a platform schema or renderer change.

## Rejection boundary

Unknown application fields are rejected by the strict workload model. Imports from the reference package or research/runtime frameworks are rejected by portability policy tests. The reference repository is inventory provenance only and is never imported, executed, or modified.
