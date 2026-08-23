# next-generation-pipeline-platform - Work Plan

## TL;DR (For humans)
**What you'll get:** A standalone, reusable Kubernetes experiment platform with a versioned workload contract, safe artifact publication, reproducible cluster bootstrap, immutable deployment assets, and evidence-backed operator workflows. It includes an isolated end-to-end proof and a read-only migration/cutover readiness package for the existing lab.

**Why this approach:** The portable application-to-platform contract is proven before any cluster automation, preventing LabCLIP assumptions from becoming platform APIs. Infrastructure then follows a one-owner-per-resource model, and completed results become visible only through a verified commit marker rather than an unsafe claim of multi-object atomicity.

**What it will NOT do:** It will not modify the existing LabCLIP or paper repositories, move/delete production data, take over live Kubernetes resources, rotate credentials, or perform a production cutover. It will not absorb training, dataset, metric, checkpoint, or experiment-tracking semantics, and it does not require Fleet or Rancher.

**Effort:** XL
**Risk:** High - this is a stateful ownership migration spanning workload compatibility, cluster bootstrap, GPU scheduling, secrets, object storage, and rollback boundaries.
**Decisions I made for you:** Python 3.12 with uv; a strict additive-only v1alpha1 API; separate platform and consumer images; Argo with a native publisher sidecar and exit finalizer; Ansible for host/bootstrap and Secrets; pinned Helm plus Kustomize with one field owner per object; GHCR digest-only release images plus a disposable loopback registry for isolated testing; one server plus agents by default or exactly three servers for HA; local fake-storage and ephemeral-cluster proof before any separately authorized lab action; optional Tailscale disabled; no Fleet.

Your next move: execute locally in the task-owned orphan worktree created by the first task. Remote default-branch initialization, push, and PR creation remain a later external-authority gate and do not block local implementation. Full execution detail follows below.

---

> TL;DR (machine): XL effort, high risk; deliver a frozen portable workload/runtime contract, safe commit-marker publication, pinned Ansible/Helm/Kustomize platform, immutable image/secret lifecycle, isolated-live proof, and non-mutating cutover/rollback readiness.

## Scope
### Must have
- A clean-room Python 3.12 distribution named `3t-clip-pipeline`, import package `three_t_clip_pipeline`, CLI `3t-pipeline`, deterministic `uv.lock`, and CI that runs formatting, lint, typing, unit, contract, render, and isolated smoke checks.
- A frozen `three-t-clip-pipeline/v1alpha1` workload schema, typed models, canonical JSON serialization, examples, and compatibility fixtures. The contract exposes only bundle, command, relative working directory, environment references, generic cache-object mappings, immutable workload image, resource request, timeouts, and output/publication requirements; it never parses training YAML or research semantics.
- A portable conformance slice delivered before cluster bootstrap: schema -> deterministic Argo renderer -> frozen platform init/main/native-sidecar/finalizer boundary -> fake-S3 isolated success and failure smoke.
- Generic runtime services for path-safe code bundles, verified S3/cache hydration, bounded and cancellable cache-profile acquisition, immutable run identity, incremental upload, final validation, commit-marker publication, and sanitized provenance.
- Publication semantics are marker-based, not whole-prefix atomicity: payload objects are immutable and may become visible independently; only `commit-results` may conditionally create `<run-prefix>/COMMITTED.json` after required-object validation and successful main completion; readers ignore prefixes without a valid marker. Cancellation, timeout, or validation failure leaves the attempt uncommitted. A conditional-create 412 is idempotent success only when the existing marker has the same run UID and canonical payload digest; otherwise the attempt fails with a collision and must not overwrite the marker.
- An Ansible bootstrap adapter for existing Ubuntu/NVIDIA hosts, pinned `k3s-io/k3s-ansible` collection `1.2.0`, k3s `v1.36.2+k3s1`, one-server-plus-N-agents default, exactly-three-server embedded-etcd HA option, and deterministic rejection of two-server HA.
- A single-owner deployment adapter: Argo Workflows chart `1.0.19` / app `v4.0.7`, NVIDIA device-plugin `v0.17.1`, NFD `0.18.3`, project resources rendered by Kustomize, optional Tailscale overlay disabled by default, and immutable image/chart/source locks.
- An Ansible Vault -> in-memory render -> Kubernetes Secret apply lifecycle with `no_log`, stdin/temporary-file-safe handling, restrictive permissions, k3s secrets encryption enabled at initial install, least-privilege RBAC, rotation procedure, and evidence/log redaction.
- Five distinct verification levels: offline schema/render checks; client-side Kubernetes validation; authenticated server-side dry-run/read-only preflight (including production-class read-only inspection); local fake-S3 integration; pinned ephemeral-cluster/MinIO isolated-live smoke. A real lab-cluster smoke and any production cutover are separate authorization gates and are not executed by this plan.
- A per-resource ownership ledger, pre-change inventory/freeze/backup/diff gates, PV/MinIO cutover readiness criteria, explicit rollback triggers, and pass/fail evidence that never treats `Retain` or `DirectoryOrCreate` as backup or mount proof.
- A compatibility ledger classifying portable invariants from `/mnt/data/lab_clip/pipeline/tests` separately from LabCLIP-only fixtures, with no imports or runtime dependency on `/mnt/data/lab_clip`.
- Operator commands for package validation, platform render, authenticated preflight, submit/watch/status/evidence, isolated smoke, bootstrap check/apply, and cutover readiness reporting.

### Resource ownership matrix
| Deterministic identity | Current owner/source before cutover | Desired field owner | Handoff state + mutation default | Required backup/diff evidence | Rollback owner |
| --- | --- | --- | --- | --- | --- |
| host selector `inventory:k3s_cluster`; `/etc/rancher/k3s/config.yaml`; `k3s*.service`; task-owned kubeconfig | manual LabCLIP runbooks/unknown host state | Ansible `k3s_adapter` | `legacy-owned -> frozen -> ready -> new-owned`; check-only by default | host facts, service args, datastore snapshot reference, config diff | Ansible/operator runbook |
| `apiextensions.k8s.io/v1/CustomResourceDefinition/*argoproj.io`; Helm release `argo/argo-workflows` | vendored kubectl manifests or mixed unknown ownership | Helm release `argo-workflows` | no adoption/apply; render and server-dry-run only until separately authorized | UID/spec/managedFields, CRD export, release lookup, field-manager diff | prior manifest/release owner |
| `apps/v1/Deployment/argo/workflow-controller`; `apps/v1/Deployment/argo/argo-server` and chart RBAC | LabCLIP raw manifests/mixed unknown | Helm release `argo-workflows` | freeze required; no mutation by default | UID/spec/managedFields, values/render diff, backup references | Helm rollback/prior owner |
| Helm releases `node-feature-discovery/node-feature-discovery`, `kube-system/nvidia-device-plugin` | manual/unknown | pinned NFD/NVIDIA Helm releases | preflight/render/server-dry-run only | node labels, DaemonSet/image IDs, Helm ownership diff | Helm rollback/prior owner |
| `v1/Namespace/three-t-pipeline`; `v1/ServiceAccount/three-t-pipeline/pipeline-runner`; matching RBAC/NetworkPolicy/ConfigMap selector `app.kubernetes.io/part-of=three-t-pipeline` | absent in clean cluster; inventory required elsewhere | Kustomize field manager `three-t-pipeline-platform` | create only in ephemeral QA; production no mutation | rendered inventory + server-dry-run | Kustomize/operator runbook |
| configured `storage.k8s.io/v1/StorageClass`, `v1/PersistentVolume`, `v1/PersistentVolumeClaim` names from overlay | LabCLIP raw manifests/manual state | Kustomize field manager `three-t-pipeline-storage` | doc/readiness only; no live apply | UID/spec/managedFields, `findmnt`, independent backup, immutable-field diff | prior storage owner |
| configured `apps/v1/Deployment` + `v1/Service` MinIO identities from overlay | LabCLIP raw manifests/manual MinIO | Kustomize field manager `three-t-pipeline-storage` | doc/readiness only; no live apply | UID/spec/managedFields, mounted-device proof, object count/bytes/checksums, restore rehearsal | prior MinIO operator |
| `v1/Secret/three-t-pipeline/{three-t-registry-pull,three-t-code-s3,three-t-data-s3,three-t-run-s3,three-t-minio-root}` | plaintext examples/manual Secrets | Ansible `platform_secrets` only | create/update only after encryption gate; never delete/rotate by default | key-name-only inventory, encryption-state proof, server-dry-run/redaction | Ansible/operator runbook |
| optional Tailscale chart/Ingress selector `platform.three-t.dev/optional=tailscale` | optional existing operator/unknown | disabled Helm/Kustomize overlay | absent by default; explicit enable only | ownership inventory + render/server-dry-run | prior operator |
| `argoproj.io/v1alpha1/Workflow/three-t-pipeline/<generated>` | absent/per-run | `3t-pipeline submit` client | only `submit` may create; production-class rejected | validated render, server-dry-run, run UID evidence | CLI/operator delete run only |
| S3 selector `<run-prefix>/**` and `<run-prefix>/COMMITTED.json` | absent/per-run | publisher owns payloads; `commit-results` solely owns marker | isolated prefix by default; immutable conditional writes | sorted object size/SHA-256 inventory and marker digest | status/evidence tooling; no default delete |

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No Ubuntu/PXE/MAAS/firmware provisioning, driver installation, general bare-metal lifecycle, multi-cluster control plane, distributed training, sweep engine, or post-training evaluation DAG.
- No model, optimizer, dataset split, metric, checkpoint-selection, W&B policy, or application configuration semantics in platform code; those remain consumer-owned.
- No edits, deletes, commits, branches, or runtime imports in `/mnt/data/lab_clip` or `/mnt/data/3t-clip`; both are read-only references.
- No Fleet, Rancher, Argo CD, Terraform, external-secrets controller, or mandatory Tailscale dependency.
- No plaintext secrets, secret CLI values, decoded secret evidence, mutable image tags, floating chart/source references, `latest`, or consumer-supplied platform helper code.
- No claim of whole-prefix S3 atomicity; no marker before all required objects validate; no reader acceptance of uncommitted prefixes.
- No unbounded cache-profile polling, lock held while sleeping, swallowed cancellation, or commit marker after timeout/cancellation.
- No default-path S3 mirroring/deletion, credential rotation, CRD/PV/PVC replacement, MinIO data copy/delete, k3s datastore restore, or live production cutover.
- No adoption of an existing Kubernetes object without a one-owner ledger row, freeze evidence, backup evidence, server-side diff, and explicit cutover authorization.
- No assertion that `Retain` is a backup, that `DirectoryOrCreate` proves the intended disk is mounted, or that printed logs prove successful scheduling/publication.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD with `pytest`, `pytest-cov`, `jsonschema`, `ruff`, `basedpyright`, `ansible-lint`, `yamllint`, `helm template/lint`, `kubectl kustomize`, and isolated fake-client/fake-S3 tests; implementation and tests remain in the same todo/commit.

### Frozen clean-host tool, source, and image lock
The executor writes these literal values to `ci/tools.lock.yaml` and `deploy/images.lock.yaml`; changing any value is a separate reviewed upgrade, not an implementation choice.

| Artifact | Exact immutable pin |
| --- | --- |
| Python/runtime base | Python `3.12.11`; `docker.io/library/python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7` |
| uv | `0.11.33`; Linux amd64 tar SHA-256 `aa9fca823c03289fb6e3460b3dc864f3ea895cafaf9b99247701a67b17d1b018` |
| Docker / Buildx | Docker Engine/CLI `29.4.0`; Buildx `0.34.1` Linux amd64 SHA-256 `f1332ddb9010bd0b72628266c3a906d9a6979848033df4c8d9bd2cd113bae12b` |
| kubectl / Kustomize | kubectl `v1.36.2` Linux amd64 SHA-256 `1e9045ec32bea85da43de85f0065358529ea7c7a152eca78154fba5b58c27d82`; use only its embedded Kustomize `v5.8.1` |
| Helm | `v4.2.3` Linux amd64 tar SHA-256 `e9b88b4ee95b18c706839c28d3a0220e5bc470e9cd9262410c90793c45ff8b7c` |
| Kind | binary `v0.31.0` Linux amd64 SHA-256 `eb244cbafcc157dff60cf68693c14c9a75c4e6e6fedaf9cd71c58117cb93e3fa`; node `docker.io/kindest/node:v1.35.0@sha256:4613778f3cfcd10e615029370f5786704559103cf27bef934597ba562b269661` |
| kubeconform | `v0.7.0` Linux amd64 tar SHA-256 `c31518ddd122663b3f3aa874cfe8178cb0988de944f29c74a0b9260920d115d3` |
| Kubernetes schemas | `yannh/kubernetes-json-schema` commit `5a69f8365c9d3ed7de997f5365e22481cf775fa2`, packaged `v1.36.2-standalone-strict` subset with checked-in per-file SHA-256 manifest |
| Argo schemas/source | Argo Workflows `v4.0.7` commit `9aeb47ce10339f4a14819335c6a00027353ba0df`; packaged full Workflow CRD OpenAPI schema with checked-in SHA-256 manifest |
| Ansible collections | `kubernetes.core==6.5.0` source commit `0f472b53e2ee73e11b5f9067ab0826d76183c157`; `k3s.orchestration` tag `1.2.0` source commit `2c3f3773c704bd00bf7f6fc340cac8ab7ce9121b` |
| k3s | `v1.36.2+k3s1`; Linux amd64 `k3s` SHA-256 `65a55ec56c24eab44383086166ec620a491952b7e23941a49ddca6e8a4c4b4de` |
| Helm chart archives | Argo Workflows `1.0.19` SHA-256 `11910d3586c6737df04dbb2e48c373bc88cf5673d6bb17d7d421565495897da8`; NFD `0.18.3` SHA-256 `83a327ad61545bc89b319e94f5175ea4d265c9b6ffaa2832af2a0a85389409de`; NVIDIA device-plugin `0.17.1` SHA-256 `542ce451ce5d4611df00959e61c5638b7deeaf65c13daf03914d4688f94ae555` |
| Local registry / MinIO | `docker.io/library/registry:2.8.3@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373`; `docker.io/minio/minio:RELEASE.2025-04-22T22-12-26Z@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e` |
| Argo images | controller `quay.io/argoproj/workflow-controller:v4.0.7@sha256:8e3ca93350c18348e50cdb1899f37d672f9995d9bf51412d86dee52da22fff19`; server/CLI `quay.io/argoproj/argocli:v4.0.7@sha256:8c141b1acd26df3de70724aedaae0ee5a7d361cdf744a1daa6a1596f205cee50`; executor `quay.io/argoproj/argoexec:v4.0.7@sha256:eb2a7ca4d678a0c8c4f2de44f815f02d9eb12ac4609855f897744139aef220b4`; CRD job `registry.k8s.io/kubectl:v1.36.2@sha256:b0d792e0d8dfb9bb1b922b78b23137e2a34bb6f9667640353a9d2aadd1fd7761` |
| GPU discovery images | `registry.k8s.io/nfd/node-feature-discovery:v0.18.3@sha256:f9ef2ebee55141a1758d3c0a87bb701f5db2adf6856f7218b11bc2bac7b63862`; `nvcr.io/nvidia/k8s-device-plugin:v0.17.1@sha256:af31e2b7c7f89834c4e5219860def7ac2e49a207b3d4e8610d5a26772b7738e5` |
| GitHub Actions | `actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd`; `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`; `astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9`; `docker/setup-docker-action@77e84dbf09b47d1e29270283c22f16145aa85ca1`; `docker/setup-buildx-action@bb05f3f5519dd87d3ba754cc423b652a5edd6d2c`; `docker/build-push-action@f9f3042f7e2789586610d6e8b85c8f03e5195baf`; `docker/login-action@dbcb813823bdd20940b903addbd779551569679f` |

