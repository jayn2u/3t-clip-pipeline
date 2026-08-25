# 3T-CLIP Pipeline Infrastructure

Infrastructure-as-code for the LabCLIP k3s cluster (`vis-lab` control-plane+worker,
`ubuntu` worker), split into two layers with a single handoff point between them:

1. **[`ansible/`](ansible/)** — node/OS layer. Installs k3s, the NVIDIA container
   runtime, and prepares local storage directories. Produces a kubeconfig.
2. **[`terraform/`](terraform/)** — cluster resource layer. Consumes that
   kubeconfig and manages everything inside the Kubernetes API: namespaces,
   RBAC, storage, MinIO, Argo Workflows, Tailscale.

```
ansible-playbook playbooks/site.yml   # -> terraform/generated/kubeconfig
        |
        v
terraform apply                       # -> running cluster resources
```

Each directory's README documents its own scope boundary and drift-checking
commands (`ansible-playbook playbooks/preflight.yml` / `terraform plan`). Do
not add Kubernetes manifests to `ansible/` or host-provisioning tasks to
`terraform/` — the split is deliberate; see `ansible/README.md` for why.
