# Full Kubeflow LabCLIP PoC Design

## Purpose and customer experience

Deploy the pinned Kubeflow Community Distribution on the existing two-host LabCLIP hardware so the customer can log in to the Central Dashboard, explore its principal applications, and personally start and inspect a real LabCLIP training pipeline. The customer will access the platform from the control-plane computer through localhost and from a laptop through a private Tailscale HTTPS name. Leave the environment running for review. Keep W&B enabled.

The PoC demonstrates the platform experience, not a claim that its UI is superior to the existing Argo, Rancher, W&B, and MinIO combination. A successful installation alone does not satisfy the PoC: login, navigation, a GPU run, its official test evaluation, and an observed failure path must work in the browser.

## Scope

Use the official `26.03.1` Community Distribution's default full-platform composition, including Central Dashboard, Pipelines, Notebooks, Katib, Trainer, KServe, Hub, and their required shared services. Optional components excluded by the upstream default composition, such as Knative Eventing, are outside this PoC. Pin the upstream source to the exact release tag and record its commit and rendered-manifest digest. Do not use a floating `master` branch or default credentials.

Keep LabCLIP's canonical `lab-data`, `lab-code`, and `lab-runs` objects in the existing MinIO installations. Use the distribution's own S3-compatible artifact store for Kubeflow platform state and Pipeline artifacts during the PoC. Preserve W&B as the training and evaluation metrics store. Avoid copying full checkpoints into multiple artifact stores unless required for a specific UI demonstration; a typed KFP artifact may record a verified URI and metadata while the checkpoint remains in `lab-runs`.

## Infrastructure ownership

The existing Ansible layer owns host preparation, NVIDIA runtime, k3s installation, and kubeconfig generation. The existing Terraform layer owns the GPU device plugin, LabCLIP MinIO deployments and credentials, retained local cache PVs, and the optional Tailscale operator. The pinned Community Distribution plus site-specific Kustomize overlays own Kubeflow namespaces, CRDs, Argo controller, Istio, Dex, OAuth2 Proxy, KFP, and other platform objects. No Kubernetes object may be applied by both Terraform and Kustomize.

The current Terraform Argo Helm release, Argo-only RBAC, and LabCLIP WorkflowTemplate must be disabled in the full-Kubeflow PoC composition. The Kubeflow release supplies the Argo controller version it supports. LabCLIP's existing Hera/Argo implementation remains in Git for comparison and rollback, but it is not simultaneously installed into the PoC cluster. The current `argo` namespace can continue to host LabCLIP MinIO resources if its object ownership remains distinct from Kubeflow's namespaces. Bind each existing node-local cache PV to exactly one PVC in the Kubeflow user's run namespace; do not leave a competing PVC for that PV in `argo`. Provide separate namespace-scoped copies of the LabCLIP run credentials. Workloads use the cache PVC and S3 APIs, never a MinIO backend mount.

Store the Kubeflow overlay and deployment commands in the IaC repository. The overlay contains only local deltas: non-default user identity and password hash supplied privately, storage/namespace integration, scheduling adjustments justified by measured capacity, and the private Tailscale ingress. Deploy in three declarative stages: Terraform foundation, Kubeflow distribution, then Terraform resources that depend on the created user namespace. The procedure must render and inspect manifests, apply CRDs and dependencies in documented order, wait for readiness, and support repeat apply and a read-only drift check. It must not use an unbounded retry loop or `--force-conflicts` without inspecting what owns the conflicting field. Destruction is a separate reviewed operation and must retain LabCLIP MinIO and cache data.

## Host and capacity gates

Both hosts are currently reachable through SSH, but k3s and the generated kubeconfig are absent after an intentional teardown. Run the existing read-only Ansible preflight, then the Ansible site playbook using an interactive privilege-escalation credential that is never sent through chat or written to the repository. Use the generated kubeconfig for all following cluster operations.