- Quality gate: `uv lock --check && uv sync --locked --all-groups && uv run ruff format --check . && uv run ruff check . && uv run basedpyright && uv run pytest -q --cov=three_t_clip_pipeline --cov-report=term-missing --cov-fail-under=90`.
- Deployment gate: render-only commands run without kubeconfig; client validation uses only the packaged checksum-verified kubeconform/Kubernetes/Argo schemas and never kubectl; authenticated server validation alone uses `kubectl apply --server-side --dry-run=server`. Server preflight may inspect any explicitly named context but stays read-only. Mutation decisions use the required `--environment-class` value rather than context-name heuristics: `production` categorically rejects apply/submit/smoke, while `ephemeral` is the only class allowed by automated isolated-live QA. A separate authorization artifact would be required for lab mutation, and this plan neither defines nor creates one.
- Test classes: register `unit`, `contract`, `render`, `client`, `server`, `integration`, and `isolated_live`. PR CI runs through `client` and local `integration`; `server` requires an explicitly configured read-only environment; `isolated_live` creates its own pinned ephemeral cluster. Production cutover is not a test class.
- Evidence: every QA command creates `.omo/evidence/` and writes `.omo/evidence/task-<N>-<slug>.<ext>` plus an `-error` artifact for failure paths. Evidence collectors redact Secret values, tokens, credentials, kubeconfigs, environment dumps, and signed URLs.
- Adversarial policy: applicable ultraqa classes are named per task; a task fails on `misleading_success_output`, `stale_state`, `dirty_worktree`, `path_traversal`, `secret_exfiltration`, `timeout_or_cancellation`, `partial_publication`, `ownership_conflict`, or `version_drift` when that class applies.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

Local bootstrap (Task 1, before any product-file write): the shared repository may keep its unborn local `develop` branch. On the verified local Git 2.43 CLI, `--orphan` is a flag and `-b` must not be combined with it in this plan: run `git -C /mnt/data/3t-clip-pipeline worktree add --lock --reason next-generation-pipeline-platform --orphan /mnt/data/3t-clip-pipeline/.worktrees/next-generation-pipeline-platform`, then run `git -C /mnt/data/3t-clip-pipeline/.worktrees/next-generation-pipeline-platform switch --orphan codex/next-generation-pipeline-platform`. Task 1 performs every ref/path/worktree preflight before touching `.git/info/exclude`, creates the task `.omo/plans` directory, descriptor-copies and digest-verifies the literal approved plan source, and rolls back partial bootstrap metadata on failure. This creates no remote ref, leaves shared `develop` unborn, and requires no network. Remote HEAD/default-branch creation, push, and PR are explicitly deferred to the external-authority gate in Task 15.

Wave 1 - foundation and frozen compatibility seams:
- Task 1 first: standalone Python/uv package foundation.
- Tasks 2 and 3 then run in parallel: frozen `v1alpha1` contract/schema and ownership/test-classification ledgers.

Wave 2 - milestone 1, portable contract conformance slice (Tasks 4-6):
- Task 4 depends [1,2]: deterministic renderer and offline/client validation.
- Task 5 depends [1,2]: platform init/main/sidecar/finalizer runtime boundaries.
- Task 6 depends [4,5]: fake-S3 isolated slice smoke; milestone 1 is complete only when both success and corrupt/cancel cases pass.

Wave 3 - milestone 2, bootstrap/deployment adapter (Tasks 7-9, parallel after Task 6):
- Task 7 depends [1,3,6]: Ansible host/k3s bootstrap.
- Task 8 depends [3,4,6]: pinned Helm/Kustomize platform adapter.
- Task 9 depends [1,3,6]: Vault-to-Kubernetes-Secret lifecycle.

Wave 4 - platform services and supply chain (Tasks 10-12, parallel after milestone 2):
- Task 10 depends [4,7,8,9]: CLI/API mutation boundaries and evidence.
- Task 11 depends [2,5,7,8,9]: bundles, storage, cache/profile, publication/provenance services.
- Task 12 depends [1,5,8,9]: runtime image, GHCR digest lifecycle, and CI gates.

Wave 5 - compatibility, isolated integration, and operator handoff (Tasks 13-15, parallel after Wave 4):
- Task 13 depends [3,10,11]: portable invariant ports and consumer example.
- Task 14 depends [7,8,9,10,11,12]: isolated Kubernetes/S3 smoke plus PV/MinIO cutover/rollback readiness gates; no production mutation.
- Task 15 depends [3,7,8,9,10,11,12]: runbooks, migration/ownership handoff, and release/PR readiness.

Critical path: local orphan-worktree bootstrap in Task 1 -> 2 -> (4 || 5) -> 6 -> 8 -> 10 -> 14 -> F1-F4; the late remote-authority/PR gate is outside and does not block this implementation path.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 2, 3, 4, 5, 7, 9, 12 | none |
| 2 | 1 | 4, 5, 6, 11 | 3 |
| 3 | 1 | 7, 8, 9, 13, 15 | 2 |
| 4 | 1, 2 | 6, 8, 10 | 5 |
| 5 | 1, 2 | 6, 11, 12 | 4 |
| 6 | 4, 5 | 7, 8, 9 | none |
| 7 | 1, 3, 6 | 10, 11, 14, 15 | 8, 9 |
| 8 | 3, 4, 6 | 10, 11, 12, 14, 15 | 7, 9 |
| 9 | 1, 3, 6 | 10, 11, 12, 14, 15 | 7, 8 |
| 10 | 4, 7, 8, 9 | 13, 14 | 11, 12 |
| 11 | 2, 5, 7, 8, 9 | 13, 14, 15 | 10, 12 |
| 12 | 1, 5, 8, 9 | 14, 15 | 10, 11 |
| 13 | 3, 10, 11 | F1-F4 | 14, 15 |
| 14 | 7, 8, 9, 10, 11, 12 | F1-F4 | 13, 15 |
| 15 | 3, 7, 8, 9, 10, 11, 12 | F1-F4 | 13, 14 |

