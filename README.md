# 3T-CLIP Pipeline Infrastructure

Infrastructure-as-code for the LabCLIP k3s cluster (`vis-lab` control-plane+worker, `ubuntu` worker). Two independent platform stacks share one host layer. Deploy only one stack on the cluster at a time.

| Directory | Owns |
|---|---|
| [`ansible/`](ansible/) | Node and OS layer: k3s, NVIDIA container runtime, local storage directories. Writes the shared kubeconfig to `ansible/generated/kubeconfig`. |
| [`argo/`](argo/) | Argo Workflows platform: its own Terraform root, scripts, tests, and runbook. |
| [`kubeflow/`](kubeflow/) | Pinned Kubeflow Community Distribution 26.03.1. `kubeflow/infra/` holds its Terraform root and helpers; `kubeflow/platform/` holds the Kustomize distribution and its prepare, render, apply, and check tools. |

The stacks do not read each other's files or state. Files such as `minio.tf`, `nvidia_device_plugin.tf`, `prepare_terraform_inputs.py`, `bootstrap_minio.sh`, and `minio_bootstrap.py` are intentionally duplicated in both stacks. In `kubeflow/`, these live under `infra/`. Each stack labels the `argo` namespace and the `labclip-local-cache` StorageClass with `labclip.io/iac-stack`, and a plan fails when the other stack owns either object. To switch from Argo to Kubeflow, destroy the Argo stack with `terraform -chdir=argo/terraform destroy`, then apply the Kubeflow stack. To switch from Kubeflow to Argo, first remove the Kustomize-applied Kubeflow distribution using the reviewed teardown in [`kubeflow/README.md`](kubeflow/README.md#teardown-boundary), then destroy the Terraform stack with `terraform -chdir=kubeflow/infra/terraform destroy`, and only then apply the Argo stack.

Do not add Kubernetes manifests to `ansible/` or host-provisioning tasks to a stack's Terraform root; see [`ansible/README.md`](ansible/README.md) for the host-layer contract.
