# Ansible — node/OS layer

Owns everything below the Kubernetes API: OS prerequisites, the NVIDIA container
runtime, local cache/MinIO directories, and installing k3s itself. It does **not**
manage anything inside the cluster (no Kubernetes manifests, no Helm releases) —
that is the job of the Terraform stacks (`../argo/terraform` and
`../kubeflow/infra/terraform`).

## Scope boundary

If a change is "install/configure something on a host" -> Ansible.
If a change is "create/update a Kubernetes object" -> Terraform.

The handoff point between the two layers is a kubeconfig file: the `k3s_server`
role writes it to `ansible/generated/kubeconfig` with mode `0600` after the
control plane comes up, and both stacks' Terraform provider blocks read from that path.
Nothing in this directory talks to the Kubernetes API directly.

## Usage

`ansible.cfg` uses the built-in `default` stdout callback with
`callback_result_format = yaml`. This keeps YAML-formatted task results without
depending on the removed `community.general.yaml` callback. Ansible Core 2.13
or newer supports this setting; the synchronization CI uses Core 2.16.3.

```bash
cp inventory/hosts.example.yml inventory/hosts.yml
ansible-playbook playbooks/preflight.yml
ansible-playbook playbooks/site.yml
```

`inventory/hosts.yml` is gitignored. Keep `ansible_host` (the SSH transport
address) separate from `k3s_node_ip` (the address both nodes use to reach the
Kubernetes API). Set `storage_mount`, `k3s_data_dir`, `cache_root`, and
`minio_data_root` explicitly for each node. Preflight verifies the mount and
that the k3s data directory is separate from cache and MinIO before creating
directories or installing k3s.

K3s containerd data, including downloaded and unpacked images, is stored under
`k3s_data_dir` on the persistent data filesystem. An existing installation whose
service environment points to another data directory is refused; tear it down
using its current directory before a clean install. This automation does not
move existing K3s state. The guard reads `K3S_DATA_DIR` from the systemd
environment; manually configured `--data-dir` arguments or `config.yaml` paths
need operator review before using these playbooks.

## Checking for drift on existing hosts

```bash
ansible-playbook playbooks/preflight.yml
```

This is a read-only check that works before k3s is installed. It verifies the OS,
kernel module availability, persistent mount identity, and any declared GPU. It
does not load modules, change sysctls, or create directories. `site.yml` applies
the required kernel and sysctl settings in the separate `host_prepare` role.

## Roles

| Role | Responsibility |
|---|---|
| `host_preflight` | Read-only OS, module availability, mount, and GPU checks |
| `host_prepare` | Load k3s networking modules and set required sysctls |
| `k3s_data_dir_guard` | Keep K3s state separate from cache and MinIO data and reject path switches |
| `nvidia_runtime` | Install the toolkit if absent and let k3s discover the runtime |
| `local_storage` | Create cache directories and ensure MinIO roots exist without changing existing ownership |
| `k3s_server` | Install the control-plane node, fetch kubeconfig for Terraform |
| `k3s_agent` | Join a worker node using the server's token |

Pinned install versions live in `group_vars/all.yml` — bump them deliberately in
a reviewed change, never let a role float to "latest". The NVIDIA toolkit role
leaves an already installed version in place and installs the pin only when the
package is absent.

## Tearing down (returning hosts to bare metal)

Destroy the deployed stack's Terraform layer first, then remove k3s from every node.
For the Kubeflow stack, first remove the Kustomize-applied distribution as described
in `../kubeflow/README.md`, and use `../kubeflow/infra/terraform` in place of
`../argo/terraform` below:

```bash
cd ../argo/terraform && terraform destroy
cd ../../ansible
ansible-playbook playbooks/teardown.yml -e confirm_teardown=yes
```

This runs k3s's own uninstall scripts (`k3s-agent-uninstall.sh` on agents,
`k3s-uninstall.sh` on the server) and removes the fetched kubeconfig. It always
preserves the NVIDIA container toolkit, caches, and every MinIO data directory.
The uninstall scripts remove the exact configured `k3s_data_dir`, including
K3s state and image layers. For an installation created before this path was
configured, pass `-e k3s_data_dir=/var/lib/rancher/k3s`; the guard allows that
exact legacy path only during teardown. Omit the override after a clean install.
