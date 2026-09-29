# 3T-CLIP Pipeline Infrastructure

Infrastructure-as-code for the LabCLIP k3s cluster (`vis-lab` control-plane+worker,
`ubuntu` worker), split across host provisioning, cluster foundations, and a pinned
Kubeflow distribution:

1. **[`ansible/`](ansible/)** — node/OS layer. Installs k3s, the NVIDIA container
   runtime, and prepares local storage directories. Produces a kubeconfig.
2. **[`terraform/`](terraform/)** — cluster foundation and LabCLIP integration.
   Consumes that kubeconfig and manages GPU support, retained cache volumes,
   MinIO, either standalone Argo Workflows or Kubeflow run bindings, pipeline
   credentials, and the optional Tailscale Operator.
3. **[`kubeflow/`](kubeflow/)** — pinned Kubeflow Community Distribution
   26.03.1 overlay plus reviewed apply, drift, readiness, and UI access steps.
   It owns Kubeflow components and the tailnet-only Ingress; Terraform owns the
   Tailscale Operator.

```
ansible-playbook playbooks/site.yml
        |
        v
terraform Kubeflow foundation
        |
        v
approved Kustomize render and apply
        |
        v
terraform LabCLIP run-namespace bindings
```

Each directory's README documents its own scope boundary and drift-checking
commands. For the full Kubeflow install, repeat apply, read-only drift and
readiness checks, and loopback/Tailscale access, see [`kubeflow/README.md`](kubeflow/README.md).
Do not add Kubernetes manifests to `ansible/` or host-provisioning tasks to
`terraform/`; see [`ansible/README.md`](ansible/README.md) for the host-layer
contract.
