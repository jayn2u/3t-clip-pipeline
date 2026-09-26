# LabCLIP infrastructure lifecycle validation

Status: complete. Real deployment, repeat application, GPU/storage verification,
and final reclamation were verified on 2026-09-26–27. The final cluster is
removed; retained MinIO and cache data remains on both hosts.

## Scope and acceptance

Deploy the Ansible host layer and Terraform cluster layer against the two real
LabCLIP hosts, verify the consumer contract in `/mnt/data/lab_clip`, and remove
the deployment while retaining existing MinIO objects and caches. Validate a
repeat apply and the teardown path. Required runtime evidence includes both
nodes Ready, one schedulable GPU per node, actual GPU execution, node-affined
cache consumption, S3 access, Argo execution, required Secrets/RBAC, and the
`labclip-train` WorkflowTemplate. Optional Rancher and Tailscale are excluded from
the core acceptance deployment.

## Initial state and authorization

The initial assumption that K3s had already been removed was contradicted by
live inspection: both nodes were Ready in an 80-day-old K3s v1.36.2+k3s1 cluster.
The operator explicitly confirmed that this existing cluster is the target and
may be interrupted. Host data must be preserved.

Initial findings:

- Both host GPUs work through `nvidia-smi`; Kubernetes advertises zero GPUs.
- No NVIDIA device-plugin DaemonSet is installed.
- The existing Terraform deployment has Argo v3.5.11, whereas LabCLIP specifies
  v4.0.7. Chart 1.0.20 declares appVersion v4.0.7 in the upstream Helm index.
- The `argo` namespace lacks cache PVCs, pipeline Secrets, and the training
  WorkflowTemplate.
- Existing Terraform MinIO Deployments use the `minio` namespace and separate
  `minio-data` directories. The consumer uses Services in `argo` and the original
  data paths below.
- The original teardown playbook fails syntax checking because `when` is placed
  at Play scope.
- Running the original preflight with `--check` skips its GPU command and then
  fails the GPU assertion; despite its documentation, normal preflight contains
  host-changing tasks.
- `terraform validate` passes on the original configuration. This is not proof
  of consumer compatibility.

## Preserved data

Read-only host checks verified x86_64, synchronized NTP, working NVIDIA drivers,
and enabled IPv4 forwarding/bridge netfilter on both hosts. `vis-lab` has 31 GiB
RAM and `/mnt/data` on a mounted ext4 data filesystem with about 420 GiB free;
`ubuntu` has 251 GiB RAM and `/data/jayn2u` on the `/data` ext4 filesystem with
about 16 TiB free. The worker's persistent mount is `/data`, not a separate mount
at `/data/jayn2u`.

All six paths are retained:

| Host | Path |
| --- | --- |
| vis-lab | `/mnt/data/minio-code` |
| vis-lab | `/mnt/data/minio-data` |
| vis-lab | `/mnt/data/labclip-cache` |
| ubuntu | `/data/jayn2u/minio` |
| ubuntu | `/data/jayn2u/minio-data` |
| ubuntu | `/data/jayn2u/labclip-cache` |

The original ML asset store is approximately 1.2 TB. No bulk copy or purge is
part of this procedure. A private recovery directory outside the repository
contains the original Terraform state/configuration, Kubernetes Secret and
resource exports, kubeconfig, server/agent configuration archives, and a
consistent SQLite snapshot. The snapshot passed `PRAGMA integrity_check`.
These recovery artifacts contain credentials and must not be published.

## Initial removal evidence

- Applied an inspected, saved destroy plan from an isolated copy of the original
  Terraform configuration: **15 resources destroyed**, exit 0. Both managed
  namespaces disappeared and Terraform state became empty.
- After correcting the teardown syntax, both K3s uninstall scripts succeeded.
  Server and worker services became inactive, both K3s binaries disappeared,
  the old server datastore was removed, and TCP 6443 stopped listening.
- That first Ansible execution still exited 2: the final delegated kubeconfig
  cleanup could not resolve `k3s_kubeconfig_fetch_dest`. This reproduced the
  difference between `ansible-inventory` and playbook variable loading; explicit
  shared-variable loading is required.
- After adding explicit variable loading, repeated the same full teardown:
  exit 0, zero changes, zero failed or unreachable hosts. Missing uninstall
  scripts were correctly skipped.
- Compared file path, size, and modification-time inventories before and after
  removal (485,945 initial entries). All differences were in `.minio.sys`
  operational state. User-object and cache entries were identical. This is a
  metadata-preservation check, not a full bytewise hash of 1.2 TB.

## Clean bootstrap and first cluster apply

- Clean Ansible bootstrap completed with exit 0. `vis-lab`: 35 tasks OK, 6
  changes; `ubuntu`: 29 tasks OK, 4 changes; zero failed/unreachable hosts.