## Todos
> Implementation + Test = ONE todo. Never separate.
- [ ] 1. Bootstrap the unborn repository locally and establish the Python package

  Owned files/directories: `.omo/plans/next-generation-pipeline-platform.md` (copied unchanged into the task worktree), `scripts/bootstrap-local-worktree.sh`, `ci/tools.lock.yaml`, `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `Makefile`, `README.md`, `src/three_t_clip_pipeline/__init__.py`, `src/three_t_clip_pipeline/cli.py`, `tests/conftest.py`, `tests/test_repository_bootstrap.py`, `tests/test_package.py`. Local Git metadata ownership: `/mnt/data/3t-clip-pipeline/.git/info/exclude` and the registered worktree entry only; neither is committed.

  What to do: perform a network-free local bootstrap from the known unborn shared branch. Set literal values `SHARED=/mnt/data/3t-clip-pipeline`, `SOURCE=/mnt/data/3t-clip-pipeline/.omo/plans/next-generation-pipeline-platform.md`, `TASK=/mnt/data/3t-clip-pipeline/.worktrees/next-generation-pipeline-platform`, `DERIVED_BRANCH=next-generation-pipeline-platform`, `BRANCH=codex/next-generation-pipeline-platform`, `DEST=$TASK/.omo/plans/next-generation-pipeline-platform.md`, and `EXCLUDE="$(git -C "$SHARED" rev-parse --absolute-git-dir)/info/exclude"`. Before changing `$EXCLUDE` or creating a directory, require Git `2.43.x`, `git -C "$SHARED" symbolic-ref --short HEAD` equals `develop`, `git -C "$SHARED" rev-parse --verify HEAD` fails, `git -C "$SHARED" show-ref --verify --quiet refs/heads/$BRANCH` fails, `git -C "$SHARED" show-ref --verify --quiet refs/heads/$DERIVED_BRANCH` fails, `test ! -e "$TASK"`, and `git -C "$SHARED" worktree list --porcelain` contains neither `worktree $TASK` nor either branch ref. Save the exact pre-change `$EXCLUDE` bytes and mode in a task-owned temporary directory. Only after all preflights pass, append one `.worktrees/` line if absent, create `$SHARED/.worktrees`, run `git -C "$SHARED" worktree add --lock --reason next-generation-pipeline-platform --orphan "$TASK"`, and immediately run `git -C "$TASK" switch --orphan "$BRANCH"`; do not use `-b`. This exact two-step form follows the locally verified Git 2.43 help signatures `git worktree add ... [--orphan] <path>` and `git switch --orphan <new-branch>`. Run `mkdir -p "$TASK/.omo/plans"` before copying. Descriptor-open `$SHARED`, then `.omo`, `plans`, and the literal source filename with `O_NOFOLLOW`, directory `fstat` checks, and a regular-file final check; copy bytes from that same final descriptor to `$DEST`, `fsync`, descriptor-open the destination through `$TASK/.omo/plans` with the same checks, and require source/destination SHA-256 and byte count equality before any package write. A bootstrap trap handles ERR/INT/TERM: if the task worktree was registered, first run `git -C "$SHARED" worktree unlock "$TASK"`, then remove only that uncommitted task worktree with `git -C "$SHARED" worktree remove --force "$TASK"`; restore `$EXCLUDE` bytes/mode; delete `$SHARED/.worktrees` only if empty; run `git -C "$SHARED" worktree prune`; and assert both branch refs, task path, and worktree record are absent. If unlock reports already-unlocked, continue; any other cleanup error is surfaced and the script reports the exact residual path/ref rather than deleting broadly. Clear the trap only after descriptor validation succeeds. Then create all package files in `$TASK` and commit Task 1 as the root commit of the task branch; shared `develop` remains unborn. Check in `scripts/bootstrap-local-worktree.sh` with this exact guarded/rollback algorithm and test it against temporary unborn repositories, symlinked source/destination ancestors, occupied paths, pre-existing refs, and injected failures after each mutation. Materialize every literal binary/action/schema-source pin from the Frozen clean-host lock table in `ci/tools.lock.yaml`. Define Python `>=3.12,<3.13`, distribution `3t-clip-pipeline`, package `three_t_clip_pipeline`, and console script `3t-pipeline`. Use Pydantic v2, PyYAML, boto3/botocore, and the Kubernetes Python client as runtime dependencies; keep pytest/coverage, ruff, basedpyright, jsonschema, ansible-core/ansible-lint, and yamllint in locked development groups. Add make targets `lock-check`, `sync`, `quality`, `test`, `render`, and `smoke`; `lock-check` runs `uv lock --check`, `sync` runs `uv sync --locked`, and no quality target changes `uv.lock`.

  Must NOT do: do not use `git worktree add --orphan -b`, `git commit-tree`, `git update-ref refs/heads/develop`, a commit on shared `develop`, path-based validate-then-open hashing, a symlink-following copy, `git ls-remote`, fetch, push, remote HEAD/default-branch mutation, or a standard `git worktree add <path> HEAD` that cannot work from unborn HEAD. Never remove a pre-existing worktree/path/ref during rollback. Do not copy `/mnt/data/lab_clip/pipeline/requirements.txt`, CUDA/OpenCLIP/training dependencies, application code, credentials, local virtual environments, or a mutable dependency range without a lock.

  Parallelization: Can parallel: NO | Wave 1 | Blocks: [2,3,4,5,7,9,12] | Blocked by: [none].

  References (executor has NO interview context):
  - Pattern: `/mnt/data/lab_clip/pyproject.toml:1-48` - current uv/build metadata shape, but not its LabCLIP dependency surface.
  - Anti-pattern: `/mnt/data/lab_clip/pipeline/requirements.txt:1-5,27-33,125-126` - full consumer ML stack that must not be copied.
  - Pattern: `/mnt/data/lab_clip/pipeline/Dockerfile:1-29` - existing broad runtime boundary to replace with a small platform package/image.
  - External: `https://git-scm.com/docs/git-worktree` and `https://git-scm.com/docs/git-switch` - locally verified two-step `worktree add --orphan <path>` then `switch --orphan <new-branch>` semantics for an unborn repository.
  - External: `https://docs.astral.sh/uv/concepts/projects/sync/` and `https://docs.astral.sh/uv/concepts/projects/layout/#the-lockfile` - `uv lock --check` and `uv sync --locked` consistency semantics.

  Acceptance criteria (agent-executable only):
  - [ ] From `$TASK`, `test "$(git symbolic-ref --short HEAD)" = codex/next-generation-pipeline-platform`, `git -C "$SHARED" worktree list --porcelain` records `$TASK`/the task branch as locked, and `git -C "$SHARED" rev-parse --verify refs/heads/develop` still fails; the script contains the literal `$SOURCE`, both exact `show-ref --verify --quiet` preflights before the first info/exclude mutation, `mkdir -p "$TASK/.omo/plans"`, descriptor-chain source/destination hashing, and no remote operation.
  - [ ] `uv lock --check && uv sync --locked --all-groups` exits 0 from a clean task checkout without changing `uv.lock`, and `uv run python -c "import three_t_clip_pipeline; print(three_t_clip_pipeline.__version__)"` prints the project version.
  - [ ] `uv run 3t-pipeline --help` exits 0 and lists only platform command groups; `uv tree | rg -i 'torch|open_clip|wandb|pandas'` exits 1.
  - [ ] `uv run pytest -q tests/test_repository_bootstrap.py tests/test_package.py` proves the exact no-`-b` orphan-worktree/switch algorithm on a temporary unborn `develop`, deterministic refusal before mutation when the task/derived ref or path/worktree exists, descriptor rejection of symlinked ancestors/final files, byte-identical digest copy, cleanup after each injected partial failure, distribution/import/CLI names, Python range, locked dependency groups, and absence of LabCLIP imports.

  QA scenarios:
  ```
  Scenario: unborn local develop yields a locked task worktree and clean package
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; (test "$(git symbolic-ref --short HEAD)" = codex/next-generation-pipeline-platform && ! git -C /mnt/data/3t-clip-pipeline rev-parse --verify refs/heads/develop && uv lock --check && uv sync --locked --all-groups && uv run 3t-pipeline --help && uv run pytest -q tests/test_repository_bootstrap.py tests/test_package.py) 2>&1 | tee .omo/evidence/task-1-package-foundation.txt
    Expected: exit 0; the locked task worktree/branch exists via the no-`-b` flow, shared `develop` remains unborn, descriptor-chain source/destination digests and byte counts match, help names `3t-pipeline`, tests pass, and `uv.lock` is unchanged.
    Evidence: .omo/evidence/task-1-package-foundation.txt

  Scenario: existing worktree target and lock drift are rejected without remote writes
    Tool: bash
    Steps: set -euo pipefail; cp pyproject.toml /tmp/task-1-pyproject.toml; trap 'mv /tmp/task-1-pyproject.toml pyproject.toml' EXIT; uv run pytest -q tests/test_repository_bootstrap.py::test_existing_target_refuses_before_mutation; sed -i '/dependencies = \[/a\  "torch",' pyproject.toml; if uv lock --check >.omo/evidence/task-1-package-foundation-error.txt 2>&1; then echo UNEXPECTED_LOCK_ACCEPTANCE | tee -a .omo/evidence/task-1-package-foundation-error.txt; exit 1; else echo EXPECTED_LOCK_REJECTION | tee -a .omo/evidence/task-1-package-foundation-error.txt; fi
    Expected: the bootstrap fixture refuses occupied refs/path/worktree before changing Git metadata and restores the original info/exclude/worktree state after injected partial failure; `uv lock --check` rejects pyproject/lock drift; the trap restores the file; network/remote call count is zero.
    Evidence: .omo/evidence/task-1-package-foundation-error.txt
  ```

  UltraQA adversarial classes: `dirty_worktree`, `version_drift`, `stale_state`. Cleanup: remove `.venv`, `.pytest_cache`, `.ruff_cache`, temporary build artifacts, and restore any QA-mutated file before commit; assert `git status --short` contains only owned files.

  Commit: YES | Message: `chore(package): initialize standalone uv project` | Files: [`.omo/plans/next-generation-pipeline-platform.md`, `scripts/bootstrap-local-worktree.sh`, `ci/tools.lock.yaml`, `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `Makefile`, `README.md`, `src/three_t_clip_pipeline/`, `tests/conftest.py`, `tests/test_repository_bootstrap.py`, `tests/test_package.py`]

- [ ] 2. Freeze the `v1alpha1` workload contract and canonical schema

  Owned files/directories: `src/three_t_clip_pipeline/contract/`, `schemas/workload-v1alpha1.schema.json`, `examples/workload-minimal.yaml`, `tests/contract/`, `docs/architecture/workload-contract-v1alpha1.md`.

  What to do: implement one source of truth using strict Pydantic models and generate a checked-in JSON Schema with `additionalProperties: false` at every object. Freeze the exact required shape: `apiVersion: three-t-clip-pipeline/v1alpha1`; `kind: Workload`; `metadata.name`; `spec.bundle.{s3Uri,sha256}`; `spec.execution.{image,command,workingDirectory}`; optional `spec.environment[]` entries `{name,valueFrom}` where `valueFrom` is exactly one of `{secretKeyRef:{name,key},configMapKeyRef:{name,key},fieldRef:{fieldPath}}`; optional `spec.cacheMappings[]` entries `{s3Uri,destination,inventorySha256,readOnly}` with `readOnly` default true; `spec.resources.{cacheProfile,gpuResource,gpuCount,cpu,memory}` with `cacheProfile` default `auto`, `gpuResource` default `nvidia.com/gpu`, and `gpuCount` default 0; `spec.timeouts.{profileAcquireSeconds,activeDeadlineSeconds,terminationGraceSeconds,publicationFinalRetrySeconds}` with defaults `900,86400,120,300` and maxima `7200,604800,600,3600`; `spec.outputs.{bucket,prefix,requiredPaths}`. No other field is accepted. Canonical serialization is UTF-8 JSON with sorted keys, compact separators, trailing newline, defaults materialized, and SHA-256 identity. Explicitly document additive-only optional-field evolution inside v1alpha1 and require a new API version for required-field, semantic, or container-boundary changes.

  Must NOT do: no embedded secret values, shell-string command, absolute/traversal/symlink destinations, duplicate destinations/required paths, mutable image tag, boolean-as-integer resources, nonpositive timeouts, hard-coded datasets/nodes/buckets, training YAML parsing, W&B policy, or platform-helper path supplied by the consumer.

  Parallelization: Can parallel: YES | Wave 1 after Task 1 foundation is available | Blocks: [4,5,6,11] | Blocked by: [1].

  References:
  - Pattern: `/mnt/data/lab_clip/pipeline/core/submit_core.py:150-187` - path rejection behavior to generalize without `train/` coupling.
  - Pattern: `/mnt/data/lab_clip/pipeline/core/dataset_cache.py:108-123,174-225` - safe relative paths, strict digest/size validation, canonical inventory serialization.
  - Pattern: `/mnt/data/lab_clip/pipeline/core/code_bundle.py:105-218` - bundle identity and secret/path exclusions.
  - Anti-pattern: `/mnt/data/lab_clip/pipeline/core/hera/labclip_pipeline.py:399-468` - hard-coded datasets/env that stay outside the contract.
  - External: `https://json-schema.org/draft/2020-12/json-schema-core.html` - schema dialect and validation semantics.

  Acceptance criteria:
  - [ ] `uv run python -m three_t_clip_pipeline.contract.generate_schema --check schemas/workload-v1alpha1.schema.json` exits 0 and a second generation is byte-identical.
  - [ ] `uv run pytest -q tests/contract` covers the minimal example, canonical round-trip/digest, unknown-field rejection, every forbidden path/secret/image/resource/timing case, and compatibility snapshots.
  - [ ] `rg -n 'cuhk|icfg|rstpreid|vis-lab|labclip|wandb' src/three_t_clip_pipeline/contract schemas examples` returns no matches.

  QA scenarios:
  ```
  Scenario: valid workload round-trips canonically
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; uv run 3t-pipeline contract validate examples/workload-minimal.yaml --canonical-json .omo/evidence/task-2-workload-contract.json; uv run python -m three_t_clip_pipeline.contract.generate_schema --check schemas/workload-v1alpha1.schema.json 2>&1 | tee -a .omo/evidence/task-2-workload-contract.json
    Expected: exit 0; canonical JSON has the frozen apiVersion/kind, digest image, relative paths, and no secret values.
    Evidence: .omo/evidence/task-2-workload-contract.json

  Scenario: traversal, mutable image, and inline secret fail before I/O
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/contract/test_rejections.py -k 'traversal or mutable_image or inline_secret' 2>&1 | tee .omo/evidence/task-2-workload-contract-error.txt
    Expected: all parameterized cases pass by asserting stable validation codes `path_not_relative`, `image_not_immutable`, and `inline_secret_forbidden`; fake external clients record zero calls.
    Evidence: .omo/evidence/task-2-workload-contract-error.txt
  ```

  UltraQA adversarial classes: `path_traversal`, `secret_exfiltration`, `version_drift`, `prompt_injection`. Cleanup: remove generated scratch schemas/canonical files outside `.omo/evidence`; verify the checked-in schema matches the generator.

  Commit: YES | Message: `feat(contract): freeze v1alpha1 workload schema` | Files: [`src/three_t_clip_pipeline/contract/`, `schemas/`, `examples/workload-minimal.yaml`, `tests/contract/`, `docs/architecture/workload-contract-v1alpha1.md`]

- [ ] 3. Define resource ownership and portable-test classification ledgers

  Owned files/directories: `config/resource-ownership.yaml`, `schemas/resource-ownership.schema.json`, `docs/architecture/resource-ownership.md`, `docs/migration/labclip-test-classification.yaml`, `docs/migration/labclip-test-map.md`, `tests/policy/test_ownership.py`, `tests/policy/test_portability.py`.

  What to do: encode every resource row from the plan's ownership matrix with `{apiVersion,kind,namespace,name,owner,mechanism,adoptionPolicy,backupRequirement,rollbackOwner}` and reject duplicate GVK/name ownership. Inventory the LabCLIP pipeline tests into two exhaustive classes: portable invariants to reimplement (bundle safety, S3 verification, inventory/hydration, scheduler accounting, renderer lifecycle, publication retries/markers, secret redaction) and LabCLIP-only fixtures to parameterize or exclude (datasets, training entries, W&B policy, node/PVC/bucket/service names, `labclip.*` labels, five-fold rules, Tailscale hostnames). Add a policy test that fails if target source imports `/mnt/data/lab_clip`, `pipeline.*`, or research modules.

  Must NOT do: do not copy tests verbatim without provenance/classification, assign two owners to one resource, mark `Retain` as backup, define live adoption as default, or edit either reference repository.

  Parallelization: Can parallel: YES | Wave 1 | Blocks: [7,8,9,13,15] | Blocked by: [1].

  References:
  - Portable: `/mnt/data/lab_clip/pipeline/tests/test_code_bundle.py:41-91`, `test_result_sync.py:47-207`, `test_cache_profile.py:72-118`, `test_dataset_cache.py:1-617` - behavioral invariants.
  - LabCLIP fixtures: `/mnt/data/lab_clip/pipeline/tests/test_submit_core.py:9-126`, `test_local_cache_manifests.py:49-116`, `test_storage.py:1-160` - dataset/node/namespace/bucket specifics.
  - Resource source: `/mnt/data/lab_clip/pipeline/rbac.yaml:1-63`, `pipeline/k8s/cache/local-pv.yaml:1-87`, `pipeline/k8s/minio-code.yaml:1-110`, `pipeline/k8s/minio-ml-assets.yaml:1-108`.
  - Risk: `/mnt/data/lab_clip/pipeline/docs/cluster/12-upgrade-and-recovery.md:19-182` - inventory, backup, rollback ordering.

  Acceptance criteria:
  - [ ] `uv run pytest -q tests/policy/test_ownership.py tests/policy/test_portability.py` asserts schema validity, one owner per resource, all matrix rows present, every discovered reference test classified exactly once, and no target-source imports from LabCLIP.
  - [ ] `uv run python -m three_t_clip_pipeline.policy.check_ownership config/resource-ownership.yaml` prints `OWNERSHIP_OK` and fails on a duplicate fixture with `ownership_conflict`.
  - [ ] `git -C /mnt/data/lab_clip status --short` and `git -C /mnt/data/3t-clip status --short` match snapshots captured before the task.

  QA scenarios:
  ```
  Scenario: exhaustive single-owner and test classification passes
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; uv run pytest -q tests/policy 2>&1 | tee .omo/evidence/task-3-ownership-portability.txt
    Expected: exit 0; every resource and every referenced LabCLIP pipeline test has exactly one class/owner.
    Evidence: .omo/evidence/task-3-ownership-portability.txt

  Scenario: duplicate owner and unclassified fixture are rejected
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/policy/test_ownership.py::test_duplicate_owner_rejected tests/policy/test_portability.py::test_unclassified_reference_rejected 2>&1 | tee .omo/evidence/task-3-ownership-portability-error.txt
    Expected: tests pass by observing `ownership_conflict` and `unclassified_reference_test`; no reference repo changes.
    Evidence: .omo/evidence/task-3-ownership-portability-error.txt
  ```

  UltraQA adversarial classes: `ownership_conflict`, `dirty_worktree`, `stale_state`, `misleading_success_output`. Cleanup: remove any generated inventory snapshots outside evidence; compare reference-repo status to pre-task capture.

  Commit: YES | Message: `docs(architecture): define ownership and portability ledgers` | Files: [`config/resource-ownership.yaml`, `schemas/resource-ownership.schema.json`, `docs/architecture/`, `docs/migration/`, `tests/policy/`]

- [ ] 4. Render deterministic Argo workflows with offline and client validation

  Owned files/directories: `src/three_t_clip_pipeline/render/`, `scripts/bootstrap-client-validator.sh`, `scripts/validate-manifest-local.sh`, `schemas/kubernetes/v1.36.2-standalone-strict/`, `schemas/argo/v4.0.7/`, `schemas/schema-sources.lock.yaml`, `tests/render/`, `tests/golden/workflow-v1alpha1.yaml`, `docs/architecture/rendering.md`.

  What to do: map only the validated `v1alpha1` model into deterministic `argoproj.io/v1alpha1` Workflow YAML. Render the frozen DAG `preflight -> acquire-profile -> prepare-cache -> run`, plus `onExit: commit-results`; set service account, namespace, run UID labels, active deadline, retry/backoff, volume contracts, immutable platform/workload images, Secret/ConfigMap refs, and native-sidecar syntax. Provide `3t-pipeline plan WORKLOAD --output -` as offline render-only. Implement client validation with the packaged kubeconform `v0.7.0` binary whose Linux amd64 archive SHA-256 is `c31518ddd122663b3f3aa874cfe8178cb0988de944f29c74a0b9260920d115d3`, installed only at `$PWD/.cache/tools/kubeconform-v0.7.0` by `scripts/bootstrap-client-validator.sh`. Package the required Kubernetes strict schemas from commit `5a69f8365c9d3ed7de997f5365e22481cf775fa2` under `schemas/kubernetes/v1.36.2-standalone-strict/` and the Argo Workflow `v4.0.7` schema extracted from commit `9aeb47ce10339f4a14819335c6a00027353ba0df` under `schemas/argo/v4.0.7/`; `schemas/schema-sources.lock.yaml` records those commits and every packaged file SHA-256. `scripts/validate-manifest-local.sh` runs exactly `$PWD/.cache/tools/kubeconform-v0.7.0 -strict -summary -exit-on-error -ignore-missing-schemas=false -kubernetes-version 1.36.2 -schema-location "$PWD/schemas/kubernetes/v1.36.2-standalone-strict/{{.ResourceKind}}{{.KindSuffix}}.json" -schema-location "$PWD/schemas/argo/v4.0.7/{{.ResourceKind}}-{{.ResourceAPIVersion}}.json" "$MANIFEST"`. `3t-pipeline validate --client` invokes only that wrapper. Sort emitted mappings/lists where semantics allow and snapshot the byte output. Reserve `kubectl apply --server-side --dry-run=server` exclusively for Task 10's authenticated server level.

  Must NOT do: no kubectl invocation at client level, default/remote kubeconform schema location, network fetch during validation, submit/apply, cluster discovery, credential resolution, current-context access, dynamic timestamps, mutable images, LabCLIP WorkflowTemplate name/parameters, skipped/missing schema, or workload print output treated as validation.

  Parallelization: Can parallel: YES | Wave 2 | Blocks: [6,8,10] | Blocked by: [1,2].

  References:
  - Pattern: `/mnt/data/lab_clip/pipeline/core/submit_core.py:523-578` - Workflow submit-manifest construction and temporary-file cleanup.
  - Pattern: `/mnt/data/lab_clip/pipeline/core/hera/labclip_pipeline.py:630-727` - DAG order, exit handler, and scheduling skeleton to generalize.
  - Tests: `/mnt/data/lab_clip/pipeline/tests/test_train_terminal_dag.py:1-220`, `test_argo_workflows_manifests.py:1-180` - lifecycle/render assertions.
  - External: `https://github.com/yannh/kubeconform/releases/tag/v0.7.0` - pinned packaged validator and strict local schema-location contract.
  - External: `https://argo-workflows.readthedocs.io/en/latest/workflow-concepts/` and `https://kubernetes.io/docs/tasks/manage-kubernetes-objects/declarative-config/#how-to-create-objects`.

  Acceptance criteria:
  - [ ] Two runs of `uv run 3t-pipeline plan examples/workload-minimal.yaml --output /tmp/workflow.yaml` produce the same SHA-256 and match `tests/golden/workflow-v1alpha1.yaml`.
  - [ ] `env -u KUBECONFIG uv run pytest -q tests/render` proves offline render makes zero Kubernetes/S3/network calls, the client validator invokes only the checksum-verified packaged kubeconform binary/local schema directories, every schema lock hash matches, and kubectl call count is zero.
  - [ ] Render tests assert the exact DAG and init/main/sidecar/finalizer names, images by digest, Secret refs, timeouts, volumes, retry rules, and no LabCLIP-specific token.

  QA scenarios:
  ```
  Scenario: minimal contract renders and validates locally
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; env -u KUBECONFIG uv run 3t-pipeline plan examples/workload-minimal.yaml --output .omo/evidence/task-4-render.yaml; env -u KUBECONFIG uv run 3t-pipeline validate examples/workload-minimal.yaml --client 2>&1 | tee .omo/evidence/task-4-render.txt
    Expected: both exit 0; rendered workflow equals golden bytes; packaged kubeconform validates Kubernetes `1.36.2` plus Argo `v4.0.7` schemas entirely locally and reports `CLIENT_VALID`; kubectl/network call counts are zero.
    Evidence: .omo/evidence/task-4-render.yaml

  Scenario: renderer rejects unknown API, missing schema, and mutable image locally
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/render/test_rejections.py -k 'unknown_api or missing_schema or mutable_image or schema_hash_drift' 2>&1 | tee .omo/evidence/task-4-render-error.txt
    Expected: tests pass with stable contract/schema errors; packaged validator refuses missing/drifted schemas; fake kubectl, Kubernetes client, and network call counts remain zero.
    Evidence: .omo/evidence/task-4-render-error.txt
  ```

  UltraQA adversarial classes: `misleading_success_output`, `stale_state`, `version_drift`, `secret_exfiltration`. Cleanup: delete `/tmp/workflow.yaml`; keep only redacted/golden evidence and restore any generated golden before commit.

  Commit: YES | Message: `feat(render): add deterministic Argo workflow renderer` | Files: [`src/three_t_clip_pipeline/render/`, `scripts/bootstrap-client-validator.sh`, `scripts/validate-manifest-local.sh`, `schemas/kubernetes/v1.36.2-standalone-strict/`, `schemas/argo/v4.0.7/`, `schemas/schema-sources.lock.yaml`, `tests/render/`, `tests/golden/workflow-v1alpha1.yaml`, `docs/architecture/rendering.md`]

- [ ] 5. Implement the frozen platform init, main, sidecar, and finalizer boundary

  Owned files/directories: `src/three_t_clip_pipeline/runtime/entrypoint.py`, `src/three_t_clip_pipeline/runtime/lifecycle.py`, `src/three_t_clip_pipeline/runtime/status.py`, `tests/runtime/test_lifecycle.py`, `tests/runtime/test_signals.py`, `docs/architecture/runtime-boundary.md`.

  What to do: expose platform-image subcommands `runtime init`, `runtime publisher`, and `runtime finalize`. Freeze four rendered roles and their exact mounts: `bundle-init` is a regular platform-image init container with read-write `emptyDir` `input` at `/workspace/input`, read-write PVC `cache` at `/workspace/cache`, and read-write `emptyDir` `control` at `/workspace/control`; it verifies/downloads the bundle and declared cache objects, writes `init.json`, uploads that manifest to the staging prefix, then exits 0 before main. `main` is the consumer image/argv with `input` and `cache` mounted read-only and `output` `emptyDir` mounted read-write at `/workspace/output`; it has no platform package/binary dependency. `artifact-publisher` is the platform image as a native sidecar under `initContainers` with `restartPolicy: Always`, `output` read-only and `control` read-write; it incrementally uploads, traps SIGTERM, stops discovery, performs a final flush bounded by `publicationFinalRetrySeconds` and `terminationGraceSeconds`, uploads `publication.json`, and never writes the marker. `commit-results` is a separate Argo `onExit` platform-image pod, not a shared-volume peer; it reads the stable Workflow status plus uploaded init/publication manifests from S3, HEAD-verifies all required objects, and is the sole conditional `COMMITTED.json` writer. Kubernetes terminates `artifact-publisher` only after `main`; timeout/cancel writes cancellation status and `commit-results` refuses commit.

  Must NOT do: do not inject platform Python into the consumer bundle/image, execute shell strings, let the sidecar create the marker, publish after cancellation, treat sidecar termination as main success, rely on prefix atomicity, or allow unbounded TERM/final-flush time.

  Parallelization: Can parallel: YES | Wave 2 | Blocks: [6,11,12] | Blocked by: [1,2].

  References:
  - Pattern: `/mnt/data/lab_clip/pipeline/core/hera/utils.py:25-129,237-286,303-438` - current init/environment/sidecar mechanics.
  - Pattern: `/mnt/data/lab_clip/pipeline/core/hera/labclip_pipeline.py:454-532,541-626` - main status, metadata, and exit publication.
  - Pattern: `/mnt/data/lab_clip/pipeline/core/result_sync.py:40-83,120-207` - filtering, retries, completion/failure status.
  - External: `https://kubernetes.io/docs/concepts/workloads/pods/init-containers/`, `https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/`, `https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/`.

  Acceptance criteria:
  - [ ] `uv run pytest -q tests/runtime` proves strict phase ordering, read-only input, main-output separation, publisher filtering, exactly one finalizer marker attempt, and no marker for main failure/cancel/timeout/corrupt object.
  - [ ] Signal tests deliver SIGTERM during upload and assert bounded exit within `terminationGraceSeconds`, `publication_state=cancelled|failed`, preserved diagnostics, and zero marker calls.
  - [ ] Renderer/runtime constants are cross-tested so exact names `bundle-init`, `main`, `artifact-publisher`, and `commit-results`, mount modes, S3 manifest keys, SIGTERM ordering, time bounds, and exit-status mappings cannot drift.

  QA scenarios:
  ```
  Scenario: successful lifecycle uploads then commits once
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; uv run pytest -q tests/runtime/test_lifecycle.py::test_successful_boundary -vv 2>&1 | tee .omo/evidence/task-5-runtime-boundary.txt
    Expected: init precedes main; sidecar uploads validated objects; finalizer performs one conditional marker create only after main success.
    Evidence: .omo/evidence/task-5-runtime-boundary.txt

  Scenario: TERM during upload leaves diagnostics but no commit
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/runtime/test_signals.py::test_sigterm_cancels_bounded_without_commit -vv 2>&1 | tee .omo/evidence/task-5-runtime-boundary-error.txt
    Expected: process exits within configured grace; partial objects may exist; `COMMITTED.json` is absent and cancellation metadata is present.
    Evidence: .omo/evidence/task-5-runtime-boundary-error.txt
  ```

  UltraQA adversarial classes: `timeout_or_cancellation`, `partial_publication`, `misleading_success_output`, `path_traversal`. Cleanup: terminate spawned test processes, remove temporary workspaces, and assert no lingering publisher PID.

  Commit: YES | Message: `feat(runtime): freeze init main sidecar finalizer boundary` | Files: [`src/three_t_clip_pipeline/runtime/`, `tests/runtime/`, `docs/architecture/runtime-boundary.md`]

- [ ] 6. Prove milestone 1 with a fake-S3 portable conformance smoke

  Owned files/directories: `tests/fakes/s3.py`, `tests/smoke/test_portable_contract_slice.py`, `scripts/smoke-portable.sh`, `examples/consumer/hello/`, `docs/testing/portable-slice.md`.

  What to do: build an isolated smoke that validates the example, renders the workflow, executes the init/main/publisher/finalizer state machines in temporary directories against a deterministic fake S3 implementing head/get/put, metadata checks, `If-None-Match: *`, 412 conflict, corruption, delay, and cancellation. The hello consumer writes a required result and provenance file. Assert readers return only a run whose commit marker schema/digest matches the uploaded immutable object set.

  Must NOT do: no Docker daemon, kubeconfig, real network, real S3/MinIO, live namespace, sleep-based nondeterminism, whole-prefix atomicity claim, or success based only on printed output.

  Parallelization: Can parallel: NO | Wave 2 milestone gate | Blocks: [7,8,9] | Blocked by: [4,5].

  References:
  - Pattern: `/mnt/data/lab_clip/pipeline/tests/test_result_sync.py:11-43,76-207` - fake S3, deterministic clock, retry/failure testing.
  - Pattern: `/mnt/data/lab_clip/pipeline/tests/test_s3_integrity.py:40-120,300-329` - object metadata/download integrity fakes.
  - Pattern: `/mnt/data/lab_clip/pipeline/core/dataset_cache.py:338-404` - publish objects before terminal inventory; adapt to commit-marker semantics.
  - External: `https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html` - conditional marker creation.

  Acceptance criteria:
  - [ ] `env -u KUBECONFIG ./scripts/smoke-portable.sh` exits 0, records canonical workload/workflow/run/marker digests, and proves zero network/Kubernetes calls.
  - [ ] Failure matrix covers corrupt download, missing required output, main nonzero, marker 412 race, upload timeout, and cancellation; every case preserves diagnostics and has no accepted committed run except the single 412 winner.
  - [ ] `for i in $(seq 1 10); do uv run pytest -q tests/smoke/test_portable_contract_slice.py || exit 1; done` is deterministic across repeated runs.

  QA scenarios:
  ```
  Scenario: complete portable slice produces one readable commit
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; env -u KUBECONFIG ./scripts/smoke-portable.sh --case success --json .omo/evidence/task-6-portable-smoke.json
    Expected: exit 0; JSON reports validated schema/render/runtime phases, required object hashes, one conditional marker, and reader status `committed`.
    Evidence: .omo/evidence/task-6-portable-smoke.json

  Scenario: corruption and cancellation never commit
    Tool: bash
    Steps: set -euo pipefail; ./scripts/smoke-portable.sh --case corrupt-object --expect-failure --json .omo/evidence/task-6-portable-smoke-error.json; ./scripts/smoke-portable.sh --case cancel --expect-failure --append-json .omo/evidence/task-6-portable-smoke-error.json
    Expected: both commands exit 0 because expected failures were observed; marker count is zero and diagnostic codes are `checksum_mismatch` and `cancelled`.
    Evidence: .omo/evidence/task-6-portable-smoke-error.json
  ```

  UltraQA adversarial classes: `partial_publication`, `timeout_or_cancellation`, `misleading_success_output`, `stale_state`. Cleanup: fake S3 and workspaces are temporary and deleted; no background process or socket remains.

  Commit: YES | Message: `test(smoke): prove portable contract conformance slice` | Files: [`tests/fakes/s3.py`, `tests/smoke/test_portable_contract_slice.py`, `scripts/smoke-portable.sh`, `examples/consumer/hello/`, `docs/testing/portable-slice.md`]

- [ ] 7. Build the pinned Ansible host and k3s bootstrap adapter

  Owned files/directories: `ansible/ansible.cfg`, `ansible/requirements.yml`, `ansible/inventory/example.yml`, `ansible/group_vars/all.yml`, `ansible/playbooks/bootstrap.yml`, `ansible/playbooks/preflight.yml`, `ansible/roles/host_preflight/`, `ansible/roles/k3s_adapter/`, `tests/ansible/test_inventory.py`, `tests/ansible/test_bootstrap.py`, `docs/operator/bootstrap.md`.

  What to do: pin `k3s.orchestration` tag `1.2.0` at source commit `2c3f3773c704bd00bf7f6fc340cac8ab7ce9121b`, `kubernetes.core==6.5.0` at source commit `0f472b53e2ee73e11b5f9067ab0826d76183c157`, and all Python/Ansible dependencies. Configure `ansible.cfg` and every command with `ANSIBLE_HOME=$PWD/.cache/ansible/home` and `ANSIBLE_COLLECTIONS_PATH=$PWD/.cache/ansible/collections`; install with `uv run ansible-galaxy collection install -r ansible/requirements.yml -p "$ANSIBLE_COLLECTIONS_PATH" --force`, verify the installed `MANIFEST.json` versions/source locks, and never read or write `~/.ansible`. Validate Ubuntu, system architecture, passwordless privilege escalation, unique hostname/machine-id, required ports, time sync, swap/firewall policy, an already-installed compatible NVIDIA driver/runtime, and storage mount prerequisites without installing OS/drivers. Support one `server` plus N `agent` hosts and exactly three `server` hosts for embedded-etcd; reject two servers. Pin `k3s_version: v1.36.2+k3s1` and its Linux-amd64 SHA-256 `65a55ec56c24eab44383086166ec620a491952b7e23941a49ddca6e8a4c4b4de`, enable secrets encryption only at initial bootstrap, write kubeconfig to a task-owned `0600` path without merging user kubeconfig, and make `make cluster-bootstrap` run check/preflight by default while `make cluster-bootstrap APPLY=1 CONTEXT=<nonproduction>` is the only apply route.

  Must NOT do: no host imaging, driver installation, firewall disablement without an explicit inventory value, destructive reset/uninstall, datastore restore, implicit HA with two servers, user kubeconfig merge, production-context apply, user-global Ansible collection/cache mutation, floating Galaxy/Git resolution, or live execution in this task's QA.

  Parallelization: Can parallel: YES | Wave 3 | Blocks: [10,11,14,15] | Blocked by: [1,3,6].

  References:
  - Pattern: `/mnt/data/lab_clip/pipeline/docs/cluster/01-host-requirements.md:13-51`, `03-install-k3s-control-plane.md:40-102`, `04-join-k3s-worker.md:1-100`, `05-configure-nvidia-runtime.md:1-130`.
  - Preflight: `/mnt/data/lab_clip/pipeline/docs/cluster/11-cluster-preflight.md:20-175`.
  - External: `https://github.com/k3s-io/k3s-ansible/tree/1.2.0`, `https://docs.k3s.io/datastore/ha-embedded`, `https://docs.k3s.io/security/secrets-encryption`.

  Acceptance criteria:
  - [ ] `ANSIBLE_HOME="$PWD/.cache/ansible/home" ANSIBLE_COLLECTIONS_PATH="$PWD/.cache/ansible/collections" uv run ansible-galaxy collection install -r ansible/requirements.yml -p "$PWD/.cache/ansible/collections" --force && ANSIBLE_HOME="$PWD/.cache/ansible/home" ANSIBLE_COLLECTIONS_PATH="$PWD/.cache/ansible/collections" uv run ansible-lint ansible/` exits 0, the installed manifests match both frozen collection versions/commits, and a before/after hash of `~/.ansible` is unchanged.
  - [ ] `uv run pytest -q tests/ansible/test_inventory.py tests/ansible/test_bootstrap.py` proves valid 1+N and 3-server inventories, rejects 0/2/even HA servers and duplicate identities, uses pinned k3s, `0600` kubeconfig, check-mode default, and zero destructive tags.
  - [ ] `uv run ansible-playbook -i ansible/inventory/example.yml ansible/playbooks/bootstrap.yml --syntax-check` exits 0; a fake-host check-mode harness reports `changed=0 failed=0` on its second pass.

  QA scenarios:
  ```
  Scenario: valid one-server inventory is idempotent in check mode
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; uv run pytest -q tests/ansible/test_bootstrap.py::test_single_server_agents_check_mode_idempotent -vv 2>&1 | tee .omo/evidence/task-7-ansible-bootstrap.txt
    Expected: two fake-host passes succeed; second recap is `changed=0 failed=0`; no real SSH connection occurs.
    Evidence: .omo/evidence/task-7-ansible-bootstrap.txt

  Scenario: two-server HA and unsupported host fail before changes
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/ansible/test_inventory.py -k 'two_server or unsupported_os or missing_nvidia' 2>&1 | tee .omo/evidence/task-7-ansible-bootstrap-error.txt
    Expected: stable errors `ha_requires_three_servers`, `ubuntu_required`, or `nvidia_prerequisite_missing`; fake task mutation count is zero.
    Evidence: .omo/evidence/task-7-ansible-bootstrap-error.txt
  ```

  UltraQA adversarial classes: `version_drift`, `dirty_worktree`, `unauthorized_mutation`, `misleading_success_output`. Cleanup: remove downloaded collections/cache and fake kubeconfigs outside evidence; assert permissions tests leave no credential file.

  Commit: YES | Message: `feat(bootstrap): add pinned k3s Ansible adapter` | Files: [`ansible/ansible.cfg`, `ansible/requirements.yml`, `ansible/inventory/example.yml`, `ansible/group_vars/all.yml`, `ansible/playbooks/`, `ansible/roles/host_preflight/`, `ansible/roles/k3s_adapter/`, `tests/ansible/`, `docs/operator/bootstrap.md`]

- [ ] 8. Add the single-owner Helm and Kustomize platform adapter

  Owned files/directories: `deploy/versions.lock.yaml`, `deploy/helm/argo-workflows-values.yaml`, `deploy/helm/nfd-values.yaml`, `deploy/helm/nvidia-device-plugin-values.yaml`, `deploy/helm/tailscale-values.yaml`, `deploy/kustomize/base/`, `deploy/kustomize/overlays/example/`, `scripts/render-platform.sh`, `tests/deploy/`, `docs/architecture/deployment-ownership.md`.

  What to do: pin chart repository URL, chart version, app/source commit, archive SHA-256, and every rendered image digest in `versions.lock.yaml`: Argo `1.0.19`/`v4.0.7` archive `11910d3586c6737df04dbb2e48c373bc88cf5673d6bb17d7d421565495897da8`, NFD `0.18.3` archive `83a327ad61545bc89b319e94f5175ea4d265c9b6ffaa2832af2a0a85389409de`, and NVIDIA device-plugin `0.17.1` archive `542ce451ce5d4611df00959e61c5638b7deeaf65c13daf03914d4688f94ae555`, with the exact image digests from the frozen lock table. Use only `HELM_CONFIG_HOME=$PWD/.cache/helm/config`, `HELM_CACHE_HOME=$PWD/.cache/helm/cache`, `HELM_DATA_HOME=$PWD/.cache/helm/data`, and `$PWD/.cache/helm/charts`; bootstrap repositories into those directories, download the three archives, verify their hashes, and render exclusively from the verified local archives. Helm solely owns those releases. Optional Tailscale is a disabled Kustomize Ingress overlay targeting a separately pre-existing operator; this task installs no Tailscale chart or image. Kustomize solely owns the project namespace, runner RBAC, NetworkPolicies, configurable local StorageClass/PV/PVC and MinIO workloads/services. `render-platform.sh` produces a combined stable manifest and resource inventory, rejects an ownership overlap against `config/resource-ownership.yaml`, rejects mutable images/floating remotes, invokes Task 4's checksum-verified local schema validator, and defaults to render only.

  Must NOT do: no raw duplicate of a Helm-owned resource, no Kustomize remote base without immutable ref/digest, no Fleet/Rancher/Argo CD, no embedded Secrets, no live apply, no automatic CRD/PV/PVC adoption, no `kubectl --dry-run=client`, no user-global Helm repository/cache/config mutation, no Tailscale installation, and no hard-coded LabCLIP node/path/hostname.

  Parallelization: Can parallel: YES | Wave 3 | Blocks: [10,11,12,14,15] | Blocked by: [3,4,6].

  References:
  - Current manifests: `/mnt/data/lab_clip/pipeline/k8s/argo-workflows/README.md:1-80`, `pipeline/rbac.yaml:1-63`, `pipeline/k8s/cache/local-pv.yaml:1-87`, `pipeline/k8s/minio-code.yaml:1-110`.
  - Renderer: `/mnt/data/lab_clip/pipeline/scripts/render_minio_manifests.py:18-87`; tests: `/mnt/data/lab_clip/pipeline/tests/test_minio_manifests.py:23-275`.
  - External: `https://artifacthub.io/packages/helm/argo/argo-workflows/1.0.19`, `https://github.com/NVIDIA/k8s-device-plugin/tree/v0.17.1`, `https://github.com/kubernetes-sigs/node-feature-discovery/tree/v0.18.3`, `https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/`.

  Acceptance criteria:
  - [ ] `./scripts/render-platform.sh --overlay example --output /tmp/platform.yaml --inventory /tmp/resources.json` exits 0 twice with identical hashes and no kubeconfig/network after dependencies are cached.
  - [ ] With the three task-local Helm environment variables exported, `uv run pytest -q tests/deploy && helm template argo-workflows "$PWD/.cache/helm/charts/argo-workflows-1.0.19.tgz" -f deploy/helm/argo-workflows-values.yaml >/tmp/argo.yaml && helm template nfd "$PWD/.cache/helm/charts/node-feature-discovery-0.18.3.tgz" -f deploy/helm/nfd-values.yaml >/tmp/nfd.yaml && helm template nvidia-device-plugin "$PWD/.cache/helm/charts/nvidia-device-plugin-0.17.1.tgz" -f deploy/helm/nvidia-device-plugin-values.yaml >/tmp/nvdp.yaml` exits 0 after archive checksum verification; policy tests assert exact app/source/image pins, one owner per rendered GVK/name, least-privilege RBAC, optional Tailscale absent, and configurable storage/node fields.
  - [ ] `./scripts/validate-manifest-local.sh /tmp/platform.yaml` exits 0 using only packaged schemas; `rg 'image: .*:(latest|main|master)$|https://.*/(main|master)' deploy /tmp/platform.yaml` finds nothing, and before/after hashes of user-global Helm directories are unchanged.

  QA scenarios:
  ```
  Scenario: example overlay renders deterministically with one owner
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; export HELM_CONFIG_HOME="$PWD/.cache/helm/config" HELM_CACHE_HOME="$PWD/.cache/helm/cache" HELM_DATA_HOME="$PWD/.cache/helm/data"; ./scripts/render-platform.sh --overlay example --output .omo/evidence/task-8-platform-render.yaml --inventory .omo/evidence/task-8-platform-resources.json; ./scripts/validate-manifest-local.sh .omo/evidence/task-8-platform-render.yaml
    Expected: exit 0; inventory lists each GVK/namespace/name once with the owner from the ledger; every image is digest-pinned.
    Evidence: .omo/evidence/task-8-platform-render.yaml

  Scenario: overlapping owner or mutable image fails render
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/deploy/test_policy.py -k 'duplicate_owner or mutable_image or floating_remote' 2>&1 | tee .omo/evidence/task-8-platform-render-error.txt
    Expected: tests pass by observing `ownership_conflict`, `image_not_immutable`, and `floating_reference`; no apply command is invoked.
    Evidence: .omo/evidence/task-8-platform-render-error.txt
  ```

  UltraQA adversarial classes: `ownership_conflict`, `version_drift`, `stale_state`, `unauthorized_mutation`. Cleanup: delete `/tmp/platform.yaml`, `/tmp/resources.json`, `/tmp/argo.yaml`, `/tmp/nfd.yaml`, and `/tmp/nvdp.yaml`; leave no Helm release/cache mutation in a cluster.

  Commit: YES | Message: `feat(deploy): add pinned Helm Kustomize adapter` | Files: [`deploy/`, `scripts/render-platform.sh`, `tests/deploy/`, `docs/architecture/deployment-ownership.md`]

- [ ] 9. Implement the redacted Ansible Vault to Kubernetes Secret lifecycle

  Owned files/directories: `ansible/roles/platform_secrets/`, `ansible/vault/example-secrets.yml.example`, `schemas/bootstrap-secrets.schema.json`, `tests/ansible/test_secrets.py`, `tests/fixtures/redaction/`, `docs/operator/secrets.md`, `.gitleaks.toml`.

  What to do: freeze the namespace/name/key identities: `three-t-pipeline/three-t-registry-pull` type `kubernetes.io/dockerconfigjson` key `.dockerconfigjson`; `three-t-{code,data,run}-s3` type `Opaque` keys `endpoint,bucket,accessKey,secretKey,region`; and `three-t-minio-root` type `Opaque` keys `rootUser,rootPassword`. Require explicit `platform_context` and `platform_environment_class` values; reject an implicit current context. `production` categorically rejects Secret create/update, `development` is read-only validation/server dry-run only, and only `ephemeral` may execute automated create/update QA. Keep only schema-valid Ansible Vault ciphertext in real inventories. Decrypt in memory, create/update Kubernetes Secret objects idempotently through `kubernetes.core.k8s` with `no_log: true` and `diff: false`, never put values in argv/environment/evidence, set unavoidable temporary files to `0600` and delete them in `always`, record only Secret UID/resourceVersion/key names/hash-of-key-names, and verify k3s secrets encryption before runtime secrets are applied. Document rotation as a separately authorized future operation with old/new readiness and restart/rollback checks; this plan implements no rotation or deletion path.

  Must NOT do: no committed secret values, `stringData` artifact in evidence, `kubectl create secret ... --from-literal`, shell tracing, decoded Secret reads, secret hash/value in logs, implicit/current context, development or production create/update, default rotation, or external-secret controller.

  Parallelization: Can parallel: YES | Wave 3 | Blocks: [10,11,12,14,15] | Blocked by: [1,3,6].

  References:
  - Pattern: `/mnt/data/lab_clip/pipeline/tests/test_minio_bootstrap.py:197-204,327-426` - redaction and stdin-safe apply behavior.
  - Pattern: `/mnt/data/lab_clip/pipeline/core/storage.py:54-101` - Secret field decoding/resolution to keep behind runtime boundary.
  - Secret examples: `/mnt/data/lab_clip/pipeline/k8s/minio-code-secret.example.yaml:1-10`, `minio-ml-assets-secret.example.yaml:1-10`.
  - External: `https://docs.ansible.com/projects/ansible/latest/vault_guide/vault.html`, `https://docs.ansible.com/projects/ansible/latest/collections/kubernetes/core/k8s_module.html`, `https://kubernetes.io/docs/concepts/configuration/secret/`.

  Acceptance criteria:
  - [ ] `uv run pytest -q tests/ansible/test_secrets.py` proves the exact Secret names/types/keys, required explicit context/environment class, production categorical mutation rejection, development read-only behavior, ephemeral-only automated create/update, schema validation, Vault ciphertext-only repo files, `no_log`/`diff:false` on all value-bearing tasks, no argv/env leakage, `0600`/cleanup, encryption-state gate, redacted failure, idempotent create/update, and absence of delete/rotation tasks.
  - [ ] `git grep -Il '' -- ansible deploy examples tests/fixtures | xargs -r uv run python -m three_t_clip_pipeline.security.scan_secrets` exits 0 and the negative fixture is detected without echoing its value.
  - [ ] A fake Kubernetes client receives the expected Secret key names/namespace but captured stdout/stderr/evidence contains none of the fixture values.

  QA scenarios:
  ```
  Scenario: vaulted values apply through a redacted fake client
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; uv run pytest -q tests/ansible/test_secrets.py::test_ephemeral_vault_to_secret_redacted -vv 2>&1 | tee .omo/evidence/task-9-secret-lifecycle.txt
    Expected: fake Secret is present with required keys; output contains only UID/resourceVersion/key names and no credential bytes.
    Evidence: .omo/evidence/task-9-secret-lifecycle.txt

  Scenario: production, plaintext, or disabled encryption blocks apply
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/ansible/test_secrets.py -k 'production_mutation_rejected or plaintext_rejected or encryption_disabled' 2>&1 | tee .omo/evidence/task-9-secret-lifecycle-error.txt
    Expected: stable errors `production_mutation_forbidden`, `vault_ciphertext_required`, or `k3s_secret_encryption_required`; fake Kubernetes mutation count is zero and output is redacted.
    Evidence: .omo/evidence/task-9-secret-lifecycle-error.txt
  ```

  UltraQA adversarial classes: `secret_exfiltration`, `prompt_injection`, `unauthorized_mutation`, `stale_state`. Cleanup: shred/delete task-created plaintext temp files, unset Vault password variables, and scan evidence before commit.

  Commit: YES | Message: `feat(secrets): add redacted Vault to Kubernetes lifecycle` | Files: [`ansible/roles/platform_secrets/`, `ansible/vault/example-secrets.yml.example`, `schemas/bootstrap-secrets.schema.json`, `tests/ansible/test_secrets.py`, `tests/fixtures/redaction/`, `docs/operator/secrets.md`, `.gitleaks.toml`]

- [ ] 10. Implement CLI clients, verification levels, and mutation authorization

  Owned files/directories: `src/three_t_clip_pipeline/commands/`, `src/three_t_clip_pipeline/clients/`, `src/three_t_clip_pipeline/evidence/`, `tests/commands/`, `tests/clients/`, `docs/operator/cli.md`.

  What to do: implement distinct commands and exit codes: `contract validate` and `plan` are offline; `validate --client` is local Kubernetes validation; `preflight --context X --environment-class {ephemeral,development,production}` performs authenticated reads and states `READ_ONLY`; `validate --server` renders then runs `kubectl apply --server-side --dry-run=server --field-manager=three-t-pipeline-plan`; `submit ... --watch` is the sole per-run Workflow mutation; `status` and `evidence` are read-only. Production-class contexts allow read-only inventory/server dry-run but categorically reject submit/apply/smoke. Reject secret-bearing flags. Sanitize Kubernetes/S3 evidence to UIDs, phases, image digests, selected node/PVC, object sizes/checksums, and marker status.

  Must NOT do: no context-name heuristic, hidden preflight probes, implicit current context, Helm/Kustomize apply, secret flags/values, production mutation, workload-log-only success, or fake-S3 result presented as cluster proof.

  Parallelization: Can parallel: YES | Wave 4 | Blocks: [13,14] | Blocked by: [4,7,8,9].

  References:
  - Anti-pattern: `/mnt/data/lab_clip/pipeline/submit_kfold.py:57-82` - print-only dry run that proves no external readiness.
  - Anti-pattern: `/mnt/data/lab_clip/pipeline/core/submit_core.py:581-590` - secret-bearing CLI arguments to remove.
  - Pattern: `/mnt/data/lab_clip/pipeline/docs/cluster/11-cluster-preflight.md:20-175` - read-only cluster facts to automate.
  - Pattern: `/mnt/data/lab_clip/pipeline/core/submit_core.py:523-578` - explicit submit boundary and cleanup.

  Acceptance criteria:
  - [ ] `uv run pytest -q tests/commands tests/clients` asserts each verification level's allowed calls, explicit context/environment, production read-only allowance, production mutation rejection, stable exit codes, watch terminal phases, and redaction.
  - [ ] `uv run 3t-pipeline --help` has no option matching `secret|password|token|access-key`; static tests prove only `submit` calls create Workflow.
  - [ ] Evidence golden files contain no Kubernetes Secret data, environment dump, credential canaries, signed URLs, or unpinned image tag.

  QA scenarios:
  ```
  Scenario: production-class preflight and server dry-run remain read-only
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; uv run pytest -q tests/commands/test_gates.py::test_production_read_only_preflight_and_server_dry_run -vv 2>&1 | tee .omo/evidence/task-10-cli-gates.txt
    Expected: fake client records GET/LIST and dry-run PATCH only, reports `READ_ONLY`, and records zero persisted mutations.
    Evidence: .omo/evidence/task-10-cli-gates.txt

  Scenario: production submit and secret flag are rejected before client creation
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/commands/test_gates.py -k 'production_submit_rejected or secret_flag_rejected' 2>&1 | tee .omo/evidence/task-10-cli-gates-error.txt
    Expected: errors are `production_mutation_forbidden` and `secret_flag_forbidden`; Kubernetes/S3 fake call counts are zero and credential canary is absent.
    Evidence: .omo/evidence/task-10-cli-gates-error.txt
  ```

  UltraQA adversarial classes: `unauthorized_mutation`, `secret_exfiltration`, `misleading_success_output`, `prompt_injection`. Cleanup: remove fake kubeconfigs and contexts; terminate watch processes; scan evidence for canaries.

  Commit: YES | Message: `feat(cli): enforce verification and mutation boundaries` | Files: [`src/three_t_clip_pipeline/commands/`, `src/three_t_clip_pipeline/clients/`, `src/three_t_clip_pipeline/evidence/`, `tests/commands/`, `tests/clients/`, `docs/operator/cli.md`]

- [ ] 11. Generalize bundle, cache, profile, publication, and provenance services

  Owned files/directories: `src/three_t_clip_pipeline/services/`, `src/three_t_clip_pipeline/runtime/publication.py`, `src/three_t_clip_pipeline/runtime/provenance.py`, `tests/services/`, `tests/runtime/test_publication.py`, `schemas/committed-v1.json`, `docs/architecture/publication.md`.

  What to do: port only portable invariants behind injected clients/clocks/cancellation tokens. Bundles exclude VCS, artifacts, results, caches, credentials, symlinks, and platform helpers; uploads carry size/SHA-256 manifest. Cache inventories use generic S3 URI/destination, verified staged downloads, atomic local replace, and no mirror/delete API. Profile acquisition uses a monotonic deadline from `profileAcquireSeconds` (default 900, maximum 7200), per-call API timeout <=15 seconds, cancellable waits <=5 seconds, Lease release before sleep and in `finally`, and stable timeout/cancel codes. Publication uploads immutable run-scoped objects and provenance, HEAD-verifies required sizes/checksums, then finalizer conditionally creates canonical `COMMITTED.json` with `additionalProperties:false` and exact fields `schema`, `runUid`, `workflowUid`, `workloadImageDigest`, `platformImageDigest`, `bundleDigest`, `objects[]` (`key,size,sha256` sorted by key), `provenanceKey`, `workloadStatus: Succeeded`, and `mainFinishedAt`. During `onExit`, pass the Workflow name/UID and main template-node identity to the finalizer; it performs a read-only Workflow GET, selects exactly one Pod node with template/display name `run`, requires phase `Succeeded`, parses that node's RFC3339 `finishedAt`, requires it to be no later than finalizer start, and copies it byte-for-byte into `mainFinishedAt`. Missing, ambiguous, unparsable, future, or non-succeeded main-node state blocks the marker. The marker never uses top-level Workflow `finishedAt` or creation time. HTTP 412 is idempotent success only if existing marker has the same run UID and canonical payload digest; otherwise collision failure.

  Must NOT do: no unbounded loop, lock held during sleep/I/O, dataset slug/model logic, MinIO backend mount, default mirror/delete, mutable overwrite of payload/marker, marker for failed/cancelled workload, failed-run marker, or whole-prefix atomicity assertion. Failed/cancelled staged objects remain non-readable through the committed-run API but diagnostics remain available through status/evidence.

  Parallelization: Can parallel: YES | Wave 4 | Blocks: [13,14,15] | Blocked by: [2,5,7,8,9].

  References:
  - Bundle: `/mnt/data/lab_clip/pipeline/core/code_bundle.py:42-49,105-249,257-315`; tests `/mnt/data/lab_clip/pipeline/tests/test_code_bundle.py:41-171`.
  - Cache: `/mnt/data/lab_clip/pipeline/core/dataset_cache.py:108-225,473-614`; destructive anti-pattern `338-404`.
  - Profile: `/mnt/data/lab_clip/pipeline/core/cache_profile.py:37-172,415-455`; tests `pipeline/tests/test_cache_profile.py:266-415`.
  - Publication: `/mnt/data/lab_clip/pipeline/core/result_sync.py:40-83,120-207`; tests `pipeline/tests/test_result_sync.py:47-207`.
  - External: `https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html`.

  Acceptance criteria:
  - [ ] `uv run pytest -q tests/services tests/runtime/test_publication.py` covers bundle safety, corrupt/missing S3 metadata, cache atomicity, profile success/timeout/cancel/API error/Lease release, publication partial/corrupt/missing/cancel, exact main-node timestamp copy, missing/ambiguous/non-succeeded/bad/future `finishedAt`, top-level timestamp rejection, identical duplicate marker, and conflicting marker.
  - [ ] Fake-clock tests assert no acquisition exceeds its deadline plus one API timeout, no wait exceeds 5 seconds, every acquired Lease is released, and cancellation reaches the caller without a marker.
  - [ ] `uv run python -m three_t_clip_pipeline.security.forbidden_api_scan src` proves no `delete_objects`, mirror flag, secret CLI, LabCLIP import, or mutable image handling exists in default command/service paths.

  QA scenarios:
  ```
  Scenario: verified publication creates one canonical marker
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; uv run pytest -q tests/runtime/test_publication.py::test_verified_commit_marker_and_idempotent_duplicate -vv 2>&1 | tee .omo/evidence/task-11-runtime-services.txt
    Expected: objects are verified before one conditional marker; identical duplicate returns committed; marker schema and digest are canonical.
    Evidence: .omo/evidence/task-11-runtime-services.txt

  Scenario: profile timeout and conflicting marker fail safely
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/services/test_profile.py::test_deadline_cancels_and_releases_lease tests/runtime/test_publication.py::test_conflicting_marker_is_collision -vv 2>&1 | tee .omo/evidence/task-11-runtime-services-error.txt
    Expected: timeout is bounded with Lease released; marker conflict reports `commit_collision`; no new marker/delete call occurs.
    Evidence: .omo/evidence/task-11-runtime-services-error.txt
  ```

  UltraQA adversarial classes: `timeout_or_cancellation`, `partial_publication`, `path_traversal`, `ownership_conflict`, `stale_state`. Cleanup: stop fake clocks/processes, remove temporary bundle/cache/output trees, and assert all Leases/fake sockets are released.

  Commit: YES | Message: `feat(runtime): add portable storage cache and publication services` | Files: [`src/three_t_clip_pipeline/services/`, `src/three_t_clip_pipeline/runtime/publication.py`, `src/three_t_clip_pipeline/runtime/provenance.py`, `tests/services/`, `tests/runtime/test_publication.py`, `schemas/committed-v1.json`, `docs/architecture/publication.md`]

- [ ] 12. Build the immutable runtime image and CI-to-GHCR digest lifecycle

  Owned files/directories: `Containerfile.runtime`, `.dockerignore`, `deploy/images.lock.yaml`, `scripts/image-metadata.py`, `.github/workflows/ci.yml`, `.github/workflows/runtime-image.yml`, `tests/supply_chain/`, `docs/operator/image-release.md`; Task 1 exclusively owns `ci/tools.lock.yaml`.

  What to do: build a small CPU platform runtime image separate from consumer GPU images from the frozen Python base digest, install from `uv.lock`, run as non-root with read-only rootfs-compatible paths, and include only platform runtime/CLI. Consume Task 1's exact `ci/tools.lock.yaml`: Docker Engine/CLI `29.4.0`, Buildx `0.34.1`, uv, kubectl, Helm, Kind, kubeconform, and the six GitHub Actions at the exact versions/checksums/commit SHAs in the frozen lock table; no unlisted scanner/download is permitted. Set `kindNodeImage` to `docker.io/kindest/node:v1.35.0@sha256:4613778f3cfcd10e615029370f5786704559103cf27bef934597ba562b269661` and `localRegistryImage` to `docker.io/library/registry:2.8.3@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373` in `deploy/images.lock.yaml`. CI verifies every checksum/digest before execution and uses only repository-local `.cache/ci`, `.cache/docker`, and `.cache/buildx` state. Use release registry `ghcr.io/jayn2u/3t-clip-pipeline-runtime`. PR CI builds without external push; default `develop` workflow may push to GHCR only after explicit GitHub environment approval, using immutable tag `sha-<40hex>`, capturing BuildKit `containerimage.digest` (`D_runtime`) and BuildKit-generated SBOM/provenance artifacts, verifying `ghcr.io/jayn2u/3t-clip-pipeline-runtime@D_runtime`, and proposing (not silently committing) that reviewed digest for `deploy/images.lock.yaml`. `D_runtime` is the immutable output of the approved build, not a preselected placeholder. Manifests consume only `image@sha256`. Retention policy must preserve all digests referenced by released locks/run provenance; never publish `latest`.

  Must NOT do: no CUDA/ML stack, credentials in layers/args/logs, root runtime, mutable tag consumption, or push from local implementation/PR to GHCR or any non-loopback registry. The sole local push exception is Task 14's task-owned disposable registry bound to `127.0.0.1`, which uses no registry credentials and is deleted in cleanup. Do not auto-update the deployment lock in the same unreviewed build or garbage-collect referenced release digests.

  Parallelization: Can parallel: YES | Wave 4 | Blocks: [14,15] | Blocked by: [1,5,8,9].

  References:
  - Anti-pattern: `/mnt/data/lab_clip/pipeline/Dockerfile:1-29`, `pipeline/requirements.txt:1-126` - coupled GPU/training image.
  - Pattern: `/mnt/data/lab_clip/pipeline/core/hera/utils.py:19` - digest-pinned image consumption.
  - Pattern: `/mnt/data/lab_clip/pipeline/docker/minio-community.Dockerfile:1-10`, `pipeline/scripts/build_minio_community.py:18-90` - digest-pinned bases and digest capture.
  - External: `https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry`, `https://kubernetes.io/docs/concepts/containers/images/#image-names`.

  Acceptance criteria:
  - [ ] `docker buildx build --load -f Containerfile.runtime -t 3t-pipeline-runtime:test .` exits 0 and `docker run --rm --read-only --tmpfs /tmp 3t-pipeline-runtime:test runtime --help` exits 0 as non-root.
  - [ ] `uv run pytest -q tests/supply_chain` asserts the six exact full-SHA Actions, all exact checksummed CI tools from Task 1, the literal digest-pinned `kindNodeImage`/`localRegistryImage`, exact release-registry/tag/`D_runtime` lifecycle, PR no external push, protected default-branch GHCR publish, the narrowly scoped `127.0.0.1` disposable-registry exception, BuildKit SBOM/provenance/digest artifacts, reviewed lock promotion, task-local caches, retention rule, and no `latest`.
  - [ ] `./scripts/render-platform.sh --overlay example ...` contains the reviewed digest from `deploy/images.lock.yaml`, never the commit tag alone.

  QA scenarios:
  ```
  Scenario: local image starts non-root and lock resolves by digest
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; docker buildx build --load -f Containerfile.runtime -t 3t-pipeline-runtime:test .; docker inspect 3t-pipeline-runtime:test > .omo/evidence/task-12-image-supply-chain.json; docker run --rm --read-only --tmpfs /tmp 3t-pipeline-runtime:test runtime --help
    Expected: build/start exit 0; configured user is non-root; no secret/build-context leakage; deployment lock matches regex `^ghcr\.io/jayn2u/3t-clip-pipeline-runtime@sha256:[0-9a-f]{64}$`.
    Evidence: .omo/evidence/task-12-image-supply-chain.json

  Scenario: mutable tag and unauthorized push path fail policy
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/supply_chain -k 'latest_rejected or pr_push_forbidden or unreviewed_digest_rejected' 2>&1 | tee .omo/evidence/task-12-image-supply-chain-error.txt
    Expected: all tests pass by rejecting mutable/unapproved lifecycle states; no registry login or external-registry push occurs, while only the explicit loopback smoke-registry policy is accepted.
    Evidence: .omo/evidence/task-12-image-supply-chain-error.txt
  ```

  UltraQA adversarial classes: `version_drift`, `secret_exfiltration`, `unauthorized_mutation`, `stale_state`. Cleanup: `docker image rm 3t-pipeline-runtime:test` after evidence capture; delete BuildKit temp metadata; do not log out/change the user's registry configuration.

  Commit: YES | Message: `ci(image): add immutable runtime image lifecycle` | Files: [`Containerfile.runtime`, `.dockerignore`, `deploy/images.lock.yaml`, `scripts/image-metadata.py`, `.github/workflows/`, `tests/supply_chain/`, `docs/operator/image-release.md`]

- [ ] 13. Port portable invariants and prove the consumer boundary

  Owned files/directories: `tests/portable/`, `tests/fixtures/portable/`, `examples/consumer/python-job/`, `docs/migration/consumer-adapter.md`, updates only to `docs/migration/labclip-test-classification.yaml` and `docs/migration/labclip-test-map.md`.

  What to do: re-express every ledger-designated portable invariant as target-owned parameterized tests using generic nodes, profiles, namespaces, buckets, paths, labels, GPU resource keys, and consumer commands. Add a standalone consumer example that owns its application config and merely supplies a `v1alpha1` workload. Mark all remaining LabCLIP-only fixtures as intentionally not ported with rationale and adapter hook. Prove the target package, examples, and rendered workflow contain no LabCLIP imports/data/model/W&B semantics.

  Must NOT do: no verbatim bulk test copy, no claim that the unverified LabCLIP suite has 251 passes, no `train/train_itc_*`, dataset slug, retrieval metric, W&B policy, fixed node/PVC/bucket, or edit to the reference repo.

  Parallelization: Can parallel: YES | Wave 5 | Blocks: [F1-F4] | Blocked by: [3,10,11].

  References:
  - Ledger sources: `/mnt/data/lab_clip/pipeline/tests/test_code_bundle.py:41-171`, `test_dataset_cache.py:1-617`, `test_cache_profile.py:72-422`, `test_result_sync.py:47-207`, `test_s3_integrity.py:300-391`.
  - LabCLIP-only sources: `/mnt/data/lab_clip/pipeline/tests/test_submit_core.py:9-126`, `pipeline/core/hera/labclip_pipeline.py:399-468`, `configs/train/train_itc_adamw.yaml:1-54`, `src/wandb_tracking.py:59-212`.
  - Boundary: `/mnt/data/lab_clip/src/config.py:63-110` - consumer YAML semantics remain consumer-owned.

  Acceptance criteria:
  - [ ] `uv run pytest -q -m 'unit or contract or render or client or integration' tests/portable tests/contract tests/render tests/services` exits 0 with all portable ledger rows linked to at least one target test.
  - [ ] `uv run python -m three_t_clip_pipeline.policy.check_test_map --reference-root /mnt/data/lab_clip` reports every listed reference test classified exactly once and no unplanned omission.
  - [ ] `rg -n 'lab_clip|labclip|cuhk|icfg|rstpreid|train_itc|wandb' src schemas examples tests/portable` returns no matches.

  QA scenarios:
  ```
  Scenario: generic consumer satisfies all portable invariants
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; uv run pytest -q tests/portable examples/consumer/python-job/tests 2>&1 | tee .omo/evidence/task-13-portable-invariants.txt
    Expected: exit 0; consumer example supplies only contract fields; platform tests pass across parameterized names/resources/stores.
    Evidence: .omo/evidence/task-13-portable-invariants.txt

  Scenario: research-specific field/import is rejected
    Tool: bash
    Steps: set -euo pipefail; uv run pytest -q tests/portable/test_boundary.py -k 'research_field or labclip_import' 2>&1 | tee .omo/evidence/task-13-portable-invariants-error.txt
    Expected: tests pass with `consumer_semantics_forbidden`/`reference_import_forbidden`; reference repo status is unchanged.
    Evidence: .omo/evidence/task-13-portable-invariants-error.txt
  ```

  UltraQA adversarial classes: `dirty_worktree`, `stale_state`, `prompt_injection`, `scope_creep`. Cleanup: remove generated reference inventory and example outputs; compare reference-repo status hashes captured in Task 3.

  Commit: YES | Message: `test(portability): port platform invariants and consumer example` | Files: [`tests/portable/`, `tests/fixtures/portable/`, `examples/consumer/python-job/`, `docs/migration/consumer-adapter.md`, `docs/migration/labclip-test-classification.yaml`, `docs/migration/labclip-test-map.md`]

- [ ] 14. Prove isolated-live behavior and PV/MinIO cutover rollback readiness

  Owned files/directories: `tests/isolated_live/`, `tests/isolated_live/fixtures/kind-local-registry.yaml`, `scripts/isolated-live-smoke.sh`, `src/three_t_clip_pipeline/readiness/`, `tests/readiness/`, `schemas/cutover-readiness.schema.json`, `docs/operator/pv-minio-cutover.md`, `docs/operator/pv-minio-rollback.md`.

  What to do: create a fully local image-distribution path for isolated-live QA. The smoke script preflights that TCP `127.0.0.1:5001`, Docker container name `three-t-pipeline-registry`, Kind cluster name `three-t-pipeline-smoke`, and all corresponding labels are absent before mutation; it then starts exactly `docker.io/library/registry:2.8.3@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373` bound only to loopback and creates the cluster with exactly `docker.io/kindest/node:v1.35.0@sha256:4613778f3cfcd10e615029370f5786704559103cf27bef934597ba562b269661`. Connect `three-t-pipeline-registry` to the Kind network. For every node, create `/etc/containerd/certs.d/localhost:5001/hosts.toml` with `server = "http://three-t-pipeline-registry:5000"`, `host."http://three-t-pipeline-registry:5000"`, and capabilities `["pull", "resolve"]`, then restart containerd before creating Pods; the checked-in fixture enables `config_path = "/etc/containerd/certs.d"`. Build platform and hello-consumer images as `localhost:5001/three-t-platform:smoke` and `localhost:5001/three-t-hello:smoke`. Mirror these literal locked sources to literal local repositories: MinIO -> `localhost:5001/minio:smoke`; Argo controller -> `localhost:5001/argo-controller:smoke`; argocli -> `localhost:5001/argo-server:smoke`; argoexec -> `localhost:5001/argo-executor:smoke`; kubectl -> `localhost:5001/kubectl:smoke`; NFD -> `localhost:5001/node-feature-discovery:smoke`; NVIDIA plugin -> `localhost:5001/nvidia-device-plugin:smoke`, using the exact seven source image digests in the frozen lock table. Use `docker buildx build --push --metadata-file` for local builds and `docker buildx imagetools create` for mirrors. Read each resulting digest, compose only `localhost:5001/<literal-repository>@sha256:<64-lowercase-hex>` references, independently inspect each digest from the loopback registry, and render those references into the isolated overlay. The script must reject any push target not exactly `localhost:5001` or `127.0.0.1:5001`, unset registry credentials and GitHub tokens, never run `docker login`, record every push target/digest, and clean the cluster/registry in a trap. Install rendered project resources only in the ephemeral cluster, submit the hello workload, and query Kubernetes/S3 state for phase, UIDs, image IDs/digests, node/PVC, object metadata, and marker. Separately implement a read-only readiness report for existing PV/MinIO: capture current UID/spec/managedFields/owner, prove the host path is an existing `Directory` on the intended mounted device via `findmnt`, require an independent object backup inventory with count/bytes/SHA-256 plus completed restore rehearsal, verify quiescence, compute immutable-field/ownership/server-dry-run diff, and enumerate rollback owner/commands. PASS only if every proof is present and matching; otherwise FAIL and perform no mutation. Cutover and rollback documents are commands for a separately authorized maintenance window, not executed QA.

  Must NOT do: no GHCR/external-registry login or push, mutable image in a rendered Pod, host-network registry exposure beyond `127.0.0.1:5001`, reuse of a pre-existing registry/cluster, production/lab apply, CRD/PV/PVC replacement, production MinIO mirror/delete/copy, credential rotation, `DirectoryOrCreate`, `Retain`-as-backup claim, workload-log-only proof, or cleanup of production state.

  Parallelization: Can parallel: YES | Wave 5 | Blocks: [F1-F4] | Blocked by: [7,8,9,10,11,12].

  References:
  - Storage risks: `/mnt/data/lab_clip/pipeline/k8s/cache/local-pv.yaml:1-87`, `pipeline/k8s/minio-code.yaml:77-91`, `pipeline/k8s/minio-ml-assets.yaml:73-87`.
  - Operations: `/mnt/data/lab_clip/pipeline/docs/cluster/07-configure-local-storage.md:1-220`, `11-cluster-preflight.md:20-175`, `12-upgrade-and-recovery.md:19-182`.
  - Evidence tests: `/mnt/data/lab_clip/pipeline/tests/test_s3_integrity.py:300-391`, `test_local_cache_manifests.py:49-116`.
  - External: `https://kubernetes.io/docs/concepts/storage/persistent-volumes/`, `https://kubernetes.io/docs/concepts/storage/volumes/#hostpath`, `https://min.io/docs/minio/linux/administration/object-management.html`.

  Acceptance criteria:
  - [ ] `env -u GHCR_TOKEN -u GITHUB_TOKEN -u DOCKER_AUTH_CONFIG ./scripts/isolated-live-smoke.sh --create --registry 127.0.0.1:5001 --build-push-local --run --collect .omo/evidence/task-14-isolated-live.json --cleanup` exits 0 against only the ephemeral cluster, records only loopback push targets, verifies all rendered platform/consumer/MinIO/Argo references and Pod `imageID` values by matching SHA-256, and verifies a committed run from API/object state.
  - [ ] `uv run pytest -q tests/readiness` covers pass plus missing mount, wrong device, missing/stale backup, count/hash mismatch, active writers, ownership conflict, immutable PV diff, server-dry-run failure, and absent rollback owner; every failure records `ready=false` and zero mutation calls.
  - [ ] `uv run pytest -q tests/isolated_live/test_registry_policy.py` proves every literal source/local mapping and digest, and proves the script rejects non-loopback push targets, mutable/mismatched digests, occupied port 5001, pre-existing `three-t-pipeline-smoke`/`three-t-pipeline-registry`, and any credential/login path before mutation; static policy also proves `hostPath.type: Directory`, production contexts have no apply path in smoke, and cutover/rollback commands require a separate authorization artifact not created here.

  QA scenarios:
  ```
  Scenario: ephemeral cluster and MinIO complete a real committed run
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; env -u GHCR_TOKEN -u GITHUB_TOKEN -u DOCKER_AUTH_CONFIG ./scripts/isolated-live-smoke.sh --create --registry 127.0.0.1:5001 --build-push-local --run --collect .omo/evidence/task-14-isolated-live.json --cleanup
    Expected: exit 0; evidence lists only loopback pushes, digest-only rendered images with matching Pod imageIDs, ephemeral context/namespace, Workflow/Pod UIDs, PVC/node, verified objects, one valid marker, and cluster/registry cleanup confirmation; GHCR receives no write.
    Evidence: .omo/evidence/task-14-isolated-live.json

  Scenario: external registry target, wrong mount, and stale backup are rejected
    Tool: bash
    Steps: set -euo pipefail; (uv run pytest -q tests/isolated_live/test_registry_policy.py -k 'external_target or mutable_digest or occupied_port' && uv run pytest -q tests/readiness/test_pv_minio.py -k 'wrong_mount or stale_backup or immutable_diff') 2>&1 | tee .omo/evidence/task-14-isolated-live-error.txt
    Expected: tests pass by rejecting all non-loopback/mutable/conflicting registry states before a push or cluster create and reporting cutover `ready=false` with exact failed checks; no external registry, production Kubernetes, or production S3 mutation occurs.
    Evidence: .omo/evidence/task-14-isolated-live-error.txt
  ```

  UltraQA adversarial classes: `unauthorized_mutation`, `ownership_conflict`, `stale_state`, `misleading_success_output`, `partial_publication`. Cleanup: always delete only the uniquely labeled ephemeral cluster/namespace and local temp directory; preserve failed-run evidence; never delete a lab/production resource or object.

  Commit: YES | Message: `test(integration): add isolated live and cutover readiness gates` | Files: [`tests/isolated_live/`, `tests/isolated_live/fixtures/kind-local-registry.yaml`, `scripts/isolated-live-smoke.sh`, `src/three_t_clip_pipeline/readiness/`, `tests/readiness/`, `schemas/cutover-readiness.schema.json`, `docs/operator/pv-minio-cutover.md`, `docs/operator/pv-minio-rollback.md`]

- [ ] 15. Complete operator, migration, rollback, and release handoff

  Owned files/directories: `docs/operator/`, `docs/migration/ownership-handoff.md`, `docs/migration/labclip-compatibility.md`, `docs/architecture/decisions/`, `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, updates to `README.md` and `Makefile`, `tests/docs/`.

  What to do: document one end-to-end path for offline plan, client validation, read-only/server preflight, isolated-live smoke, bootstrap check/apply, submit/watch/status/evidence, backup/readiness, cutover/rollback authorization, and recovery. Include exact ownership freeze/handoff states (`unmanaged`, `legacy-owned`, `frozen`, `ready`, `new-owned`, `rolled-back`), abort criteria, expected evidence, and explicit non-goals. Record version/digest update workflow and API evolution. Add doc-command tests and one concrete Makefile interface: `make release-handoff DELIVERY_MODE=local` is the mode for this plan execution and stops after all local verification with `LOCAL_COMPLETE_REMOTE_DEFERRED`. It performs no network or remote write. Document but do not invoke a later authorized interface, `make release-handoff DELIVERY_MODE=authorized-pr AUTHORIZATION_FILE=.omo/authorizations/remote-delivery.json`; that mode must reject a missing file and require an externally created, mode-`0600`, schema-valid JSON artifact containing exactly `{"schema":"three-t-pipeline/remote-delivery-v1","repository":"jayn2u/3t-clip-pipeline","branch":"codex/next-generation-pipeline-platform","base":"develop","allowPush":true,"allowPullRequest":true}`. The artifact is authority, never generated by this plan. Only in that mode, run `git ls-remote --exit-code --symref origin HEAD` and `git ls-remote --exit-code origin refs/heads/develop`; if either is absent, exit 78 with `REMOTE_AUTHORITY_REQUIRED: an external owner must initialize origin/develop and remote HEAD; local task worktree and commits are complete and preserved`, without remote writes. After an external owner creates both, run the exact fetch `git fetch origin +refs/heads/develop:refs/remotes/origin/develop`, record the pre-rebase task SHA, run `git rebase --onto refs/remotes/origin/develop --root` in the task worktree so the orphan-root task history becomes PR-compatible, rerun the complete quality/manual-QA gates on the rewritten SHA, then push only `codex/next-generation-pipeline-platform` and open the Korean ready-for-review PR. If rebase conflicts, run `git rebase --abort`, verify the pre-rebase SHA is restored, report `REMOTE_BASE_CONFLICT`, and perform no push; do not invent a merge policy. These are product Makefile modes, not skill invocation syntax.

  Must NOT do: no actual consumer migration, live takeover/cutover, backup/restore, credential rotation, remote initialization, push/PR without the execution mode's authorization, or edits to reference repos.

  Parallelization: Can parallel: YES | Wave 5 | Blocks: [F1-F4] | Blocked by: [3,7,8,9,10,11,12].

  References:
  - Current architecture/runbooks: `/mnt/data/lab_clip/pipeline/README.md:9-155`, `pipeline/docs/cluster/00-current-architecture.md:1-180`, `11-cluster-preflight.md:20-175`, `12-upgrade-and-recovery.md:19-182`.
  - Target state: `/mnt/data/3t-clip-pipeline/.git/config` - configured GitHub origin; current remote/default branch must be rechecked, never assumed.
  - Project workflow: `/mnt/data/lab_clip/AGENTS.md` - dedicated worktree/branch, verification, commit attribution, Korean PR description standards inherited for this migration plan.

  Acceptance criteria:
  - [ ] `uv run pytest -q tests/docs` executes every safe/read-only documented command or snapshot-checks commands explicitly marked `REQUIRES_SEPARATE_AUTHORIZATION`; no production mutation command is runnable by copy/paste without the gate.
  - [ ] `make quality test render smoke` exits 0 and documentation links/anchors resolve.
  - [ ] `make release-handoff DELIVERY_MODE=local` deterministically reports `LOCAL_COMPLETE_REMOTE_DEFERRED` with zero network calls; tests of `DELIVERY_MODE=authorized-pr` report `PR_READY` only after the exact authorization artifact, remote HEAD/develop, exact fetch refspec, root-history rebase, rewritten-SHA gates, and push authority are present, or exit 78 with exact external-owner remediation and no remote mutation.

  QA scenarios:
  ```
  Scenario: fresh operator follows safe path through isolated smoke
    Tool: bash
    Steps: set -euo pipefail; mkdir -p .omo/evidence; uv run pytest -q tests/docs/test_operator_journey.py -vv 2>&1 | tee .omo/evidence/task-15-operator-handoff.txt
    Expected: offline/client/read-only/isolated commands execute or snapshot successfully in order; authorization-only commands are never executed.
    Evidence: .omo/evidence/task-15-operator-handoff.txt

  Scenario: unborn remote defers only push/PR while preserving completed local work
    Tool: bash
    Steps: set -euo pipefail; make release-handoff DELIVERY_MODE=local; uv run pytest -q tests/docs/test_release_gate.py::test_unborn_remote_stops_only_before_push_and_pr tests/docs/test_release_gate.py::test_external_base_uses_exact_refspec_and_rebases_orphan_root tests/docs/test_release_gate.py::test_external_base_conflict_aborts_and_restores tests/docs/test_operator_journey.py::test_cutover_without_authorization_rejected -vv 2>&1 | tee .omo/evidence/task-15-operator-handoff-error.txt
    Expected: missing remote state yields `REMOTE_AUTHORITY_REQUIRED` after local branch/tests remain complete; the compatible-base fixture replays root commits onto remote develop and reruns gates; the conflict fixture aborts and restores the pre-rebase SHA; no unauthorized remote/cluster mutation occurs.
    Evidence: .omo/evidence/task-15-operator-handoff-error.txt
  ```

  UltraQA adversarial classes: `dirty_worktree`, `unauthorized_mutation`, `stale_state`, `misleading_success_output`, `scope_creep`. Cleanup: delete only temporary doc-test repos/contexts; compare before/after status hashes of `/mnt/data/lab_clip` and `/mnt/data/3t-clip`; retain release evidence.

  Commit: YES | Message: `docs(operator): complete platform migration handoff` | Files: [`docs/operator/`, `docs/migration/ownership-handoff.md`, `docs/migration/labclip-compatibility.md`, `docs/architecture/decisions/`, `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `README.md`, `Makefile`, `tests/docs/`]

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
  Run an independent read-only audit against every Must-have, Must-NOT-have, todo acceptance checkbox, dependency, evidence path, and commit. Reject if any task is self-reported without its exact artifact/command. Evidence: `.omo/evidence/final-f1-plan-compliance.md`.