The control-plane host has 16 CPUs and approximately 31 GiB total RAM, with approximately 15 GiB host-available RAM in the latest read-only sample. The worker has 96 CPUs and substantially more memory and storage. The upstream distribution estimates approximately 4.38 CPU, 12.34 GiB RAM, and 65 GB PVC storage for its default composition; these are planning figures, not Kubernetes allocatable or peak-use proof. Before installing the full distribution, check node allocatable resources, current host pressure, storage provisioning, and the schedule of memory-heavy Pods. Prefer worker placement for platform workloads that need it while keeping GPU training placement tied to a valid node-local cache PVC. A shortfall fails the preflight; do not silently remove products from the agreed full composition.

The Ansible-pinned k3s version is `v1.36.2+k3s1`; the selected Kubeflow release documents Kubernetes 1.35+ support. The k3s server disables bundled Traefik, so the Kubeflow Istio ingress gateway must be the explicit web entry point. Review all installed CRDs, admission webhooks, default StorageClass interactions, and Argo version ownership before apply.

## Authentication and access

Create a private Dex identity and bcrypt password hash before deployment. The upstream sample `user@example.com` / `12341234` must not be reachable, including during a temporary installation phase. Do not print or commit passwords, OAuth secrets, Terraform sensitive values, or rendered Secrets. Restrict sensitive generated inputs to mode 0600.

Keep `istio-ingressgateway` as a `ClusterIP` service. Local access uses a loopback-only port-forward to the gateway. Remote access uses the existing Tailscale operator pattern to publish the gateway on HTTPS port 443 to authorized tailnet members only. Do not use Tailscale Funnel, a public LoadBalancer, a public NodePort, or a public host bind. Before enabling the ingress, confirm the tailnet grant's source identities and that the target is only the intended gateway; verify Dex redirects and secure cookies from both origins in a real browser.

## LabCLIP pipeline behavior

The application repository owns a KFP pipeline source and a compiled, versioned pipeline package. Its PoC path uses the existing image and a code snapshot so the exact source can be reconstructed. The container receives explicit dataset/model selection, code identity, run ID, and result URI. The selected cache PVC supplies `DATASET_ROOT=/mnt/data/lab_datasets`; it is a cache of canonical MinIO objects. The training and official-test tasks request one GPU each, run on a node compatible with that PVC, and retain the current W&B failure semantics.

The first customer run uses CUHK-PEDES and a short bounded, stem-paired training YAML configuration, then evaluates `best_t2i.pt` on the official test split. Dataset, model, and runtime selection remain explicit YAML/environment injections. The PoC must not overwrite a published full-run result or silently change the dataset's train/validation/test protocol. Preserve the existing result publication metadata and SHA-256 verification contract. Disable KFP execution caching for training, publishing, evaluation, and failure-demonstration steps so a customer re-run really executes. Record a small typed output linking the resulting model and evaluation to the KFP run; verify in the UI whether a cross-run lineage view is actually present before claiming it.

A separate intentionally failing run demonstrates that the UI shows the failed step and that failure metadata is still published. The failure must be injected through a PoC-specific parameter or harmless test task; do not corrupt a dataset, checkpoint, or shared Secret. The normal run remains independently runnable after the failure demonstration.

## Verification and customer handoff

Static gates: render the pinned Kubeflow overlay, check that no object has two declarative owners, check no default login or public exposure remains, validate Terraform and Ansible, compile the KFP package, run focused tests, and check repository diffs. Runtime gates: both Kubernetes nodes Ready, Kubeflow component Pods Ready, storage claims Bound, Dex login works, Central Dashboard navigation works, local and Tailscale origins work, and one actual GPU run completes with official-test JSON and a verifiable checkpoint checksum. Verify W&B synchronization and inspect the customer-visible run in the browser; API status alone is insufficient.

Deliver the exact local and tailnet URLs, private credential retrieval instructions, a brief UI walkthrough, pipeline/run identifiers, t2i R@1 and artifact URIs, failure-run evidence, IaC commands for repeat deploy and drift checks, and a separately reviewed teardown plan. Leave the cluster and UI running until the customer confirms the review is finished. If host authorization or external credentials block installation, preserve the completed source and report the exact action needed without claiming runtime success.

## Open implementation checks

The implementation plan must settle the manifest apply mechanism and inventory/prune behavior against the pinned distribution, the KFP run namespace and node-local PVC binding, Dex origin handling for both access paths, and placement of stateful PVCs. These are engineering checks against actual rendered manifests and cluster state; they do not change the agreed customer-facing scope.
