# Bootstrap a k3s cluster

The bootstrap adapter supports either one server plus any number of agents, or exactly three
servers for embedded etcd. It rejects empty, two-server, and larger/even server topologies before
any host mutation. Ubuntu amd64 hosts must already have passwordless privilege escalation, clock
synchronization, disabled swap, the required firewall/port policy, a compatible NVIDIA driver and
runtime, and the configured storage mount. The playbooks validate those prerequisites; they do not
image hosts, install drivers, disable firewalls, or change storage.

Copy `ansible/inventory/example.yml`, replace the documentation addresses and both identity values,
and confirm each hostname and `/etc/machine-id` is unique. Set
`host_preflight_required_ports_open` only
after verifying TCP 6443 from agents to servers, TCP 2379-2380 between three servers, and the k3s
overlay/backend ports required by the selected network policy. Set
`host_preflight_firewall_policy_valid` only after the existing policy permits that traffic.

Install the frozen collections into the task cache:

```bash
export ANSIBLE_HOME="$PWD/.cache/ansible/home"
export ANSIBLE_COLLECTIONS_PATH="$PWD/.cache/ansible/collections"
uv run ansible-galaxy collection install -r ansible/requirements.yml \
  -p "$ANSIBLE_COLLECTIONS_PATH" --force
```

The requirements file locks `k3s.orchestration` 1.2.0 to commit
`2c3f3773c704bd00bf7f6fc340cac8ab7ce9121b` and `kubernetes.core` 6.5.0 to commit
`0f472b53e2ee73e11b5f9067ab0826d76183c157`. The project lock file pins the control-node Python
and Ansible environment. Do not install these collections into a user-global Galaxy directory.

Before any host installation, the adapter downloads the Linux-amd64 k3s binary and the installer
from the frozen `v1.36.2+k3s1` release into `.cache/k3s/artifacts`. Ansible verifies SHA-256
`65a55ec56c24eab44383086166ec620a491952b7e23941a49ddca6e8a4c4b4de` for the binary and
`46177d4c99440b4c0311b67233823a8e8a2fc09693f6c89af1a7161e152fbfad` for the installer before
the pinned collection's airgap path copies or executes either file. A mismatch stops bootstrap
before host mutation; the collection never falls back to its floating network-installer path.

`make cluster-bootstrap` runs preflight in check mode and never connects to the documentation
addresses unless the operator supplies a real inventory through `INVENTORY`. Apply is accepted only
with both `APPLY=1` and a lowercase non-production context beginning with `dev`, `development`,
`test`, `testing`, `staging`, `stage`, `sandbox`, `local`, or `ci`. Every other context, including
production-prefixed variants, is rejected before Ansible starts:

```bash
make cluster-bootstrap INVENTORY=ansible/inventory/staging.yml APPLY=1 CONTEXT=staging
```

Set `k3s_initial_bootstrap: true` only for the first cluster creation. That initial run enables k3s
secrets encryption. Set it back to `false` before subsequent convergence runs; enabling or rotating
encryption on an existing cluster requires a separately reviewed maintenance procedure. Supply
`vault_k3s_token` through Ansible Vault. The fetched kubeconfig is written only to
`.cache/kubeconfig/config` with mode `0600`; it is never merged into a user kubeconfig.
