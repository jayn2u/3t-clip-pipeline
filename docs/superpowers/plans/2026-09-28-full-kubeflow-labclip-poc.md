# Full Kubeflow LabCLIP PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Leave the official full Kubeflow 26.03.1 platform running on the two LabCLIP hosts so the customer can log in locally and through Tailscale, explore the Central Dashboard, and inspect a real GPU training and official-test run.

**Architecture:** Ansible restores k3s; Terraform manages LabCLIP GPU, MinIO, cache, and Tailscale resources; a pinned Kustomize overlay manages the full Kubeflow distribution. The LabCLIP repository owns a KFP 2.16.1 pipeline that reuses the existing code snapshot, MinIO result contract, and W&B tracking.

**Tech Stack:** k3s v1.36.2+k3s1, Terraform 1.16.4, Ansible, Kubeflow Community Distribution 26.03.1 at commit `f09f3eeaa25cc852665f460497a42b7fc68639ac`, Kustomize 5.8.1, KFP and kfp-kubernetes 2.16.1, Argo Workflows 3.7.3 from Kubeflow, MinIO, W&B, Tailscale.

**Spec:** `docs/superpowers/specs/2026-09-28-full-kubeflow-labclip-poc-design.md`

## Global Constraints

- The PoC uses the upstream default full-platform composition, including Central Dashboard, Pipelines, Notebooks, Katib, Trainer, KServe, and Hub; optional upstream-disabled components stay disabled.
- The upstream tag, commit, and rendered manifest digest are recorded; no floating upstream revision is applied.
- `lab-data`, `lab-code`, and `lab-runs` remain canonical in LabCLIP MinIO; Kubeflow's own artifact store is separate.
- W&B train/eval logging remains required and failures fail the run.
- Existing Argo Helm release, Argo-only RBAC, and `labclip-train` WorkflowTemplate are excluded from the Kubeflow mode; no shared Kubernetes resource has two owners.
- Training scripts keep stem-paired YAML and explicit environment dataset/model selection; no train/eval settings are added as CLI flags.
- No code comments are added. Do not commit credentials, generated kubeconfig, rendered Secret manifests, local `wandb/`, or data.
- Local UI is loopback-only; remote UI is tailnet-only HTTPS on TCP 443 through the Istio gateway, with no Funnel, public NodePort, public LoadBalancer, or public bind.
- The customer's PoC stays running after handoff. Teardown is documented and reviewed separately; MinIO and cache data survive.
- Use `uv run python` and `uv` for LabCLIP Python execution and dependency management. In the IaC repository, use its existing Python/test tools.
- Perform each repository-changing task on a dedicated worktree branch; preserve the configured human Git author and append the actual AI Co-authored-by trailer to each commit. Push and ready-for-review PRs follow successful implementation and verification, not the planning stage.

## Review Focus

- An upstream tag unexpectedly moves or renders different objects: the pinned SHA and manifest-digest checks fail before apply (Task 2).
- A default Dex credential or public gateway slips into the render: rendered-manifest and live login/access checks reject it (Tasks 2 and 5).
- Kubeflow and Terraform claim the same Argo object or node-local PV twice: owner inventory and namespace/PVC tests reject it (Tasks 1 and 3).
- A train task is satisfied from KFP cache or runs on a node without its dataset PVC: compiled IR and live Pod placement checks reject it (Task 4).
- A failed train is displayed as successful, or a test result points at an unverified checkpoint: failure-injection and checksum/metadata checks reject it (Tasks 4 and 6).

---

### Task 1: Split the Terraform platform mode without changing the Argo default

**Files:**
- Modify: `terraform/variables.tf`, `terraform/argo_workflows.tf`, `terraform/rbac.tf`, `terraform/storage.tf`, `terraform/outputs.tf`
- Create: `terraform/kubeflow_integration.tf`
- Test: `tests/test_terraform_platform_mode.py`

