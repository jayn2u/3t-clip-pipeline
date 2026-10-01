# Kubeflow infra

Terraform root and helper scripts for the Kubeflow stack's cluster foundation.

| Area | Owns |
|---|---|
| `infra/` | NVIDIA device plugin, MinIO, retained cache volumes, run-namespace bindings, and the optional Tailscale operator, installed with Terraform. |
| `platform/` | The pinned Kubeflow Community Distribution, installed with Kustomize and the `prepare`, `render`, `apply`, and `check` tools. |

`platform/scripts/apply.py` and `platform/scripts/check.py` read the owner inventory from `infra/terraform` with `terraform console`, so the Terraform foundation must be applied first.

Run the infra tests from this directory:

```bash
uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests
```
