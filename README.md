# 3T-CLIP Pipeline Infrastructure

Infrastructure-as-code for the LabCLIP k3s cluster (`vis-lab` control-plane+worker, `ubuntu` worker). Two independent platform stacks share one host layer. Deploy only one stack on the cluster at a time.

| Directory | Owns |
|---|---|
| [`ansible/`](ansible/) | Node and OS layer: k3s, NVIDIA container runtime, local storage directories. Writes the shared kubeconfig to `ansible/generated/kubeconfig`. |
| [`argo/`](argo/) | Argo Workflows platform: its own Terraform root, scripts, tests, and runbook. |
| [`kubeflow/`](kubeflow/) | Pinned Kubeflow Community Distribution 26.03.1: its own Terraform root, Kustomize overlay, scripts, tests, and runbook. |

The stacks do not read each other's files or state. Files such as `minio.tf`, `nvidia_device_plugin.tf`, `prepare_terraform_inputs.py`, and `bootstrap_minio.sh` are intentionally duplicated in both stacks. Each stack labels the `argo` namespace with `labclip.io/iac-stack`, and a plan fails when the other stack owns it. To switch from Argo to Kubeflow, destroy the Argo stack with `terraform -chdir=argo/terraform destroy`, then apply the Kubeflow stack. To switch from Kubeflow to Argo, first remove the Kustomize-applied Kubeflow distribution using the reviewed teardown in [`kubeflow/README.md`](kubeflow/README.md#teardown-boundary), then destroy the Terraform stack with `terraform -chdir=kubeflow/terraform destroy`, and only then apply the Argo stack.

Do not add Kubernetes manifests to `ansible/` or host-provisioning tasks to a stack's Terraform root; see [`ansible/README.md`](ansible/README.md) for the host-layer contract.
