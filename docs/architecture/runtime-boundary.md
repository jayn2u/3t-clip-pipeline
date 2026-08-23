# Frozen runtime boundary

The platform and consumer meet at four named container roles. These names, mount paths, manifest names, and status mappings are compatibility surface for `v1alpha1`; changing one requires a new contract version.

| Role | Image and Kubernetes form | Writable mounts | Read-only mounts |
| --- | --- | --- | --- |
| `bundle-init` | platform image, regular init container | `input` `emptyDir` at `/workspace/input`; `cache` PVC at `/workspace/cache`; `control` `emptyDir` at `/workspace/control` | none |
| `main` | immutable consumer image and consumer argv | `output` `emptyDir` at `/workspace/output` | `input` at `/workspace/input`; `cache` at `/workspace/cache` |
| `artifact-publisher` | platform image, native sidecar under `initContainers` with `restartPolicy: Always` | `control` at `/workspace/control` | `output` at `/workspace/output` |
| `commit-results` | platform image, separate Argo `onExit` pod | none of the run pod's shared volumes | S3 manifests and immutable objects through its client |

The main container receives an argv array directly. It does not contain or import the platform package and never executes a shell string. Read-only input/cache mounts prevent it from changing verified inputs; its only shared write surface is output.

## State ordering

`bundle-init` downloads the immutable bundle and declared cache inventories into temporary files, verifies their SHA-256 identities, promotes them into their task-owned roots, and uploads `<run-prefix>/.staging/<run-uid>/init.json`. It returns only after that manifest exists, so Kubernetes cannot start `main` first.

`artifact-publisher` discovers only regular files under `/workspace/output`. It ignores symlinks and the platform-owned names `publication.json` and `COMMITTED.json`. Payload keys are immutable and may become visible independently; this is not prefix atomicity. Kubernetes sends TERM only after main exits. TERM stops new discovery, interrupts an in-flight upload, performs only the bounded terminal flush, and uploads `<run-prefix>/.staging/<run-uid>/publication.json`. An interrupted upload records `publicationState: cancelled` with diagnostics. The sidecar interface deliberately has no marker-write capability.

`commit-results` reads stable Workflow main status plus strict init/publication manifests from S3. Only `Succeeded` is commit-eligible; `Failed`, `Cancelled`, and `TimedOut` are refused. The finalizer HEAD-verifies every published object and every required output before one conditional create of `<run-prefix>/COMMITTED.json`. Missing or malformed manifests, missing objects, checksum/size corruption, partial publication, cancellation, timeout, or collision remain uncommitted. Output that merely prints success has no effect on this decision.

## Platform commands

The platform image exposes argv-only commands:

```console
3t-pipeline runtime init --phase preflight --request-json /workspace/control/runtime.json
3t-pipeline runtime init --phase acquire-profile --request-json /workspace/control/runtime.json
3t-pipeline runtime init --phase prepare-cache --request-json /workspace/control/runtime.json
3t-pipeline runtime publisher --request-json /workspace/control/runtime.json
3t-pipeline runtime finalize --request-json /workspace/control/runtime.json
```

Request files are strict immutable JSON models. Runtime paths reject absolute paths, backslashes, empty components, `.` and `..`. S3 downloads are promoted only after SHA-256 verification. Payloads, manifests, and the marker use conditional writes and never mutable overwrite. Finalizer refusal returns non-zero; logs or consumer output are never treated as proof of commit.

Rendered workloads supply the same strict fields through individual environment variables and JSON-array environment values, so they use `runtime init`, `runtime publisher`, and `runtime finalize` without `--request-json`. The request-file option is the equivalent explicit surface for isolated execution. Phase-labelled DAG init commands carry no S3 credentials or payload mutation; the workload pod's unlabelled `bundle-init` invocation alone hydrates the bundle/cache and writes `init.json`.