- [ ] F2. Code quality review
  Run `uv lock --check`, `uv sync --locked --all-groups`, `uv run ruff format --check .`, `uv run ruff check .`, `uv run basedpyright`, all registered non-live tests, `ansible-lint`, `yamllint`, Helm lint/render, Kustomize render, schema drift checks, secret scan, and image/ownership policy checks. Reject lock drift, dead code, LabCLIP coupling, duplicate owners, floating references, or diagnostics. Evidence: `.omo/evidence/final-f2-code-quality.txt`.
- [ ] F3. Real manual QA
  Execute every todo's happy and failure QA exactly, including the fake-S3 portable slice and pinned ephemeral Kubernetes/MinIO isolated-live smoke; verify evidence by querying process/Kubernetes/S3 state rather than trusting logs. Production/lab mutation stays forbidden. Evidence index: `.omo/evidence/final-f3-manual-qa.json`.
- [ ] F4. Scope fidelity
  Compare implementation diff and before/after status hashes. Permit in the shared `/mnt/data/3t-clip-pipeline` repository only the Task 1 inventory of required registered-worktree metadata: one exact `.worktrees/` line in `.git/info/exclude`, the Git-created `.git/worktrees/<admin-entry>/` record whose gitdir/HEAD/locked data resolves only to `/mnt/data/3t-clip-pipeline/.worktrees/next-generation-pipeline-platform` and `refs/heads/codex/next-generation-pipeline-platform`, and that worktree's `.git` pointer; reject every other shared-tree product/source/ref/config mutation. Prove all planned source/product edits live in the task worktree, `/mnt/data/lab_clip` and `/mnt/data/3t-clip` are unchanged, no Fleet/Rancher/training semantics/secret values/destructive S3/default live apply shipped, and cutover remains documentation/readiness only. Evidence: `.omo/evidence/final-f4-scope-fidelity.md`.

