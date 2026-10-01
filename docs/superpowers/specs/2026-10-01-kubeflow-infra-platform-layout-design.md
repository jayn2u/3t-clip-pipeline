# Kubeflow infra/platform Layout Design

## Purpose

Reorganize `kubeflow/` so that its two installation mechanisms live in two named areas: `infra/` (Terraform and its helper scripts) and `platform/` (the pinned Kustomize distribution and the tooling that renders, applies, and checks it). A code review recommended this layout. It removes the single-overlay nesting (`overlays/labclip/`), the two `generated/` directories, and the mixing of Terraform helpers with Kustomize tooling in one `scripts/` directory.

This change moves and renames files. It must not change what Kubeflow or Terraform installs.

## Background

- `kubeflow/README.md` already documents the split: Terraform owns the GPU plugin, MinIO, cache PVs, run bindings, and the Tailscale operator; the pinned Kustomize overlay owns the Kubeflow distribution and the tailnet Ingress.
- Today `kubeflow/scripts/` mixes `prepare_terraform_inputs.py` and `bootstrap_minio.sh` (Terraform) with `apply_kubeflow.py`, `check_kubeflow.py`, `prepare_kubeflow_overlay.py`, and `render_kubeflow.py` (Kustomize).
- Private output is written to two places: `kubeflow/generated/` (identity, rendered manifest, receipt, inventory, approval) and `kubeflow/overlays/labclip/generated/` (site patches).
- `prepare_kubeflow_overlay.write_site_patches` reads its templates from fixed locations inside the overlay directory: `patches/dex-config-template.yaml` and `kubeflow-tailnet-ingress.yaml`.
- `lab_clip` runs a no-cluster gate on every IaC commit (`scripts/iac_submodule_sync.py`). The gate names each stack's Terraform root and unit-test directories explicitly, so a layout change must update the gate in the same rollout.
- Kubeflow is currently reclaimed and not deployed, so this change is safe to make now and has no cluster effect.

## Scope

In scope:

- The new `kubeflow/` layout below, applied with `git mv` so history follows the files.
- Renaming the four Kustomize tooling scripts to `prepare.py`, `render.py`, `apply.py`, and `check.py`.
- Updating path constants, imports between those scripts, the template lookup in `write_site_patches`, tests, ignore rules, runbooks, and the root README.
- Updating the `lab_clip` synchronization gate, its tests, and its runbook for the new paths.

Out of scope:

- Any change to the rendered Kubeflow manifest, the Terraform resources, the ownership guard, or the `argo/` stack.
- Renaming the Trainer patch file (it keeps `trainer-controller-manager-node-selector.yaml`) or converting the identity file from JSON to YAML (it stays `identity.json`).
- Deploying Kubeflow or running `terraform apply`.

## Target layout

```
kubeflow/
  README.md                      stack runbook (commands updated)
  infra/
    README.md                    short pointer plus the infra/platform ownership table
    terraform/                   unchanged contents, new location
    scripts/
      __init__.py
      prepare_terraform_inputs.py
      bootstrap_minio.sh
    tests/
      test_terraform_inputs.py
      test_kubeflow_terraform.py
      test_stack_independence.py
  platform/
    kustomization.yaml
    patches/
      ingress-gateway-service.yaml
      trainer-controller-manager-node-selector.yaml
    templates/
      dex-config-template.yaml
      kubeflow-tailnet-ingress.yaml
    generated/                   untracked: identity.json, rendered.yaml, receipt.json,
                                 inventory.json, approval.json, and the four site patches
    scripts/
      prepare.py
      render.py
      apply.py
      check.py
    tests/
      test_kubeflow_render.py
      test_kubeflow_apply.py
      test_kubeflow_access.py
      test_script_paths.py
```

Deviations from the review's tree, with reasons:

- `kubeflow-tailnet-ingress.yaml` is also a rendered template (it contains `__TAILNET_HOSTNAME__`), so it moves to `templates/` next to the Dex template.
- The Trainer patch keeps its current file name. The rename is cosmetic and would touch more references.
- The identity file stays JSON because the scripts and tests read it as JSON.

## Path and code changes

