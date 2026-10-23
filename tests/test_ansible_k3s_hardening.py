from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_DIR = REPO_ROOT / "ansible"
ROLES = ANSIBLE_DIR / "roles"


def ansible_files() -> list[Path]:
    return sorted(
        path
        for path in ANSIBLE_DIR.rglob("*")
        if path.is_file() and path.suffix in {".yml", ".yaml", ".j2", ".cfg"}
    )


class GuardTests(unittest.TestCase):
    MATCH = "<inventory>"

    def guard(self, *, unit: bool, env_dir: str | None, config_dir: str | None) -> subprocess.CompletedProcess[str]:
        ansible_playbook = shutil.which("ansible-playbook")
        self.assertIsNotNone(ansible_playbook, "ansible-playbook must be installed")
        with tempfile.TemporaryDirectory(prefix="labclip-guard-") as tmp:
            root = Path(tmp)
            data_dir = root / "k3s-data"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            k3s = fake_bin / "k3s"
            k3s.write_text("#!/bin/sh\necho 'k3s version v1.36.2+k3s1 (abc)'\n", encoding="utf-8")
            k3s.chmod(0o755)
            systemd_dir = root / "systemd"
            systemd_dir.mkdir()
            if unit:
                (systemd_dir / "k3s.service").write_text("[Unit]\n", encoding="utf-8")
            if env_dir is not None:
                value = data_dir if env_dir == self.MATCH else env_dir
                (systemd_dir / "k3s.service.env").write_text(
                    f"K3S_TOKEN=do-not-print-me\nK3S_DATA_DIR={value}\n", encoding="utf-8"
                )
            config_file = root / "config.yaml"
            if config_dir is not None:
                value = data_dir if config_dir == self.MATCH else config_dir
                config_file.write_text(f'data-dir: "{value}"\nnode-ip: 127.0.0.1\n', encoding="utf-8")
            inventory = root / "hosts.yml"
            inventory.write_text(
                f"""all:
  children:
    k3s_server:
      hosts:
        test-server:
          ansible_connection: local
          storage_mount: {root}
          k3s_data_dir: {data_dir}
          cache_root: {root / 'cache'}
          k3s_data_guard_systemd_dir: {systemd_dir}
          k3s_data_guard_config_file: {config_file}
""",
                encoding="utf-8",
            )
            playbook = root / "guard.yml"
            playbook.write_text(
                """- name: Run the data directory guard
  hosts: all
  gather_facts: false
  become: false
  roles:
    - k3s_data_dir_guard
""",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["ANSIBLE_ROLES_PATH"] = str(ROLES)
            return subprocess.run(
                [ansible_playbook or "ansible-playbook", str(playbook), "-i", str(inventory)],
                cwd=ANSIBLE_DIR,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )

    def test_binary_without_unit_env_or_config_is_not_installed(self) -> None:
        result = self.guard(unit=False, env_dir=None, config_dir=None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"Refuse to switch an existing K3s installation[^\n]*\*+\nskipping")

    def test_unit_without_env_or_config_uses_the_k3s_default(self) -> None:
        result = self.guard(unit=True, env_dir=None, config_dir=None)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("/var/lib/rancher/k3s", result.stdout)

    def test_config_data_dir_must_match_inventory(self) -> None:
        result = self.guard(unit=False, env_dir=None, config_dir="/srv/other-k3s")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Tear down K3s", result.stdout)
        result = self.guard(unit=False, env_dir=None, config_dir=self.MATCH)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_env_file_data_dir_wins_and_credentials_stay_hidden(self) -> None:
        result = self.guard(unit=True, env_dir=self.MATCH, config_dir="/srv/other-k3s")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("do-not-print-me", result.stdout + result.stderr)


class ConfigTemplateTests(unittest.TestCase):
    def render(self, host_vars: str) -> dict:
        ansible_playbook = shutil.which("ansible-playbook")
        self.assertIsNotNone(ansible_playbook, "ansible-playbook must be installed")
        with tempfile.TemporaryDirectory(prefix="labclip-k3s-config-") as tmp:
            root = Path(tmp)
            out = root / "config.yaml"
            inventory = root / "hosts.yml"
            inventory.write_text(
                f"""all:
  children:
    k3s_server:
      hosts:
        vis-lab:
          ansible_connection: local
          k3s_node_ip: 10.0.0.10
          storage_mount: /mnt/data
          k3s_data_dir: /mnt/data/labclip-k3s
{host_vars}""",
                encoding="utf-8",
            )
            playbook = root / "render.yml"
            playbook.write_text(
                f"""- name: Render k3s config
  hosts: all
  gather_facts: false
  become: false
  vars_files:
    - {ANSIBLE_DIR / 'group_vars/all.yml'}
  roles:
    - k3s_common
  tasks:
    - name: Render
      ansible.builtin.copy:
        content: "{{{{ lookup('template', '{ROLES / 'k3s_common/templates/config.yaml.j2'}') }}}}"
        dest: {out}
        mode: "0600"
""",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["ANSIBLE_ROLES_PATH"] = str(ROLES)
            result = subprocess.run(
                [ansible_playbook or "ansible-playbook", str(playbook), "-i", str(inventory)],
                cwd=ANSIBLE_DIR,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return yaml.safe_load(out.read_text(encoding="utf-8"))

    def test_server_config_carries_storage_and_eviction_settings(self) -> None:
        config = self.render("")
        self.assertEqual(config["data-dir"], "/mnt/data/labclip-k3s")
        self.assertEqual(config["node-ip"], "10.0.0.10")
        self.assertEqual(config["advertise-address"], "10.0.0.10")
        self.assertEqual(config["write-kubeconfig-mode"], "0600")
        self.assertEqual(config["disable"], ["traefik"])
        self.assertNotIn("bind-address", config)
        args = config["kubelet-arg"]
        self.assertIn("image-gc-high-threshold=80", args)
        self.assertIn("image-gc-low-threshold=70", args)
        self.assertIn("container-log-max-size=50Mi", args)
        self.assertIn("container-log-max-files=3", args)
        eviction = next(arg for arg in args if arg.startswith("eviction-hard="))
        for signal in ("nodefs.available<15%", "imagefs.available<10%", "memory.available<2Gi"):
            self.assertIn(signal, eviction)
        self.assertTrue(any(arg.startswith("eviction-minimum-reclaim=") for arg in args))
        self.assertTrue(any(arg.startswith("system-reserved=") for arg in args))

    def test_gpu_is_not_offered_by_default(self) -> None:
        config = self.render("")
        self.assertNotIn("node-label", config)
        self.assertNotIn("node-taint", config)

    def test_gpu_opt_in_adds_label_and_optional_taint(self) -> None:
        config = self.render("          k3s_gpu_offered: true\n          k3s_gpu_node_taint_enabled: true\n")
        self.assertEqual(config["node-label"], ["labclip.io/k8s-gpu=true"])
        self.assertEqual(config["node-taint"], ["labclip.io/k8s-gpu=true:NoSchedule"])


class StaticHardeningTests(unittest.TestCase):
    def test_no_role_pipes_an_installer_into_a_shell(self) -> None:
        for path in ansible_files():
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertNotIn("get.k3s.io", text)
                self.assertNotRegex(text, r"curl[^\n]*\|\s*sh")
                self.assertNotIn("apt_key", text)

    def test_nothing_calls_the_k3s_uninstall_or_killall_scripts(self) -> None:
        for path in ansible_files():
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertNotIn("uninstall.sh", text)
                self.assertNotIn("killall.sh", text)

    def test_pinned_k3s_assets_have_sha256_checksums(self) -> None:
        defaults = yaml.safe_load((ROLES / "k3s_common/defaults/main.yml").read_text(encoding="utf-8"))
        self.assertTrue(defaults["k3s_release_assets"])
        for arch, asset in defaults["k3s_release_assets"].items():
            with self.subTest(arch=arch):
                self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
        install = (ROLES / "k3s_common/tasks/install.yml").read_text(encoding="utf-8")
        self.assertIn('checksum: "sha256:{{ k3s_release_assets[ansible_architecture].sha256 }}"', install)

    def test_teardown_preserves_data_unless_purge_is_requested(self) -> None:
        defaults = yaml.safe_load((ROLES / "k3s_common/defaults/main.yml").read_text(encoding="utf-8"))
        self.assertIs(defaults["k3s_teardown_purge"], False)
        tasks = yaml.safe_load((ROLES / "k3s_teardown/tasks/main.yml").read_text(encoding="utf-8"))
        purge_tasks = [task for task in tasks if "Purge" in task["name"]]
        self.assertEqual(len(purge_tasks), 2)
        for task in purge_tasks:
            self.assertEqual(task["when"], "k3s_teardown_purge | bool")
        teardown = (ANSIBLE_DIR / "playbooks/teardown.yml").read_text(encoding="utf-8")
        self.assertNotIn("always preserves data", teardown)
        self.assertIn("k3s_teardown_purge=true", teardown)

    def test_cache_root_is_not_world_writable(self) -> None:
        defaults = yaml.safe_load((ROLES / "local_storage/defaults/main.yml").read_text(encoding="utf-8"))
        self.assertEqual(defaults["cache_root_mode"], "0775")
        tasks = (ROLES / "local_storage/tasks/main.yml").read_text(encoding="utf-8")
        self.assertNotIn("0777", tasks)

    def test_kubelet_storage_is_bind_mounted_before_k3s_starts(self) -> None:
        site = yaml.safe_load((ANSIBLE_DIR / "playbooks/site.yml").read_text(encoding="utf-8"))
        order = []
        for play in site:
            for role in play.get("roles", []):
                order.append(role["role"] if isinstance(role, dict) else role)
        self.assertLess(order.index("kubelet_storage"), order.index("k3s_server"))
        self.assertLess(order.index("kubelet_storage"), order.index("k3s_agent"))
        tasks = yaml.safe_load((ROLES / "kubelet_storage/tasks/main.yml").read_text(encoding="utf-8"))
        mount = next(task for task in tasks if "ansible.posix.mount" in task)
        self.assertEqual(mount["ansible.posix.mount"]["state"], "mounted")
        self.assertIn("bind", mount["ansible.posix.mount"]["opts"])
        defaults = yaml.safe_load((ROLES / "k3s_common/defaults/main.yml").read_text(encoding="utf-8"))
        targets = [item["path"] for item in defaults["k3s_bind_mounts"]]
        self.assertEqual(targets, ["{{ k3s_kubelet_root_dir }}", "{{ k3s_pod_log_dir }}"])

    def test_mount_dropin_requires_storage_and_kubelet_mounts(self) -> None:
        template = (ROLES / "k3s_common/templates/mount.conf.j2").read_text(encoding="utf-8")
        self.assertIn("RequiresMountsFor=", template)
        defaults = (ROLES / "k3s_common/defaults/main.yml").read_text(encoding="utf-8")
        self.assertRegex(defaults, r"k3s_requires_mounts_for:[^\n]*\n[^\n]*storage_mount, k3s_kubelet_root_dir")
        handlers = yaml.safe_load((ROLES / "k3s_common/handlers/main.yml").read_text(encoding="utf-8"))
        self.assertIn("reload systemd", [handler["name"] for handler in handlers])

    def test_nvidia_runtime_restart_is_not_tied_to_fresh_installs_only(self) -> None:
        tasks = yaml.safe_load((ROLES / "nvidia_runtime/tasks/main.yml").read_text(encoding="utf-8"))
        notifying = [task for task in tasks if task.get("notify") == "restart k3s"]
        self.assertTrue(any("when" not in task for task in notifying))


if __name__ == "__main__":
    unittest.main()
