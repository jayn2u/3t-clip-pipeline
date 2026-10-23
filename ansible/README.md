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

Plays in `site.yml` are tagged `storage`, `host_prepare`, `firewall`,
`kubelet_storage`, `k3s`, and `nvidia`; preflight always runs.

`inventory/hosts.yml` is gitignored. Keep `ansible_host` (the SSH transport
address) separate from `k3s_node_ip` (the address both nodes use to reach the
Kubernetes API). Set `storage_mount`, `k3s_data_dir`, `cache_root`, and
`minio_data_root` explicitly for each node. Preflight verifies the mount and
that the k3s data directory is separate from cache and MinIO before creating
directories or installing k3s.

K3s containerd data, including downloaded and unpacked images, is stored under
`k3s_data_dir` on the persistent data filesystem. An existing installation whose
data directory differs from the inventory is refused; tear it down using its
current directory before a clean install. This automation does not move
existing K3s state. The guard treats k3s as installed only when the binary is
present together with its systemd unit, environment file, or
`/etc/rancher/k3s/config.yaml`, and resolves the data directory from
`K3S_DATA_DIR`, then `data-dir` in `config.yaml`, then the k3s default.
A stray binary without a unit is treated as not installed.

## k3s installation and configuration

k3s is installed from the pinned GitHub release asset for `k3s_version`; the
download is verified against the SHA-256 in `k3s_release_assets`
(`roles/k3s_common/defaults/main.yml`). Bump both together. No `curl | sh`
installer is used and no `k3s-*uninstall.sh` or `k3s-killall.sh` script is
created or called. The roles template:

- `/etc/rancher/k3s/config.yaml` (mode `0600`): `data-dir`, `node-ip`,
  `advertise-address`, optional `bind-address`, disabled components (Traefik),
  `write-kubeconfig-mode: "0600"`, node labels/taints, and kubelet arguments
  for image GC (`80`/`70`), `eviction-hard`, `eviction-minimum-reclaim`,
  container log rotation (`50Mi` x `3`), and `system-reserved`. Every value is a
  variable in `roles/k3s_common/defaults/main.yml`.
- `/etc/systemd/system/k3s.service` or `k3s-agent.service`.
- `/etc/systemd/system/<service>.service.d/mount.conf` with
  `RequiresMountsFor=` covering `storage_mount`, `/var/lib/kubelet`, and
  `/var/log/pods`, so k3s never starts on the root filesystem when the data
  disk is missing.

Changes to the binary, config, unit, or drop-in reload systemd and restart k3s.

## Kubelet and pod-log storage

The `kubelet_storage` role bind-mounts `<dirname of k3s_data_dir>/labclip-kubelet`
onto `/var/lib/kubelet` and `<dirname of k3s_data_dir>/labclip-podlogs` onto
`/var/log/pods` through `/etc/fstab` (`ansible.posix.mount`, `state: mounted`,
options `bind,nofail,x-systemd.requires-mounts-for=<storage_mount>`). It runs
before k3s is installed or started and refuses to run while k3s is active with
an unmounted target, or when a target holds data and its source is still empty.
Migrate existing data first, or set `k3s_bind_mounts_allow_shadow=true`.
Disable the role per host with `k3s_bind_mounts_enabled: false`.

## Firewall

`k3s_firewall` adds ufw rules that allow `6443/tcp` and `10250/tcp` only from
`k3s_firewall_lan_cidrs`, `k3s_firewall_tailscale_cidrs` (default
`100.64.0.0/10`), the pod and service CIDRs, and every cluster node IP, followed
by deny rules for everyone else. It never enables ufw; when ufw is inactive it
only warns. Set `k3s_firewall_enabled: false` to skip it.

## GPU opt-in

A node offers its GPU to Kubernetes only when `k3s_gpu_offered: true`, which adds
the `labclip.io/k8s-gpu=true` node label (and, with
`k3s_gpu_node_taint_enabled: true`, a matching `NoSchedule` taint). The NVIDIA
device plugin in both Terraform stacks only runs on nodes with that label.
k3s applies `node-label`/`node-taint` only when a node first registers, so on an
already registered node toggle the label with
`kubectl label node <node> labclip.io/k8s-gpu=true --overwrite` (or
`labclip.io/k8s-gpu-` to withdraw it).

## Cache directory permissions

`cache_root` is owned by `root:<cache_root_group>` (default `labclip`) with mode
`0775`. Add host users that need write access with `cache_root_group_members`.

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
| `k3s_firewall` | Restrict 6443/10250 to trusted CIDRs with ufw when ufw is active |
| `kubelet_storage` | Bind-mount kubelet and pod-log directories from persistent storage via fstab |
| `k3s_data_dir_guard` | Keep K3s state separate from cache and MinIO data and reject path switches |
| `k3s_common` | Shared defaults, pinned binary install, config, unit, and mount drop-in |
| `nvidia_runtime` | Install the toolkit if absent and restart k3s until containerd registers the NVIDIA runtime |
| `local_storage` | Create cache directories and ensure MinIO roots exist without changing existing ownership |
| `k3s_server` | Install the control-plane node, fetch kubeconfig for Terraform |
| `k3s_agent` | Join a worker node using the server's token |
| `k3s_teardown` | Stop and remove k3s while keeping data unless purge is requested |

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

Teardown never calls `k3s-uninstall.sh`, `k3s-agent-uninstall.sh`, or
`k3s-killall.sh`. On each node it removes all pod sandboxes, stops and disables
the k3s service, stops leftover container shims of this data directory,
unmounts k3s runtime and pod volume mounts, deletes the CNI interfaces, removes
the unit, drop-in, config, admin kubeconfig, helper links, and binary, and
removes the fetched kubeconfig. By default it keeps `k3s_data_dir`,
`/var/lib/kubelet`, and `/var/log/pods`. Pass `-e k3s_teardown_purge=true` to
delete those as well. The NVIDIA container toolkit, caches, every MinIO data
directory, and the fstab bind mounts are always kept. For an installation
created before this path was configured, pass
`-e k3s_data_dir=/var/lib/rancher/k3s`; the guard allows that exact legacy path
only during teardown. Omit the override after a clean install.
