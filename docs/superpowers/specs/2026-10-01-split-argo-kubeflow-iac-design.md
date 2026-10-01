# Split Argo and Kubeflow IaC Design

## Purpose

Replace the single `terraform/` root, which switches between Argo and Kubeflow through `platform_mode`, with two fully independent stacks: `argo/` and `kubeflow/`. Each stack owns its Terraform root, state, private inputs, scripts, tests, and runbook. Changing, planning, or applying one stack must not read or alter files, state, or variables of the other.

Only one stack is deployed on the cluster at a time. Switching platforms means destroying one stack and applying the other.

## Background

- `terraform/` was Argo-only until commit `5b01f5d`. Commit `9640d0a` added `platform_mode` (`argo` default, `kubeflow`), `count` switches on the Argo resources, and `kubeflow_integration.tf`.
- Kubeflow itself is not installed by Terraform. A pinned Kustomize overlay under `kubeflow/overlays/labclip` is rendered and applied by `scripts/render_kubeflow.py`, `prepare_kubeflow_overlay.py`, `apply_kubeflow.py`, and `check_kubeflow.py`.
- Terraform state, generated inputs, and the `.terraform` directory live in the standalone checkout `/mnt/data/3t-clip-pipeline`. This repository is also vendored as the `infra/3t-clip-pipeline` submodule of `lab_clip`, where state and private inputs do not exist.
- The Kubeflow PoC has been reclaimed. The cluster contains only the default k3s components and the Terraform state is empty.

## Scope

In scope:

- Two independent stacks with the layout below.
- Moving every Kubeflow-only file into `kubeflow/` without changing script logic beyond path constants.
- A cross-stack ownership guard.
- Relocating the kubeconfig output to a location shared only through Ansible.
- Updating tests, ignore rules, runbooks, and the one `lab_clip` document that references the Terraform path.

Out of scope:

- Changing Kubeflow script logic, replacing the scripts with a GitOps tool, or simplifying the overlay.
- Deploying either stack (`terraform apply`), or changing the Ansible playbooks beyond the kubeconfig destination.
- Changing LabCLIP pipeline code in the `lab_clip` repository.

## Layout

```
ansible/                      shared host layer, unchanged except kubeconfig destination
argo/
  README.md
  terraform/                  Argo-only root
  scripts/
    prepare_terraform_inputs.py
    bootstrap_minio.sh
  tests/
kubeflow/
  README.md
  terraform/                  Kubeflow-only root
  overlays/labclip/           existing Kustomize overlay
  scripts/
    prepare_terraform_inputs.py
    bootstrap_minio.sh
    apply_kubeflow.py
    check_kubeflow.py
    prepare_kubeflow_overlay.py
    render_kubeflow.py
  tests/
tests/                        Ansible tests only
docs/
README.md
```

All moves use `git mv` so history follows the files. The old `terraform/` and root `scripts/` directories are removed. `docs/validation/` and `docs/superpowers/` stay at the repository root as history.

## Stack contents

### argo/terraform

Derived from the `terraform/` tree at `5b01f5d`, with later changes that are unrelated to Kubeflow carried forward. It contains no `platform_mode`, no `enable_kubeflow_run_bindings`, no `confirm_kubeflow_cache_pv_rebind`, no `labclip_run_namespace`, no `kubeflow_integration.tf`, and no Kubeflow cache data sources. Resources: `argo` namespace, Argo Workflows Helm release, LabCLIP WorkflowTemplate, NVIDIA device plugin, local cache StorageClass and PVs with one claim per PV in `argo`, both MinIO deployments, services and secrets, the MinIO bootstrap, runner RBAC, and the optional Tailscale operator.

The `labclip_workflow_template_path` default moves one directory deeper to remain correct for the standalone checkout (`../../../lab_clip/pipeline/k8s/generated/labclip-train.yaml`). The README documents the override for the submodule layout.

### kubeflow/terraform

Derived from the current `terraform/` tree with the `argo` platform paths removed: no `platform_mode` variable, no `count` switches, no Argo Helm release, no Argo WorkflowTemplate, no Argo-only RBAC. The Kubeflow behavior that was previously selected by `platform_mode = "kubeflow"` is unconditional. `enable_kubeflow_run_bindings` and `confirm_kubeflow_cache_pv_rebind` remain because they gate a real two-stage procedure.

### Shared files are copies