**Interfaces:**
- Consumes: existing `nodes`, MinIO Secrets, cache PVs, and `argo` namespace.
- Produces: `platform_mode` (`argo` by default, `kubeflow` for PoC), `labclip_run_namespace`, and a second-stage `enable_kubeflow_run_bindings` switch; exactly one PVC per cache PV and no standalone Argo controller in Kubeflow mode.

- [ ] Add failing tests `test_default_mode_keeps_argo`, `test_kubeflow_mode_has_no_standalone_argo`, and `test_run_cache_claim_has_one_namespace`. Assert the default Argo resources still render, Kubeflow mode omits Argo Helm/WorkflowTemplate/Argo-only RBAC, and run PVCs plus copied credentials target only `labclip_run_namespace` after the second-stage switch.
- [ ] Run `python3 -m unittest tests.test_terraform_platform_mode -v`; confirm the new test fails for the missing mode.
- [ ] Add mode-conditional resources and a distinct Kubeflow integration file. Keep MinIO in its existing namespace; keep cache PV names and `Retain` policy. Do not create a second claim for either PV.
- [ ] Run `terraform -chdir=terraform fmt -check`, `terraform -chdir=terraform validate`, and `python3 -m unittest tests.test_terraform_platform_mode -v`; require zero failures. Run `terraform -chdir=terraform plan` with private inputs for both modes and inspect resource deletions before any apply.
- [ ] Commit only this task's Terraform and test files with the required Co-authored-by trailer.

### Task 2: Pin and render the full Kubeflow distribution with private identity

**Files:**
- Create: `kubeflow/overlays/labclip/kustomization.yaml`, `kubeflow/overlays/labclip/patches/`, `kubeflow/overlays/labclip/kubeflow-tailnet-ingress.yaml`, `scripts/prepare_kubeflow_overlay.py`, `scripts/render_kubeflow.py`
- Modify: `.gitignore`
- Test: `tests/test_kubeflow_render.py`

**Interfaces:**
- Consumes: upstream `github.com/kubeflow/community-distribution/example?ref=f09f3eeaa25cc852665f460497a42b7fc68639ac`; a mode-0600 private identity file.
- Produces: ignored `kubeflow/generated/rendered.yaml`, SHA-256 receipt, object-identity inventory, and private generated Dex patch; no committed or rendered sample credential.

- [ ] Write failing tests `test_release_ref_is_pinned`, `test_private_identity_permissions`, `test_default_dex_login_absent`, `test_gateway_is_cluster_ip`, and `test_render_inventory_is_deterministic`. Assert the pinned ref and commit, mode-0600 input, replacement of sample Dex identity/password, absence of public Service exposure, and stable object identities.
- [ ] Run `python3 -m unittest tests.test_kubeflow_render -v`; confirm expected failures.
- [ ] Implement `prepare_identity(path: Path) -> IdentityMaterial` and `render_distribution(source_ref: str, site_dir: Path, output: Path) -> RenderReceipt`. Use the local Kustomize 5.8.1 client. Generate the private bcrypt hash from an unprinted password using `bcrypt==4.2.1`; never persist plaintext in Terraform state. Fail if the upstream tag does not resolve to the pinned commit. Apply site patches only after inspecting the release's actual Secret, ConfigMap, gateway, and profile names.
- [ ] Run the focused tests and render command. Inspect the rendered kinds/namespaces, digest, owner inventory, and default credential scan without printing Secret bodies. Confirm `kubectl kustomize` exits zero.
- [ ] Commit only the overlay templates, preparation/render tools, tests, and ignore rule.

### Task 3: Reproducible install, drift, and access infrastructure

**Files:**
- Create: `scripts/apply_kubeflow.py`, `scripts/check_kubeflow.py`, `kubeflow/README.md`
- Modify: `terraform/tailscale.tf`, `terraform/README.md`, `README.md`
- Test: `tests/test_kubeflow_apply.py`, `tests/test_kubeflow_access.py`

