# Kubeflow Community Distribution

This runbook installs the pinned Kubeflow Community Distribution 26.03.1
release used by the LabCLIP PoC. It keeps the responsibilities explicit:

| Layer | Owns |
|---|---|
| Ansible | Host preparation, NVIDIA runtime, k3s, and the private kubeconfig |
| Terraform | GPU plugin, MinIO, retained cache PVs, LabCLIP run bindings, and the optional Tailscale Operator |
| Pinned Kustomize overlay | Kubeflow namespaces, CRDs, controllers, Istio, Dex, OAuth2 Proxy, KFP, other distribution components, and the single Tailscale Ingress |

Terraform owns the Operator and its OAuth Secret. The Kustomize overlay owns
`Ingress/istio-system/kubeflow-tailnet`; do not manage that Ingress in
Terraform. The Istio ingress gateway stays `ClusterIP`. No layer opens a public
NodePort, public LoadBalancer, Tailscale Funnel, or host listener.

## Preconditions

Run commands from the IaC repository root and use the existing private Ansible
inventory and Terraform inputs. The inventory, Terraform variables, generated
kubeconfig, Kubeflow identity, rendered manifest, receipt, inventory, and digest
approval are private local files and are ignored by Git.

Before enabling the Tailscale Operator, inspect the current Access Controls
policy and confirm that the Operator can use `tag:k8s-operator`, that this tag
owns `tag:k8s`, and that the intended user or device group can reach only TCP
443 on `tag:k8s`. Preserve unrelated policy rules. The existing LabCLIP
Tailscale runbook describes the expected tag and grant entries. Terraform does
not edit the tailnet policy. If the existing grant does not match the intended
audience, stop and review that policy change separately. Check the current
[Operator installation](https://tailscale.com/docs/kubernetes-operator/install-operator)
and [Ingress](https://tailscale.com/docs/kubernetes-operator/ingress)
documentation when verifying current tags, OAuth scopes, and access behavior.

The current Terraform chart default is Tailscale Operator 1.76.1. Compare it
with any existing Operator release before enabling it; do not adopt an existing
Helm release or change its version without a reviewed plan. OAuth client ID and
secret must be provided through the ignored `terraform/terraform.tfvars` or the
existing private Terraform input mechanism. Do not put either value in a shell
argument, rendered manifest, screenshot, or chat.

## Install sequence

First inspect host readiness, then install k3s. Ansible privilege escalation
prompts interactively:

```bash
ansible-playbook ansible/playbooks/preflight.yml
ansible-playbook ansible/playbooks/site.yml --ask-become-pass
```

Prepare Terraform's private MinIO, GHCR, and W&B inputs. Set
`platform_mode = "kubeflow"`, `enable_kubeflow_run_bindings = false`, and
`enable_tailscale = true` in private Terraform inputs only after the tailnet
policy and OAuth client have passed the preconditions above. Leave the run
namespace integration disabled for the foundation stage.

```bash
python3 scripts/prepare_terraform_inputs.py
terraform -chdir=terraform init
terraform -chdir=terraform plan -var='platform_mode=kubeflow' -var='enable_kubeflow_run_bindings=false' -var='enable_tailscale=true'
terraform -chdir=terraform apply -var='platform_mode=kubeflow' -var='enable_kubeflow_run_bindings=false' -var='enable_tailscale=true'
```

Inspect the complete Terraform plan before applying. Confirm it keeps the
canonical MinIO roots and retained local cache PVs, omits standalone Argo
Workflows resources, and installs only the intended Tailscale Operator. Stop
if Terraform proposes a resource replacement or an unreviewed Tailscale
release change.

Prepare the private Dex identity and Tailscale Ingress patches, then render the
pinned overlay. The first render is a candidate and prints its digest without
printing the generated password or hash:

```bash
uv run --with bcrypt==4.2.1 python scripts/prepare_kubeflow_overlay.py --identity-file kubeflow/generated/identity.json --email labclip@example.com --tailnet-hostname labclip-kubeflow
python3 scripts/render_kubeflow.py
```

Review the pinned source, object inventory, gateway `ClusterIP` setting,
non-default Dex identity, and private Ingress contract. Approve only the exact
digest reviewed in that render:

```bash
python3 scripts/render_kubeflow.py --approve-digest <reviewed-sha256>
```

The rendered manifest, receipt, inventory, and approval must remain mode 0600.
`apply_kubeflow.py` verifies the receipt, explicit digest approval, inventory,
Terraform ownership boundary, and private-exposure checks immediately before
each apply. It applies CRDs first, waits for them to become Established, then
applies the full manifest. It retries only recognized missing-CRD or
temporarily unavailable dependency errors, with no more than six apply
attempts total including the CRD stage. Field conflicts stop immediately and
are never forced.

```bash
export KUBECONFIG="$PWD/terraform/generated/kubeconfig"
python3 scripts/apply_kubeflow.py
```

After applying Kubeflow, confirm that the generated Profile created the
expected namespace. For the default PoC identity it is
`kubeflow-user-labclip-example-com`. Verify the retained PVs and host data using
the handoff checks in [`terraform/README.md`](../terraform/README.md#kubeflow-run-bindings-and-retained-cache-pv-handoff).
Then enable Terraform's second stage and review its plan before applying:

```bash
terraform -chdir=terraform plan -var='platform_mode=kubeflow' -var='enable_kubeflow_run_bindings=true' -var='confirm_kubeflow_cache_pv_rebind=true' -var='labclip_run_namespace=kubeflow-user-labclip-example-com' -var='enable_tailscale=true'
terraform -chdir=terraform apply -var='platform_mode=kubeflow' -var='enable_kubeflow_run_bindings=true' -var='confirm_kubeflow_cache_pv_rebind=true' -var='labclip_run_namespace=kubeflow-user-labclip-example-com' -var='enable_tailscale=true'
```

Set the run namespace to the exact name in the private identity file if the
email differs from the default. The Terraform guard checks the PV phase, claim
reference, local path, and node affinity before creating LabCLIP's cache claims
and copied run credentials.

## UI access and verification

Run the read-only drift and readiness checks after both Terraform stages:

```bash
python3 scripts/check_kubeflow.py
```

The drift report invokes only `kubectl diff`; it prints changed-object counts
without printing diff bodies. Readiness checks compare rendered Deployments,
StatefulSets, and PVCs with their live status. Check the Central Dashboard and
Dex login in a browser as a separate runtime check; Kubernetes readiness alone
does not prove the UI flow works.

For the local UI, forward only the Istio gateway to loopback:

```bash
kubectl -n istio-system port-forward --address 127.0.0.1 service/istio-ingressgateway 8080:80
```

Open `http://127.0.0.1:8080/`. For the private remote URL, read the hostname
assigned to the single Ingress and use HTTPS from a tailnet-connected device:

```bash
kubectl -n istio-system get ingress kubeflow-tailnet -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

Confirm the returned hostname ends in the tailnet's `.ts.net` domain. Verify
that authorized tailnet clients can log in and that a client outside the
tailnet cannot reach the URL. Keep the Ingress class set to `tailscale`, the
backend set to `istio-ingressgateway:80`, and Funnel disabled.

## Repeat apply and drift

After an approved render exists, re-rendering verifies it against the recorded
digest. Repeat the apply to reconcile the exact approved manifest, then run the
read-only drift and readiness report:

```bash
python3 scripts/render_kubeflow.py
python3 scripts/apply_kubeflow.py
python3 scripts/check_kubeflow.py
```

If the manifest digest changes, stop. Review the new rendered manifest and
inventory, then explicitly approve the new digest before applying it. Do not
accept a new upstream commit or an unreviewed generated identity as a routine
repeat apply.

## Teardown boundary

This PoC is intended to remain running for customer review. Teardown is a
separate, reviewed operation. Before any removal, inspect Terraform's destroy
plan, Kubeflow PVCs and CRDs, active KFP runs, Tailscale-generated proxy
resources, and the canonical MinIO/cache data roots. Delete only the exact
reviewed platform resources. Preserve `/mnt/data/minio-code`,
`/data/jayn2u/minio`, `/mnt/data/labclip-cache`, and
`/data/jayn2u/labclip-cache`; do not use a broad `kubectl delete all`,
`kustomize delete`, or unreviewed `terraform destroy` as a shortcut.
