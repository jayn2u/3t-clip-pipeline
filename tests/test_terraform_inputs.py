import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from scripts.prepare_terraform_inputs import prepare_inputs


class TerraformInputTests(unittest.TestCase):
    def test_bootstrap_prefers_a_local_mc_and_passes_it_to_both_stores(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            labclip_root = directory / "lab_clip"
            (labclip_root / "pipeline" / "scripts").mkdir(parents=True)
            (labclip_root / "pipeline" / "scripts" / "minio_bootstrap.py").touch()
            executable_dir = directory / "bin"
            executable_dir.mkdir()
            local_mc = executable_dir / "mc"
            local_mc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            local_mc.chmod(0o755)
            runner = directory / "python-capture"
            runner.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['CAPTURE_ARGS'], 'a', encoding='utf-8') as f:\n"
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n",
                encoding="utf-8",
            )
            runner.chmod(0o755)
            kubeconfig = directory / "kubeconfig"
            kubeconfig.touch()
            capture = directory / "arguments.jsonl"
            environment = os.environ.copy()
            environment.update(
                {
                    "LABCLIP_ROOT": str(labclip_root),
                    "LABCLIP_PYTHON": str(runner),
                    "KUBECONFIG": str(kubeconfig),
                    "PATH": f"{executable_dir}:{environment['PATH']}",
                    "CAPTURE_ARGS": str(capture),
                }
            )

            subprocess.run(
                [str(Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_minio.sh")],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            calls = [json.loads(line) for line in capture.read_text().splitlines()]
            self.assertEqual(2, len(calls))
            self.assertEqual(["code", "ml-assets"], [calls[0][2], calls[1][2]])
            for args in calls:
                mc_index = args.index("--mc-path")
                self.assertEqual(str(local_mc), args[mc_index + 1])

    def test_prepare_inputs_filters_ghcr_and_preserves_generated_minio_users(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            docker_config = directory / "config.json"
            docker_config.write_text(
                json.dumps(
                    {
                        "auths": {
                            "ghcr.io": {"auth": "Z2hjci11c2VyOnBhc3N3b3Jk"},
                            "https://index.docker.io/v1/": {
                                "auth": "ZG9ja2VyLXVzZXI6cGFzc3dvcmQ"
                            },
                        },
                        "credsStore": "secretservice",
                    }
                ),
                encoding="utf-8",
            )
            docker_config.chmod(0o600)
            wandb_env = directory / ".env"
            wandb_env.write_text(
                "WANDB_API_KEY='wandb-private-value'\n"
                "WANDB_ENTITY=lab-team\n"
                "WANDB_PROJECT=clip-training\n"
                "UNRELATED_TOKEN=must-not-be-copied\n",
                encoding="utf-8",
            )
            output = directory / "terraform.generated.tfvars.json"

            prepare_inputs(output, docker_config, wandb_env)
            first = json.loads(output.read_text(encoding="utf-8"))
            prepare_inputs(output, docker_config, wandb_env)
            second = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(first["minio_credentials"], second["minio_credentials"])
            self.assertEqual({"code", "ml-assets"}, set(first["minio_credentials"]))
            for credentials in first["minio_credentials"].values():
                self.assertLessEqual(len(credentials["researcher_secret_key"]), 40)
                self.assertNotEqual(credentials["access_key"], credentials["researcher_access_key"])

            docker_document = json.loads(first["ghcr_dockerconfigjson"])
            self.assertEqual({"auths": {"ghcr.io": {"auth": "Z2hjci11c2VyOnBhc3N3b3Jk"}}}, docker_document)
            self.assertEqual("wandb-private-value", first["wandb_api_key"])
            self.assertEqual("lab-team", first["wandb_entity"])
            self.assertEqual("clip-training", first["wandb_project"])
            self.assertEqual(
                str(Path(__file__).resolve().parents[1] / "terraform" / "generated" / "kubeconfig"),
                first["kubeconfig_path"],
            )
            self.assertNotIn("UNRELATED_TOKEN", first)
            self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))

    def test_root_credentials_require_explicit_generation_and_remain_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            docker_config = directory / "config.json"
            docker_config.write_text(
                json.dumps({"auths": {"ghcr.io": {"auth": "Z2hjci11c2VyOnBhc3N3b3Jk"}}}),
                encoding="utf-8",
            )
            docker_config.chmod(0o600)
            wandb_env = directory / ".env"
            wandb_env.write_text(
                "WANDB_API_KEY=private-value\nWANDB_ENTITY=lab-team\nWANDB_PROJECT=clip-training\n",
                encoding="utf-8",
            )
            output = directory / "terraform.generated.auto.tfvars.json"

            initial = prepare_inputs(output, docker_config, wandb_env)
            self.assertNotIn("minio_root_user", initial)
            self.assertNotIn("minio_root_password", initial)

            generated = prepare_inputs(
                output, docker_config, wandb_env, generate_root_credentials=True
            )
            repeated = prepare_inputs(
                output, docker_config, wandb_env, generate_root_credentials=True
            )
            default_repeat = prepare_inputs(output, docker_config, wandb_env)
            self.assertEqual(generated["minio_root_user"], repeated["minio_root_user"])
            self.assertEqual(generated["minio_root_password"], repeated["minio_root_password"])
            self.assertEqual(generated["minio_root_user"], default_repeat["minio_root_user"])
            self.assertEqual(generated["minio_root_password"], default_repeat["minio_root_password"])
            self.assertGreaterEqual(len(generated["minio_root_user"]), 16)
            self.assertGreaterEqual(len(generated["minio_root_password"]), 16)
            for credentials in generated["minio_credentials"].values():
                self.assertNotEqual(generated["minio_root_user"], credentials["access_key"])
                self.assertNotEqual(generated["minio_root_password"], credentials["secret_key"])
            self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))

    def test_dash_secret_repair_only_rotates_unsafe_secret_fields_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            docker_config = directory / "config.json"
            docker_config.write_text(
                json.dumps({"auths": {"ghcr.io": {"auth": "Z2hjci11c2VyOnBhc3N3b3Jk"}}}),
                encoding="utf-8",
            )
            docker_config.chmod(0o600)
            wandb_env = directory / ".env"
            wandb_env.write_text(
                "WANDB_API_KEY=private-value\nWANDB_ENTITY=lab-team\nWANDB_PROJECT=clip-training\n",
                encoding="utf-8",
            )
            output = directory / "terraform.generated.auto.tfvars.json"
            original = prepare_inputs(
                output, docker_config, wandb_env, generate_root_credentials=True
            )
            original["minio_root_password"] = "-legacy-root-password-long"
            original["minio_credentials"]["code"]["secret_key"] = "-legacy-code-secret-key"
            original["minio_credentials"]["code"]["researcher_secret_key"] = "-legacy-researcher-secret-key"
            output.write_text(json.dumps(original), encoding="utf-8")
            output.chmod(0o600)

            repaired = prepare_inputs(
                output,
                docker_config,
                wandb_env,
                repair_leading_dash_secrets=True,
            )
            repeated = prepare_inputs(
                output,
                docker_config,
                wandb_env,
                repair_leading_dash_secrets=True,
            )

            self.assertEqual(original["minio_root_user"], repaired["minio_root_user"])
            self.assertNotEqual(original["minio_root_password"], repaired["minio_root_password"])
            self.assertNotEqual(
                original["minio_credentials"]["code"]["secret_key"],
                repaired["minio_credentials"]["code"]["secret_key"],
            )
            self.assertNotEqual(
                original["minio_credentials"]["code"]["researcher_secret_key"],
                repaired["minio_credentials"]["code"]["researcher_secret_key"],
            )
            self.assertEqual(
                original["minio_credentials"]["ml-assets"],
                repaired["minio_credentials"]["ml-assets"],
            )
            self.assertEqual(repaired["minio_credentials"], repeated["minio_credentials"])
            self.assertEqual(repaired["minio_root_password"], repeated["minio_root_password"])
            for store in repaired["minio_credentials"].values():
                self.assertFalse(store["secret_key"].startswith("-"))
                self.assertFalse(store["researcher_secret_key"].startswith("-"))
            self.assertFalse(repaired["minio_root_password"].startswith("-"))
            self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))


if __name__ == "__main__":
    unittest.main()