**Interfaces:**
- Consumes: Task 2 rendered manifest/receipt and Task 1 Terraform mode.
- Produces: bounded CRD/dependency apply, read-only drift report, readiness checks, and the overlay-owned tailnet-only HTTPS Ingress to `istio-ingressgateway` port 80; localhost uses `kubectl port-forward` bound to `127.0.0.1`.

- [ ] Write failing tests `test_apply_rejects_digest_mismatch`, `test_apply_retries_missing_crd_only`, `test_apply_stops_on_field_conflict`, `test_drift_check_is_read_only`, and `test_tailnet_ingress_is_private`. Assert pinned receipt enforcement, at most six attempts for temporarily unavailable CRDs, conflict failure without `--force-conflicts`, read-only drift, and no Funnel/public listener.
- [ ] Run both focused test modules and observe expected failures.
- [ ] Implement `apply_distribution(manifest: Path, receipt: Path, *, max_attempts: int = 6) -> None` and `check_distribution(manifest: Path, receipt: Path) -> DriftReport`. Keep Terraform-owned objects out of the render and verify the owner inventory before apply. Terraform owns the Tailscale operator; Kustomize owns its Ingress. Add them only after inspecting existing tailnet grants; require OAuth input through the current private Terraform mechanism.
- [ ] Run tests, `terraform fmt -check`, `terraform validate`, Ansible syntax checks, and `git diff --check`. Document preflight → Ansible site → Terraform foundation → rendered Kubeflow apply → Terraform namespace integration, plus repeat apply, drift, and separately reviewed teardown.
- [ ] Commit only this task's files.

### Task 4: Build a KFP LabCLIP run that preserves the result contract

**Files in `/mnt/data/lab_clip` worktree:**
- Create: `pipeline/kfp/labclip_pipeline.py`, `pipeline/kfp/run_train.py`, `pipeline/kfp/run_post_eval.py`, `pipeline/submit_kfp.py`, `train/train_itc_sgd_kfp_poc.py`, `configs/train/train_itc_sgd_kfp_poc.yaml`
- Modify: `pyproject.toml`, `uv.lock`, `pipeline/README.md`
- Test: `pipeline/tests/test_kfp_pipeline.py`, `pipeline/tests/test_kfp_runners.py`, `pipeline/tests/test_submit_kfp.py`

**Interfaces:**
- Consumes: existing code-bundle upload, `RunIdentity`, MinIO preflight, `result_sync`, `post_eval_runner`, W&B helpers, pinned LabCLIP container image, and Task 1 user-namespace PVC/Secrets.
- Produces: `build_labclip_pipeline(...)` compiled to KFP 2.16.1 IR, `run_training(settings: KfpTrainSettings) -> TrainPublication`, `run_post_eval(settings: KfpEvalSettings) -> EvaluationPublication`, and submitter `main()` registering a versioned package and creating a run.

- [ ] In a dedicated clean LabCLIP worktree and `codex/full-kubeflow-poc` branch, add failing tests `test_compiled_poc_dag`, `test_gpu_tasks_use_cache_claim`, `test_side_effect_tasks_disable_cache`, `test_post_eval_is_required`, `test_failure_injection_is_isolated`, and `test_submit_uses_unique_result_prefix`. Assert the five-stage dependency chain, one GPU on train/eval, correct node-local PVC and Secret mounts, no execution caching on side-effect steps, mandatory post-evaluation, separate failure injection, unique result prefix, and preserved W&B settings.
- [ ] Run the focused tests to see the expected failures. Pin `kfp==2.16.1` and `kfp-kubernetes==2.16.1` with `uv`; do not add W&B YAML keys.
- [ ] Implement the KFP definition and runners. Stage the uploaded source snapshot into a shared ephemeral workspace before invoking the new stem-paired training script; preserve streaming/final publication and SHA-256 verification. Reuse existing modules instead of reimplementing publication or retrieval metrics. Keep the PoC YAML to a bounded epoch count and explicit CUHK-PEDES dataset selection; do not alter the standard 60-epoch YAML. A small typed KFP artifact contains the verified MinIO model URI and hash, not another full checkpoint copy.
- [ ] Compile the pipeline with `uv run python`; inspect IR for image digest, volumes, Secret references, GPU count, dependency edges, no cache on train/eval/publish, and exit/failure paths. Run focused tests plus affected existing `pipeline/tests` modules, `git diff --check`, and a dry-run submit. Require zero failures.
- [ ] Commit only the LabCLIP PoC files and dependency lockfile, with the required Co-authored-by trailer.