- Both fresh nodes became Ready at v1.36.2+k3s1, with containerd
  2.3.2-k3s2. RuntimeClass `nvidia` exists and the three K3s system Pods run.
- Repeated the full Ansible installation: exit 0, zero changes and zero failures
  on both hosts.
- The fresh Terraform plan contained 24 creates and no deletes. First apply
  installed the Argo/NVIDIA charts, both MinIO Deployments, cache resources,
  Secrets, RBAC and WorkflowTemplate. Both nodes advertised one GPU.
- First Terraform apply exited 1 because LabCLIP's MinIO bootstrap rejected the
  legacy root username/password length (13 characters versus the required 16).
  Application credentials met the requirement. Values were not printed. This
  is a real consumer-contract failure, despite healthy Pods and valid HCL.
- Seven local regression/helper/smoke-manifest tests passed after removing a
  teardown-test assumption that the live generated kubeconfig must be absent.

Further real bootstrap failures were isolated and corrected before final
acceptance:

- The pinned MinIO client download URL returned HTTP 410. The operator already
  has the exact required release and SHA-256; the wrapper now selects an explicit
  path or the local `mc` executable and retains LabCLIP's version/hash checks.
- One generated application secret began with a dash, which the consumer's
  `mc` command interpreted as a flag. New secrets use an alphanumeric prefix;
  explicit repair preserves safe credentials and all account identities.
- The Argo controller's task-result deletion check returned `no`. Added the
  namespace-scoped cleanup Role/Binding from the LabCLIP contract; the live
  authorization check then returned `yes`.
- Review found missing MinIO rollout on root-credential changes and a cache
  capacity assertion that ignored retained cache usage. Both were corrected;
  the cache fix's live Ansible repeat again completed with zero changes.

## K3s container storage placement

The actual LabCLIP image contains 8,630,203,045 compressed bytes. The server's
root filesystem had only 22,758,309,888 bytes available, before retaining both
compressed content and unpacked snapshots. To give the workload sufficient
storage headroom, K3s state and image storage were moved to dedicated
directories on the existing data filesystems:

- `vis-lab`: `/mnt/data/labclip-k3s`
- `ubuntu`: `/data/jayn2u/labclip-k3s`

This is a clean replacement, not a copy of a running datastore. Terraform
successfully destroyed all 26 trial resources, and the previous default-path
K3s installation was removed with a verified snapshot of the teardown playbook
(exit 0). Existing MinIO/cache data remains outside the K3s directories.

The new path guard resolves symlinks, rejects mount roots and overlap with
configured cache/MinIO paths, and refuses silent data-directory changes on an
installed cluster. Global playbook aborts protect both the confirmation and
all-host path-validation steps.

Custom-directory bootstrap completed with exit 0 and both nodes Ready. The
full repeat also exited 0 with zero changes (vis-lab: 56 tasks OK; ubuntu: 50).
The full Terraform apply then succeeded, including both MinIO bootstraps;
the subsequent plan exited 0 with `No changes`.

Before teardown, both cache PVCs were Bound to their intended node-affined PVs. The
required Secret names and key sets were verified without printing values, and
all Services were ClusterIP. The code hostPort was restricted to loopback.

The actual LabCLIP `s3_verify.py` passed independently against both stores,
checking object size, SHA-256 metadata, and downloaded bytes. Its isolated
markers belonged to the runtime workflow's unique prefix and were removed
with that workflow's receipt.

## Final runtime acceptance

All three real Argo Workflows reached `Succeeded`:

| Workflow suffix | Verified result |
| --- | --- |
| `cd7dac9f9fe84c30` | Cold pull plus CUDA, PVC and S3 checks; 66 minutes 17 seconds including the 8.63 GB image download |
| `9a869291674b4cc0` | SHA-256 object metadata and body checks on all eight node/bucket pairs |
| `e99bf586ea2d46f6` | Final operator command exited 0; 77 seconds of workflow runtime; both GPUs, both PVCs, eight S3 checks/deletions, six existing-object reads |

The image was the actual pinned LabCLIP image
`ghcr.io/jayn2u/labclip:0.0.2@sha256:a12c5555f33c9da84ea5d5049872b1ba95f0acf3aaf4719ba6725a9639af4d4d`.
CUDA matrix multiplication ran on the RTX 5070 Ti and RTX A6000. Each workload
wrote, read and removed its unique PVC marker on its assigned node. Each node
exercised `lab-code`, `lab-data`, `lab-runs` and `argo-artifacts` through the
non-root pipeline users. The final worker verified HEAD length/SHA-256 metadata,
downloaded bytes, deletion and subsequent HEAD 404.