| Area | Change |
|---|---|
| `platform/kustomization.yaml` | Same content as `overlays/labclip/kustomization.yaml`. Its `generated/...` and `patches/...` entries stay valid because the file moves to the directory that now holds both. |
| `platform/scripts/prepare.py` | `OVERLAY_ROOT` becomes the `platform/` directory. `write_site_patches` reads `templates/dex-config-template.yaml` and `templates/kubeflow-tailnet-ingress.yaml` instead of `patches/dex-config-template.yaml` and the overlay-root ingress file. The identity default becomes `platform/generated/identity.json`. This is the only logic change in the scripts. |
| `platform/scripts/render.py`, `apply.py`, `check.py` | Default manifest, receipt, inventory, and approval paths point at `platform/generated/`. `TERRAFORM_ROOT` becomes `kubeflow/infra/terraform`; the dependency of `apply.py` and `check.py` on Terraform's owner inventory (`terraform console`) and on the ownership check is unchanged and documented. Imports between the scripts use the new module names. |
| `infra/scripts/prepare_terraform_inputs.py`, `bootstrap_minio.sh` | The kubeconfig path resolves to the shared `ansible/generated/kubeconfig` from the new depth. |
| `infra/terraform/variables.tf`, `terraform.tfvars.example` | The `kubeconfig_path` default becomes `../../../ansible/generated/kubeconfig`. |
| Tests | Each test file moves next to what it tests and keeps its assertions. Path constants and module names change. The stack-independence test keeps scanning the whole `kubeflow/` tree for references to `argo/`. |
| `.gitignore` | `kubeflow/generated/` and `kubeflow/overlays/labclip/generated/` become `kubeflow/platform/generated/`. The Terraform state, `.terraform`, `tfvars`, and `*.auto.tfvars.json` patterns move under `kubeflow/infra/terraform/`. |
| `tests/test_kubeconfig_contract.py` (root) | The Kubeflow Terraform root and its expected ignore patterns use the new paths. |
| READMEs | `README.md`, `kubeflow/README.md`, and the Terraform README use the new commands and relative links. |

The generic module names `apply`, `check`, `render`, and `prepare` are loaded from `platform/scripts/`, which is put on `sys.path` by the scripts themselves and by the tests. No script or test puts both `infra/scripts` and `platform/scripts` on `sys.path` at the same time.

## lab_clip synchronization gate

`lab_clip` `scripts/iac_submodule_sync.py` describes each stack with its Terraform root and its unit-test directories:

- `argo`: Terraform root `argo/terraform`, tests in `argo/`.
- `kubeflow`: Terraform root `kubeflow/infra/terraform`, tests in `kubeflow/infra/` and `kubeflow/platform/`.

The gate runs `fmt`, `init`, and `validate` for each Terraform root and the unit tests in each test directory. The LabCLIP compatibility check stays on the Argo root. `docs/agents/iac-submodule-sync.md` and the gate's tests change accordingly.

## Verification

1. Python tests pass in each area, run from its own directory so that the right modules are imported:
   - repository root (`tests/`), `argo/`, `kubeflow/infra/`, and `kubeflow/platform/`.
   - Command: `uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests`.
2. `terraform fmt -check`, `terraform init -backend=false`, and `terraform validate` pass for `argo/terraform` and `kubeflow/infra/terraform`.
3. A read-only `terraform plan` with synthetic inputs for `kubeflow/infra/terraform` still reports `18 to add, 0 to change, 0 to destroy`, the same as before the move.
4. `git diff -M --stat` shows the moved files as renames, and the rendered Kubeflow manifest produced by the render test is unchanged.
5. In `lab_clip`, the full test suite passes and a check against the new IaC tree passes for the compatibility check and both Terraform roots' `fmt`.

`terraform apply` and any cluster write are not part of this change.

## Rollout

Order matters because the synchronization gate reads these paths and locks the sync when it fails:

1. Open and merge the `lab_clip` gate pull request first. The gate does not run until the IaC repository's `develop` receives a push, so nothing breaks in between.
2. Open and merge the IaC pull request. Its merge triggers the synchronization with the new gate. A green run updates the `lab_clip` submodule pointer.
3. If the run fails and locks `IAC_SYNC_STATE`, fix the gate and recover with the `workflow_dispatch` input described in the runbook.

Both pull requests are written in Korean and opened ready for review against `develop`. Subagents used for review run on the Sonnet model.

## Risks

- A path missed in a README or test is caught only by the checks above. A final grep for `overlays/labclip`, `kubeflow/terraform`, `kubeflow/generated`, `apply_kubeflow`, `check_kubeflow`, `render_kubeflow`, and `prepare_kubeflow_overlay` must return only historical documents under `docs/superpowers/` and `docs/validation/`.
- The generic script names could collide if another directory is ever added to `sys.path`. The layout keeps them in one directory and the tests assert it.
- The gate and the IaC change land in two repositories. The rollout order above removes the lock risk; if the order is reversed, the recovery path from the previous incident applies.