`minio.tf`, `nvidia_device_plugin.tf`, `providers.tf`, `versions.tf`, `prepare_terraform_inputs.py`, and `bootstrap_minio.sh` exist in both stacks as independent copies. No test requires the copies to stay identical, because that would couple the stacks. Each stack README states that these files are intentionally duplicated.

## Independence

- Each stack keeps its own `terraform.tfstate`, `terraform.tfvars`, `*.auto.tfvars.json`, `.terraform/`, and lock file inside its own `terraform/` directory. `.gitignore` lists the patterns for each stack separately.
- Neither stack's files reference the other stack's directory.
- The only shared input is the kubeconfig. Ansible writes it to `ansible/generated/kubeconfig` (`k3s_kubeconfig_fetch_dest` in `ansible/group_vars/all.yml`), and each stack's `kubeconfig_path` default and `prepare_terraform_inputs.py` point there.

### Cross-stack ownership guard

Both stacks create the same cluster objects (the `argo` namespace, MinIO, cache PVs). Applying the second stack over the first would conflict. Each stack therefore labels the `argo` namespace with `labclip.io/iac-stack` set to `argo` or `kubeflow`, and its plan fails when the namespace already exists with the other value.

- A missing namespace passes (fresh cluster).
- A namespace without the label passes (legacy objects created before this change).
- The check reads the namespace through a data source that tolerates absence and fails through a `precondition`, with an error message naming the other stack and the destroy-first procedure.

## Path and reference updates

| Target | Change |
|---|---|
| `.gitignore` | Replace the single `terraform/` and `kubeflow/` patterns with per-stack patterns |
| `ansible/group_vars/all.yml` | `k3s_kubeconfig_fetch_dest` becomes `ansible/generated/kubeconfig` |
| `tests/test_ansible_teardown.py` | Match the new kubeconfig destination |
| `prepare_terraform_inputs.py` (both copies) | Output file and `kubeconfig_path` use the stack's own `terraform/` and the shared Ansible kubeconfig |
| `bootstrap_minio.sh` (both copies) | Kubeconfig fallback path |
| `apply_kubeflow.py`, `check_kubeflow.py`, `prepare_kubeflow_overlay.py`, `render_kubeflow.py` | Update `REPOSITORY_ROOT`, `TERRAFORM_ROOT`, `OVERLAY_ROOT`, and generated-output constants only |
| `README.md`, `argo/README.md`, `kubeflow/README.md` | Commands and paths for the new layout |
| `lab_clip` `docs/agents/iac-submodule-sync.md` | Template path reference, in a follow-up `lab_clip` pull request after the submodule pointer updates |

## Tests

- Existing tests move to the owning stack's `tests/` directory with path constants updated. Ansible tests stay in `tests/`.
- `test_terraform_platform_mode.py` is replaced by stack-independence tests:
  - the Argo root contains no Kubeflow resources, variables, or files;
  - the Kubeflow root contains no `platform_mode` variable and no Argo Helm release;
  - no file under `argo/` references `kubeflow/` and no file under `kubeflow/` references `argo/` as a path (the `argo` namespace name is allowed);
  - the ownership guard exists in both roots with the correct own-stack label.

## Verification

1. All Python tests pass (`unittest` discovery in each stack's `tests/` and in `tests/`).
2. For each Terraform root: `terraform fmt -check`, `terraform init -backend=false`, `terraform validate`.
3. For the Argo root, a read-only `terraform plan` against the empty cluster with stack-local inputs shows only creations for the resource set of `5b01f5d`.
4. `git diff --stat -M` confirms moved files are detected as renames and that Kubeflow script changes are limited to path constants.

`terraform apply` is not part of this change.

## Rollout

Work happens on branch `claude/split-argo-kubeflow-iac` in the worktree `.worktrees/split-argo-kubeflow-iac` of `/mnt/data/3t-clip-pipeline`. A ready-for-review pull request against `develop` is written in Korean. The existing `notify-labclip` workflow then opens the `lab_clip` submodule update. The documentation line in `lab_clip` is fixed in a separate small pull request afterwards.

Existing local state and private inputs in `/mnt/data/3t-clip-pipeline/terraform/` stay untouched. They are not tracked, the state is empty, and the operator creates inputs for the chosen stack from scratch.

## Risks

- Duplicated files can drift. This is accepted in exchange for independence and is documented in the stack READMEs.
- The ownership guard only protects stacks that apply this version. A cluster object created by an older apply carries no label and is treated as unowned.
- Deriving the Argo root from `5b01f5d` can omit a later non-Kubeflow fix. Verification step 3 and a review of the `5b01f5d..HEAD` terraform diff cover this.
