# Deployment ownership

The platform adapter is render-only. It never reads a kubeconfig, applies resources, adopts
existing resources, or creates Secrets.

Helm exclusively renders the pinned Argo Workflows, Node Feature Discovery, and NVIDIA device
plugin releases from checksum-verified archives in `.cache/helm/charts`. The repository-local
Helm config, cache, and data homes are mandatory; user-global Helm state is never consulted.

Kustomize exclusively renders the `three-t-pipeline` namespace, runner Role-based RBAC,
NetworkPolicies, local StorageClass/PV/PVC, and MinIO Deployment and Service. Storage capacity,
local path, storage class, and node hostname are example-overlay inputs and must be reviewed for
each environment. `Retain` is not a backup or adoption policy.

The Tailscale file in the example overlay is deliberately excluded from its kustomization. It
only describes an Ingress for a separately installed operator; this adapter installs no
Tailscale chart, controller, image, or credentials. Enabling it is a separate reviewed overlay
change.

`scripts/render-platform.sh` verifies all three archive hashes, renders only local archives,
combines Helm and Kustomize output deterministically, creates a one-owner inventory, rejects
mutable images and floating references, and invokes the packaged local schema validator. The
render and inventory are review artifacts, not authorization to mutate a cluster.
