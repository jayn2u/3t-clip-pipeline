# Ansible — node/OS layer

Owns everything below the Kubernetes API: OS prerequisites, the NVIDIA container
runtime, local cache/MinIO directories, and installing k3s itself. It does **not**
manage anything inside the cluster (no Kubernetes manifests, no Helm releases) —
that is `../terraform`'s job.

## Scope boundary

If a change is "install/configure something on a host" -> Ansible.
If a change is "create/update a Kubernetes object" -> Terraform.

The handoff point between the two layers is a kubeconfig file: the `k3s_server`
role fetches it to `terraform/generated/kubeconfig` after the control plane comes
up, and Terraform's provider blocks read from that path. Nothing in this directory
talks to the Kubernetes API directly.

## Usage

```bash
cp inventory/hosts.example.yml inventory/hosts.yml   # fill in real host/IP values
ansible-playbook playbooks/preflight.yml              # read-only, safe anytime
ansible-playbook playbooks/site.yml                    # installs/joins k3s
```

`inventory/hosts.yml` is gitignored — it will contain real IPs and SSH users.

## Checking for drift on existing hosts

```bash
ansible-playbook playbooks/preflight.yml
```

This only asserts expected state (kernel modules, sysctl, GPU visibility, cache
directory) and never mutates anything — the node-layer equivalent of `terraform
plan`.

## Roles

| Role | Responsibility |
|---|---|
| `host_preflight` | Assert OS family, kernel modules, sysctl, GPU visibility, cache root exists |
| `nvidia_runtime` | Install nvidia-container-toolkit, wire it into k3s's containerd |
| `local_storage` | Create cache and MinIO backend directories with correct ownership |
| `k3s_server` | Install the control-plane node, fetch kubeconfig for Terraform |
| `k3s_agent` | Join a worker node using the server's token |

Pinned versions live in `group_vars/all.yml` — bump them deliberately in a
reviewed change, never let a role float to "latest".

## Tearing down (returning hosts to bare metal)

Destroy the Terraform layer first, then remove k3s from every node:

```bash
cd ../terraform && terraform destroy
cd ../ansible
ansible-playbook playbooks/teardown.yml -e confirm_teardown=yes
```

This runs k3s's own uninstall scripts (`k3s-agent-uninstall.sh` on agents,
`k3s-uninstall.sh` on the server) and removes the fetched kubeconfig. It
leaves the NVIDIA container toolkit and the cache/MinIO data directories in
place. To also delete that data, add `-e purge_data=yes` — this is
irreversible, it removes real files, not just cluster state.
