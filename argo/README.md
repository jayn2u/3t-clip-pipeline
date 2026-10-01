# Argo Workflows stack

Independent Terraform root for standalone Argo Workflows, the LabCLIP WorkflowTemplate, MinIO, the NVIDIA device plugin, retained cache volumes, runner RBAC, and the optional Tailscale operator. It shares nothing with `kubeflow/` except the host layer under `ansible/`.

Run the commands below from the repository root.

```bash
python3 argo/scripts/prepare_terraform_inputs.py
terraform -chdir=argo/terraform init
terraform -chdir=argo/terraform plan
terraform -chdir=argo/terraform apply
```

`argo/terraform/terraform.tfvars` holds the MinIO root credentials and stays untracked. `prepare_terraform_inputs.py` writes `argo/terraform/terraform.generated.auto.tfvars.json` and points `kubeconfig_path` at `ansible/generated/kubeconfig`.

When this repository is vendored as a submodule of `lab_clip`, set `labclip_workflow_template_path` to the checked-in `pipeline/k8s/generated/labclip-train.yaml` of that checkout.

Tests run from the `argo/` directory so that its own `scripts` package is imported:

```bash
uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests
```

Files such as `minio.tf`, `nvidia_device_plugin.tf`, `prepare_terraform_inputs.py`, and `bootstrap_minio.sh` are intentionally duplicated in `kubeflow/`. This stack never reads `kubeflow/`, and the Kubeflow stack never reads this directory.

Stack state lives in `terraform/` and is untracked. Details of the resources and the MinIO bootstrap are in [`terraform/README.md`](terraform/README.md). The `argo` namespace carries `labclip.io/iac-stack=argo`; if the Kubeflow stack is deployed, destroy it first.
