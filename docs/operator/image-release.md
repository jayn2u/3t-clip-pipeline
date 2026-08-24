# Runtime image release

The platform runtime is built from `Containerfile.runtime`; consumer CUDA and ML dependencies do
not belong in this image. The build context is allowlisted by `.dockerignore`, the Python base is
digest-pinned, and dependencies are installed from the checked-in `uv.lock` with the frozen uv
checksum from `ci/tools.lock.yaml`.

Pull requests run `.github/workflows/ci.yml`. That workflow may build the runtime image but has no
registry login or external push step. Local pushes are forbidden except for Task 14's disposable,
credential-free registry bound to `127.0.0.1`.

Pushes to `develop` enter the protected `runtime-image-release` GitHub environment. An approver must
authorize the job before it logs into GHCR. The job publishes only
`ghcr.io/jayn2u/3t-clip-pipeline-runtime:sha-<40-character-commit>`, asks BuildKit for maximal
provenance and an SBOM, captures `containerimage.digest`, and verifies the resulting digest reference
with `docker buildx imagetools inspect`.

The workflow uploads a proposed lock artifact. It never edits or commits `deploy/images.lock.yaml`.
Reviewers compare the proposal with the workflow run, provenance, SBOM, source commit, and verified
digest before separately promoting `runtimeImage`. A promotion must retain the full
`ghcr.io/jayn2u/3t-clip-pipeline-runtime@sha256:<64-hex>` reference. Mutable tags, including the
immutable commit tag without its digest, are never deployment inputs.

Registry retention must preserve every digest named by a released image lock or run-provenance
record. Automated cleanup must not delete referenced digests, and the project never publishes a
floating `latest` tag.

For local verification, run:

```console
docker buildx build --load -f Containerfile.runtime -t 3t-pipeline-runtime:test .
docker inspect 3t-pipeline-runtime:test
docker run --rm --read-only --tmpfs /tmp 3t-pipeline-runtime:test runtime --help
```

The configured user must be numeric and nonzero, and the command must exit successfully with a
read-only root filesystem. Remove only the task-owned image afterward with
`docker image rm 3t-pipeline-runtime:test`; do not log out or modify Docker credentials or builders.