## Commit strategy
- One logical implementation-plus-test change per task using the exact Conventional Commit message stated in that task; each commit must build and pass its targeted checks independently.
- Use the human user's configured Git author. Add `Co-authored-by: Codex <codex@openai.com>` for Codex-authored commits without changing `user.name` or `user.email`.
- Stage only each task's owned paths; never stage `.omo/evidence/`, credentials, kubeconfig, rendered Secrets, reference-repo files, caches, ephemeral manifests, or unrelated user changes.
- No WIP/fixup/cleanup-later commits on the review branch. Before PR handoff, verify `git log --format=full` has the intended atomic sequence and every commit includes its required co-author trailer.
- Local implementation/commits do not depend on remote HEAD/default-branch state. This execution uses `make release-handoff DELIVERY_MODE=local` and ends with `LOCAL_COMPLETE_REMOTE_DEFERRED`. At a later push/PR boundary only, require the exact Task 15 `authorized-pr` Makefile mode and external authorization artifact; once an external owner supplies the base, fetch with `git fetch origin +refs/heads/develop:refs/remotes/origin/develop`, rebase with `git rebase --onto refs/remotes/origin/develop --root`, rerun all gates on the rewritten SHA, push only `codex/next-generation-pipeline-platform`, and open a ready-for-review PR against `develop` with Korean title/body covering changes, rationale, impact, verification, and live-cutover exclusions.
- Reference this plan in the final commit footer: `Plan: .omo/plans/next-generation-pipeline-platform.md`.