The actual LabCLIP `s3_verify.py` also passed on all eight retained markers from
the second run. Existing objects in `lab-code`, `lab-data` and `lab-runs` were
read from both nodes: six successful bounded reads, without empty/access-denied
skips. These are sampled reads of up to 4096 bytes, not whole-store hashing.

The second Workflow exposed a receiver bug despite successful workload checks:
the receiver expected existing-object evidence at the wrong JSON level. It was
fixed against the real saved receipt, covered by regression tests, and the final
fresh command exited 0. The final suite has **27 passing tests**. Terraform
format/validation, all three Ansible playbook syntax checks, and `git diff --check`
passed. The checked-in training WorkflowTemplate also passed Argo offline lint
and was installed in the real cluster.

## Final reclamation and preservation

- First and second run cleanup each deleted exactly eight receipt-owned S3
  markers and verified HEAD 404. The final run deleted its own eight markers.
- Completed smoke Pod objects initially held PVC protection. After saving the
  three Workflow receipts, deleting only those completed smoke Workflows released
  the claims. No finalizer was forcibly removed. This order is documented in the
  Terraform README.
- Final Terraform destroy exited 0: **26 resources destroyed**, state empty.
- Final custom-directory Ansible teardown exited 0 on both nodes; a complete
  repeat exited 0 with **zero changes** on both nodes.
- Both service units report `LoadState=not-found`, `ActiveState=inactive`.
  Both K3s binaries, both dedicated state directories, the legacy default state
  directories and generated kubeconfig are absent. TCP 6443 has no listener;
  the loopback MinIO endpoint refuses connections.
- Final inventories contain 486,027 file entries versus 485,945 initially.
  Excluding `.minio.sys` operational metadata and only the three exact smoke-run
  prefixes, both host comparisons exit 0: all retained file paths, sizes and
  modification times are identical. Differences are confined to those excluded
  operational/test paths; no user-object or cache entry was lost.

## Independent Ansible-only verification on 2026-09-27

Starting again from the reclaimed hosts, Ansible preflight passed as an
unprivileged user on both hosts with `changed=0` and no failures. The real
`site.yml` then installed k3s v1.36.2+k3s1: `vis-lab` had 48 tasks OK, 2
changes, and `ubuntu` had 42 tasks OK, 1 change. Both nodes became `Ready`.
The generated kubeconfig was mode 0600, `RuntimeClass/nvidia` existed, the
generated containerd configurations on both nodes referenced
`/usr/bin/nvidia-container-runtime`, and all three default `kube-system`
Deployments became Available.

A second real `site.yml` run passed with **zero changes** on both hosts
(`vis-lab` 56 OK; `ubuntu` 50 OK). Ansible teardown with explicit confirmation
then removed both installations; a complete repeat passed with **zero changes**
(`vis-lab` 8 OK; `ubuntu` 7 OK). Both custom k3s data directories, binaries,
and the fetched kubeconfig are absent; TCP 6443 has no listener and Terraform
state remains empty.

This independent cycle created no Terraform resources. Complete before/after
inventories for all six preserved MinIO/cache roots match **exactly** across
486,027 files by path, size, and modification time, with no exclusions. This
confirms that the Ansible host layer can bootstrap and reclaim the real hosts
without changing those retained files. Logs and inventories are in the private
recovery directory named below.

Recovery logs, plans, receipts, inventories and protected credential backups are
outside Git in `/mnt/data/labclip-lifecycle-recovery/20260926`. Stable deployment
inputs remain in the ignored mode-0600 generated Terraform input file.
The temporary sudo credential file used for this exercise was removed. Both
NVIDIA drivers and the installed container runtimes still respond normally.

## Verification boundaries

This verifies the real infrastructure with the LabCLIP image and storage
consumer, not full model-training convergence or accuracy. Rancher and Tailscale
were intentionally excluded as optional components. The Helm controller is
named `argo-workflow-controller`; its service account and the consumer-facing
`argo-server` Service retain the required contracts.

Startup-only CNI/readiness warnings resolved as the Pods became healthy. The
Ubuntu 5.15 host reported its existing lack of tmpfs `noswap` support; changing
the host kernel was outside this deployment exercise. No failing or pending
system Pods remained at acceptance. Data preservation was checked by exhaustive
file metadata comparison plus sampled S3 reads, not a bytewise hash of 1.2 TB.

## Upstream references checked

- [K3s alternative runtime detection](https://docs.k3s.io/advanced): K3s detects
  the NVIDIA runtime on its service PATH; replacing the generated containerd
  template is not needed for this deployment.
- [Argo Helm index](https://argoproj.github.io/argo-helm/index.yaml): chart
  1.0.20 declares Argo Workflows v4.0.7.
