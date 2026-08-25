# Terraform — cluster resource layer

Owns everything inside the Kubernetes API: namespaces, RBAC, PersistentVolumes,
MinIO Deployments, and the Argo Workflows / Tailscale operator Helm releases.
It does **not** touch hosts, install packages, or run k3s itself — that is
`../ansible`'s job. See `../ansible/README.md` for the scope boundary.

This is a bare-metal cluster with no cloud provider: Terraform's `kubernetes`,
`helm`, and `kubectl` providers all point at a kubeconfig that already exists
(produced by Ansible), and manage resources on that existing API server.

## Usage

```bash
cd ../ansible && ansible-playbook playbooks/site.yml   # produces generated/kubeconfig
cd ../terraform
cp terraform.tfvars.example terraform.tfvars           # fill in real secrets
terraform init
terraform plan
terraform apply
```

`generated/kubeconfig` and `terraform.tfvars` are both gitignored.

## Checking for drift

```bash
terraform plan
```

If this ever proposes destroying and recreating a resource that is known-good
in the live cluster, the code is wrong, not the cluster — reconcile with
`terraform import` rather than applying a destructive plan.

## File map

| File | Owns |
|---|---|
| `namespace.tf` | `argo`, `minio` namespaces |
| `rbac.tf` | `labclip-runner` ServiceAccount/ClusterRole/Binding |
| `storage.tf` | `local-cache` StorageClass, per-node local PersistentVolumes |
| `minio.tf` | Per-node MinIO Deployment/Service/Secret |
| `argo_workflows.tf` | Argo Workflows via the upstream Helm chart (owns its own CRDs) |
| `tailscale.tf` | Tailscale operator via Helm, gated by `enable_tailscale` |

## Migrating from the old hand-applied manifests

If a resource already exists in the cluster from a previous manual
`kubectl apply`, `terraform apply` will try to create it again and fail with
"already exists". Reconcile before applying:

```bash
terraform import kubernetes_namespace.argo argo
terraform import kubernetes_cluster_role.labclip_runner labclip-runner
# ... one import per pre-existing resource, then:
terraform plan   # must show no changes before trusting apply
```

Do not skip the "no changes" check — a plan that still shows a diff after
import means the code doesn't match reality yet, and applying it would mutate
a resource you didn't mean to touch.

## State backend

Starts on local state (`backend.tf`), fine for a single operator. Once more
than one person runs `apply`, switch to the commented S3 backend in
`backend.tf`, pointed at the cluster's own MinIO — no new infrastructure
needed, just an extra bucket.