### Task 5: Restore hosts and install the full platform

**Files:**
- Modify only if a verified install defect requires a scoped fix; preserve both repositories' committed artifacts and a local validation receipt outside Git.

**Interfaces:**
- Consumes: Tasks 1–3 committed IaC, private inventory and Terraform inputs, and Task 4 compiled pipeline.
- Produces: two Ready k3s nodes; full Kubeflow 26.03.1 installed with a non-default Dex identity; local and tailnet dashboard URLs.

- [ ] Run the read-only Ansible preflight and collect host RAM/disk, GPU, kernel, and k3s-data-dir evidence. Obtain sudo authorization through an interactive prompt on the operator terminal; do not receive a password in chat.
- [ ] Run Ansible site, then verify both nodes, Kubernetes allocatable resources, RuntimeClass, GPU advertisement, and dynamic StorageClass behavior. Stop on capacity shortfall rather than dropping an agreed Kubeflow product.
- [ ] Run `terraform plan` for Kubeflow mode and inspect exact creates/replacements; apply foundation only after confirming canonical MinIO and retained data paths. Render and inspect the Kubeflow manifest; apply with Task 3's bounded procedure; apply the namespace integration stage.
- [ ] Verify every required Kubeflow namespace and component deployment, PVC binding, Dex login, Central Dashboard navigation, Pipelines/Notebooks/Katib/Trainer/KServe/Hub links, local loopback URL, and Tailscale HTTPS URL with a real browser. Replace or fix only evidence-backed failures, then repeat the failed check.
- [ ] Run read-only drift checks and a repeat Ansible/Terraform/Kubeflow apply. Record exact changes and leave the cluster running.

### Task 6: Customer-visible real run, failure proof, and handoff

**Files:**
- Create or update: `docs/validation/YYYY-MM-DD-full-kubeflow-poc.md`, `kubeflow/README.md`, `/mnt/data/lab_clip/pipeline/README.md` as evidence demands.

**Interfaces:**
- Consumes: Task 5 live dashboard and Task 4 KFP package.
- Produces: normal and intentionally failed KFP run IDs, browser evidence, verified checkpoint/test results, customer URLs, repeat/deploy/drift instructions, and a reviewed but unexecuted teardown procedure.

- [ ] Register the compiled LabCLIP package as a versioned KFP pipeline and submit one short CUHK-PEDES GPU run. In the browser confirm graph, Pod logs, task placement, W&B sync, `best_t2i.pt` SHA-256, and official test JSON/t2i R@1. Do not use API status as the sole evidence.
- [ ] Submit one harmless failure-injection run and verify the failed UI node and durable failure metadata; then confirm the normal run is independently rerunnable.
- [ ] Validate the customer's localhost and Tailscale browser paths, including Dex redirects and secure cookies; document the exact URLs without credentials. Record the observed menu behavior rather than assuming every optional application is functional.
- [ ] Run final focused tests, Terraform validate/plan, Ansible syntax check, manifest digest/drift check, and `git diff --check`. Commit evidence/documentation after fresh verification. Push intended branches and open ready-for-review Korean PRs with change, rationale, impact, and verification; attach every created PR to the chat.
- [ ] Hand off a concise walkthrough and private credential retrieval path. Keep Kubeflow and the cluster running for customer review; do not execute teardown until separately directed.