## Success criteria
- Task 1 creates and locks the local orphan task worktree/branch without network or remote writes while shared local `develop` remains unborn; all local implementation can complete in that worktree. Missing remote HEAD/`origin/develop` blocks only authorized push/PR, never local implementation.
- All 15 implementation todos and their acceptance/QA checks pass with redacted evidence; Tasks 2-6 prove milestone 1 before Tasks 7-9 begin, and Tasks 7-9 prove milestone 2 before platform-service integration.
- `v1alpha1` schema/canonical fixture and exact `bundle-init`/`main`/`artifact-publisher`/`commit-results` boundary are frozen and cross-tested.
- Artifact readers accept only a valid `COMMITTED.json`; partial, corrupt, failed, cancelled, timed-out, or conflicting publications remain uncommitted, while identical duplicate finalization is idempotent.
- Cache-profile acquisition is bounded/cancellable and always releases its Lease; Vault-to-Secret output is fully redacted; deployed images/charts/tools resolve from immutable locks and reviewed digests.
- Offline, client, server, local integration, and isolated-live classes are independently proven; no production/lab mutation or cutover occurs.
- Ownership/readiness evidence makes every Kubernetes/PV/MinIO resource PASS or FAIL with a rollback owner; no ambiguous dual ownership or `Retain`/`DirectoryOrCreate` false assurance remains.
- `/mnt/data/lab_clip` and `/mnt/data/3t-clip` remain byte-for-byte/worktree-status unchanged; the shared target repository differs only by the exact registered-worktree metadata inventoried in F4, while every source/product edit remains inside the task worktree. No Fleet, consumer research logic, secrets, destructive default S3 operation, or live cutover ships.
- F1-F4 all return unconditional APPROVE against the same final commit, results are surfaced to the caller, and completion is declared only after the caller's explicit `okay`.
