# Kubeflow infra/platform Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `kubeflow/` into `infra/` (Terraform and its helper scripts) and `platform/` (the pinned Kustomize distribution and its prepare, render, apply, check tools), and update the `lab_clip` synchronization gate to match.

**Architecture:** Files move with `git mv` so history follows them. Kustomize tooling is renamed to `prepare.py`, `render.py`, `apply.py`, `check.py`. The only logic change is that `write_site_patches` reads its two templates from `platform/templates/`. Tests move next to what they test. The `lab_clip` gate describes each stack by its Terraform root and test directories.

**Tech Stack:** Terraform 1.16, Python 3.12 `unittest`, bash, git, `lab_clip` pytest suite, `gh`.

**Spec:** `docs/superpowers/specs/2026-10-01-kubeflow-infra-platform-layout-design.md`

## Global Constraints

- IaC work happens only in the worktree `/mnt/data/lab_clip/infra/3t-clip-pipeline/.worktrees/kubeflow-infra-platform-layout` on branch `claude/kubeflow-infra-platform-layout`. `lab_clip` work happens only in the worktree `/mnt/data/lab_clip/.worktrees/claude-iac-sync-kubeflow-layout` on branch `claude/iac-sync-kubeflow-layout` (created in Task 5). Paths in Tasks 1-4 are relative to the IaC worktree; paths in Task 5 are relative to the `lab_clip` worktree.
- Do not write comments in code (project rule): HCL, Python, bash.
- Use `git mv` for moves. The old layout must be gone when the change is done.
- Test command, run from the directory named in each step (baseline before this change: root 12, `argo/` 28, `kubeflow/` 72 passing):
  `uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests`
- Do not run `terraform apply` or `terraform destroy`. `terraform plan` is read-only and uses synthetic inputs from the scratchpad, never real credentials.
- Every commit message ends with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Any subagent used for review runs on the Sonnet model (pass `model: "sonnet"`).
- Merging pull requests and dispatching recovery workflows are actions on shared state: stop and ask the user before each, even though the plan lists them.

## Review Focus

- `write_site_patches` must find the templates under `templates/` and must fail with a clear `FileNotFoundError` when they are missing or still in the old locations. Task 2 tests both.
- The kubeconfig default must resolve to `ansible/generated/kubeconfig` from the deeper `kubeflow/infra/terraform`, in `variables.tf`, `terraform.tfvars.example`, `prepare_terraform_inputs.py`, and `bootstrap_minio.sh`. Task 1 tests the Terraform side; the Python side is covered by the existing input test with the changed depth.
- The two script directories must never be on `sys.path` together, because the generic names `apply` and `check` live in `platform/scripts`. Task 2 asserts it.
- No reference to the old layout may remain outside the historical documents. Task 4 greps for it.
- The `lab_clip` gate must accept the new layout and no longer name `kubeflow/terraform`. Task 5 tests the gate commands and the runbook.

---

### Task 1: infra area

**Files:**
- Create: `tests/test_kubeflow_layout.py`, `kubeflow/infra/README.md`
- Rewrite: `tests/test_kubeconfig_contract.py`
- Move: `kubeflow/terraform` -> `kubeflow/infra/terraform`; `kubeflow/scripts/{__init__.py,prepare_terraform_inputs.py,bootstrap_minio.sh}` -> `kubeflow/infra/scripts/`; `kubeflow/tests/{test_terraform_inputs.py,test_kubeflow_terraform.py,test_stack_independence.py}` -> `kubeflow/infra/tests/`
- Modify: `kubeflow/infra/terraform/variables.tf`, `kubeflow/infra/terraform/terraform.tfvars.example`, `kubeflow/infra/scripts/prepare_terraform_inputs.py`, `kubeflow/infra/scripts/bootstrap_minio.sh`, `kubeflow/infra/tests/test_terraform_inputs.py`, `kubeflow/infra/tests/test_stack_independence.py`, `.gitignore`

**Interfaces:**
- Produces: the directory `kubeflow/infra/terraform` (Terraform root), `kubeflow/infra/scripts` (Python package `scripts`), and the ignore patterns for `kubeflow/platform/generated/` that Task 2 relies on. The platform tests under `kubeflow/tests/` are temporarily broken by this task and are fixed in Task 2.

- [ ] **Step 1: Write the failing layout test**

Create `tests/test_kubeflow_layout.py`:

```python
import unittest
from pathlib import Path


KUBEFLOW = Path(__file__).resolve().parents[1] / "kubeflow"


class KubeflowInfraLayoutTests(unittest.TestCase):
    def test_terraform_root_lives_under_infra(self) -> None:
        self.assertFalse((KUBEFLOW / "terraform").exists())

    def test_infra_area_is_complete(self) -> None:
        for relative in (
            "infra/README.md",
            "infra/terraform/variables.tf",
            "infra/scripts/__init__.py",
            "infra/scripts/prepare_terraform_inputs.py",
            "infra/scripts/bootstrap_minio.sh",
            "infra/tests/test_terraform_inputs.py",
            "infra/tests/test_kubeflow_terraform.py",
            "infra/tests/test_stack_independence.py",
        ):
            with self.subTest(path=relative):
                self.assertTrue((KUBEFLOW / relative).is_file())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rewrite the kubeconfig contract test for the new Kubeflow paths**

Replace the whole content of `tests/test_kubeconfig_contract.py` with:

```python
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (REPO_ROOT / "ansible" / "generated" / "kubeconfig").resolve()
TERRAFORM_ROOTS = {
    "argo": REPO_ROOT / "argo" / "terraform",
    "kubeflow": REPO_ROOT / "kubeflow" / "infra" / "terraform",
}
TERRAFORM_IGNORE_PREFIXES = {
    "argo": "argo/terraform",
    "kubeflow": "kubeflow/infra/terraform",
}


def terraform_default(stack: str) -> Path:
    root = TERRAFORM_ROOTS[stack]
    source = (root / "variables.tf").read_text(encoding="utf-8")
    block = re.search(r'variable "kubeconfig_path" \{[^}]*\}', source).group(0)
    default = re.search(r'default\s*=\s*"([^"]+)"', block).group(1)
    return (root / default).resolve()


class KubeconfigContractTests(unittest.TestCase):
    def test_ansible_writes_the_shared_kubeconfig(self) -> None:
        group_vars = (REPO_ROOT / "ansible" / "group_vars" / "all.yml").read_text(encoding="utf-8")
        destination = re.search(r'k3s_kubeconfig_fetch_dest:\s*"([^"]+)"', group_vars).group(1)
        resolved = (REPO_ROOT / "ansible" / "playbooks" / destination.replace("{{ playbook_dir }}/", "")).resolve()
        self.assertEqual(EXPECTED, resolved)

    def test_each_stack_reads_the_shared_kubeconfig(self) -> None:
        for stack in TERRAFORM_ROOTS:
            with self.subTest(stack=stack):
                self.assertEqual(EXPECTED, terraform_default(stack))

    def test_each_stack_example_inputs_use_the_shared_kubeconfig(self) -> None:
        for stack, root in TERRAFORM_ROOTS.items():
            with self.subTest(stack=stack):
                example = (root / "terraform.tfvars.example").read_text(encoding="utf-8")
                value = re.search(r'kubeconfig_path\s*=\s*"([^"]+)"', example).group(1)
                self.assertEqual(EXPECTED, (root / value).resolve())

    def test_each_stack_ignores_its_own_private_files(self) -> None:
        ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for stack, prefix in TERRAFORM_IGNORE_PREFIXES.items():
            for pattern in (
                f"{prefix}/.terraform/",
                f"{prefix}/terraform.tfstate",
                f"{prefix}/terraform.tfvars",
                f"{prefix}/*.auto.tfvars.json",
            ):
                with self.subTest(pattern=pattern):
                    self.assertIn(pattern, ignore)
        self.assertIn("ansible/generated/", ignore)
        self.assertIn("kubeflow/platform/generated/", ignore)
        for stale in ("terraform/generated/", "kubeflow/generated/", "kubeflow/overlays/labclip/generated/"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, ignore)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run both tests to see them fail**

```bash
uv run --no-project --with pyyaml python -m unittest discover -s tests -p 'test_kubeflow_layout.py' 2>&1 | tail -4
uv run --no-project --with pyyaml python -m unittest discover -s tests -p 'test_kubeconfig_contract.py' 2>&1 | tail -4
```

Expected: both FAIL (the infra directories do not exist and the old ignore patterns are still in `.gitignore`).

- [ ] **Step 4: Move the infra files**

```bash
mkdir -p kubeflow/infra/scripts kubeflow/infra/tests
git mv kubeflow/terraform kubeflow/infra/terraform
git mv kubeflow/scripts/__init__.py kubeflow/scripts/prepare_terraform_inputs.py kubeflow/scripts/bootstrap_minio.sh kubeflow/infra/scripts/
git mv kubeflow/tests/test_terraform_inputs.py kubeflow/tests/test_kubeflow_terraform.py kubeflow/tests/test_stack_independence.py kubeflow/infra/tests/
```

- [ ] **Step 5: Fix the kubeconfig depth and the moved tests**

```bash
python3 - <<'EOF'
from pathlib import Path


def edit(path, pairs):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert text.count(old) == 1, (path, old[:80], text.count(old))
        text = text.replace(old, new)
    p.write_text(text, encoding="utf-8")


edit("kubeflow/infra/terraform/variables.tf", [
    ('default     = "../../ansible/generated/kubeconfig"', 'default     = "../../../ansible/generated/kubeconfig"'),
])
edit("kubeflow/infra/terraform/terraform.tfvars.example", [
    ('kubeconfig_path = "../../ansible/generated/kubeconfig"', 'kubeconfig_path = "../../../ansible/generated/kubeconfig"'),
])
edit("kubeflow/infra/scripts/prepare_terraform_inputs.py", [
    ('str(ROOT.parent / "ansible" / "generated" / "kubeconfig")', 'str(ROOT.parent.parent / "ansible" / "generated" / "kubeconfig")'),
])
edit("kubeflow/infra/scripts/bootstrap_minio.sh", [
    ('KUBECONFIG="${repo_root}/../ansible/generated/kubeconfig"', 'KUBECONFIG="${repo_root}/../../ansible/generated/kubeconfig"'),
])
edit("kubeflow/infra/tests/test_terraform_inputs.py", [
    ('Path(__file__).resolve().parents[2] / "ansible" / "generated" / "kubeconfig"', 'Path(__file__).resolve().parents[3] / "ansible" / "generated" / "kubeconfig"'),
])
edit("kubeflow/infra/tests/test_stack_independence.py", [
    ('STACK_ROOT = Path(__file__).resolve().parents[1]\nTERRAFORM_ROOT = STACK_ROOT / "terraform"',
     'STACK_ROOT = Path(__file__).resolve().parents[2]\nTERRAFORM_ROOT = Path(__file__).resolve().parents[1] / "terraform"'),
])
EOF
```

- [ ] **Step 6: Create the infra README**

Create `kubeflow/infra/README.md`:

````markdown
# Kubeflow infra

Terraform root and helper scripts for the Kubeflow stack's cluster foundation.

| Area | Owns |
|---|---|
| `infra/` | NVIDIA device plugin, MinIO, retained cache volumes, run-namespace bindings, and the optional Tailscale operator, installed with Terraform. |
| `platform/` | The pinned Kubeflow Community Distribution, installed with Kustomize and the `prepare`, `render`, `apply`, and `check` tools. |

`platform/scripts/apply.py` and `platform/scripts/check.py` read the owner inventory from `infra/terraform` with `terraform console`, so the Terraform foundation must be applied first.

Run the infra tests from this directory:

```bash
uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests
```
````

- [ ] **Step 7: Update `.gitignore`**

Replace the whole `.gitignore` with:

```
ansible/inventory/hosts.yml
ansible/generated/

argo/terraform/.terraform/
argo/terraform/terraform.tfstate
argo/terraform/terraform.tfstate.backup
argo/terraform/terraform.tfstate.*.backup
argo/terraform/.terraform.tfstate.lock.info
argo/terraform/terraform.tfvars
argo/terraform/*.auto.tfvars.json

kubeflow/infra/terraform/.terraform/
kubeflow/infra/terraform/terraform.tfstate
kubeflow/infra/terraform/terraform.tfstate.backup
kubeflow/infra/terraform/terraform.tfstate.*.backup
kubeflow/infra/terraform/.terraform.tfstate.lock.info
kubeflow/infra/terraform/terraform.tfvars
kubeflow/infra/terraform/*.auto.tfvars.json
kubeflow/platform/generated/

__pycache__/
*.py[cod]

.claude/
.worktrees
```

- [ ] **Step 8: Run the checks**

```bash
uv run --no-project --with pyyaml python -m unittest discover -s tests 2>&1 | tail -4
(cd kubeflow/infra && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -4)
terraform -chdir=kubeflow/infra/terraform fmt -check && terraform -chdir=kubeflow/infra/terraform init -backend=false -input=false >/dev/null 2>&1; terraform -chdir=kubeflow/infra/terraform validate 2>&1 | grep -E 'Success|Error'
```

Expected: root tests `OK`; infra tests `OK`; `Success! The configuration is valid.`

- [ ] **Step 9: Commit**

```bash
git add -A tests kubeflow .gitignore
git commit -m "refactor: move the kubeflow terraform root and helpers under infra

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: platform area

**Files:**
- Move: `kubeflow/tests` -> `kubeflow/platform/tests`; `kubeflow/scripts/apply_kubeflow.py` -> `kubeflow/platform/scripts/apply.py`; `check_kubeflow.py` -> `check.py`; `render_kubeflow.py` -> `render.py`; `prepare_kubeflow_overlay.py` -> `prepare.py`; `kubeflow/overlays/labclip/kustomization.yaml` -> `kubeflow/platform/kustomization.yaml`; the two patch files -> `kubeflow/platform/patches/`; `kubeflow/overlays/labclip/patches/dex-config-template.yaml` and `kubeflow/overlays/labclip/kubeflow-tailnet-ingress.yaml` -> `kubeflow/platform/templates/`
- Modify: the four moved scripts, the four moved tests
- Rewrite: `kubeflow/platform/tests/test_script_paths.py`
- Modify: `tests/test_kubeflow_layout.py`

**Interfaces:**
- Consumes: `kubeflow/infra/terraform` from Task 1.
- Produces: the modules `prepare`, `render`, `apply`, `check` in `kubeflow/platform/scripts`; `prepare.OVERLAY_ROOT` equal to `kubeflow/platform`; `apply.TERRAFORM_ROOT` equal to `kubeflow/infra/terraform`; `apply.GENERATED_ROOT` and `check.GENERATED_ROOT` equal to `kubeflow/platform/generated`.

- [ ] **Step 1: Move the platform tests and rewrite the path test (RED)**

```bash
mkdir -p kubeflow/platform
git mv kubeflow/tests kubeflow/platform/tests
```

Replace the whole content of `kubeflow/platform/tests/test_script_paths.py` with:

```python
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PLATFORM_ROOT / "scripts"
INFRA_TERRAFORM = PLATFORM_ROOT.parent / "infra" / "terraform"


def load(name: str):
    if str(SCRIPTS_ROOT) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_ROOT))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PlatformScriptPathTests(unittest.TestCase):
    def test_apply_defaults_point_at_platform_and_infra(self) -> None:
        apply = load("apply")
        self.assertEqual(PLATFORM_ROOT / "generated", apply.GENERATED_ROOT)
        self.assertEqual(INFRA_TERRAFORM, apply.TERRAFORM_ROOT)
        self.assertTrue(apply.TERRAFORM_ROOT.is_dir())

    def test_check_defaults_point_at_platform(self) -> None:
        check = load("check")
        self.assertEqual(PLATFORM_ROOT / "generated", check.GENERATED_ROOT)

    def test_overlay_root_is_the_platform_directory(self) -> None:
        prepare = load("prepare")
        self.assertEqual(PLATFORM_ROOT, prepare.OVERLAY_ROOT)
        self.assertTrue((prepare.OVERLAY_ROOT / "kustomization.yaml").is_file())
        self.assertTrue((prepare.OVERLAY_ROOT / "templates" / "dex-config-template.yaml").is_file())
        self.assertTrue((prepare.OVERLAY_ROOT / "templates" / "kubeflow-tailnet-ingress.yaml").is_file())

    def test_inventory_has_no_platform_mode(self) -> None:
        apply = load("apply")
        self.assertNotIn("platform_mode", apply.TerraformOwnerInventory.__dataclass_fields__)
        self.assertNotIn("platform_mode", apply.TERRAFORM_CONSOLE_EXPRESSION)

    def test_site_patches_require_the_templates_directory(self) -> None:
        prepare = load("prepare")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                prepare.write_site_patches(None, Path(directory))

    def test_site_patches_ignore_templates_left_in_the_old_locations(self) -> None:
        prepare = load("prepare")
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site / "patches").mkdir()
            (site / "patches" / "dex-config-template.yaml").write_text("data: {}\n", encoding="utf-8")
            (site / "kubeflow-tailnet-ingress.yaml").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                prepare.write_site_patches(None, site)

    def test_infra_and_platform_script_directories_are_not_both_on_the_path(self) -> None:
        load("apply")
        infra_scripts = str(PLATFORM_ROOT.parent / "infra" / "scripts")
        self.assertNotIn(infra_scripts, sys.path)


if __name__ == "__main__":
    unittest.main()
```

Apply these exact edits to the other moved tests:

```bash
python3 - <<'EOF'
import re
from pathlib import Path


def edit(path, pairs):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    for old, new, count in pairs:
        assert text.count(old) == count, (path, old[:80], text.count(old))
        text = text.replace(old, new)
    p.write_text(text, encoding="utf-8")


edit("kubeflow/platform/tests/test_kubeflow_render.py", [
    ('OVERLAY_ROOT = REPOSITORY_ROOT / "overlays/labclip"', 'OVERLAY_ROOT = REPOSITORY_ROOT', 1),
    ('REPOSITORY_ROOT / "scripts/prepare_kubeflow_overlay.py"', 'REPOSITORY_ROOT / "scripts/prepare.py"', 1),
    ('spec_from_file_location("prepare_kubeflow_overlay", PREPARE_SCRIPT)', 'spec_from_file_location("prepare", PREPARE_SCRIPT)', 1),
    ('ignore=shutil.ignore_patterns("generated")', 'ignore=shutil.ignore_patterns("generated", "scripts", "tests")', 2),
])
edit("kubeflow/platform/tests/test_kubeflow_access.py", [
    ('REPOSITORY_ROOT / "overlays/labclip/kubeflow-tailnet-ingress.yaml"', 'REPOSITORY_ROOT / "templates/kubeflow-tailnet-ingress.yaml"', 1),
    ('REPOSITORY_ROOT / "terraform/tailscale.tf"', 'REPOSITORY_ROOT.parent / "infra/terraform/tailscale.tf"', 1),
    ('KUBEFLOW_README = REPOSITORY_ROOT / "README.md"', 'KUBEFLOW_README = REPOSITORY_ROOT.parent / "README.md"', 1),
])

path = Path("kubeflow/platform/tests/test_kubeflow_apply.py")
text = path.read_text(encoding="utf-8")
text = text.replace('"apply_kubeflow.py"', '"apply.py"').replace('"check_kubeflow.py"', '"check.py"')
text = text.replace('"apply_kubeflow"', '"apply"').replace('"check_kubeflow"', '"check"')
text = text.replace("from prepare_kubeflow_overlay import", "from prepare import")
assert text.count('SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts"\n') == 1
text = text.replace(
    'SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts"\n',
    'SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts"\nTERRAFORM_ROOT = REPOSITORY_ROOT.parent / "infra" / "terraform"\n',
)
assert text.count('REPOSITORY_ROOT / "terraform"') >= 1
text = text.replace('REPOSITORY_ROOT / "terraform"', "TERRAFORM_ROOT")
path.write_text(text, encoding="utf-8")
EOF
grep -n 'apply_kubeflow\|check_kubeflow\|prepare_kubeflow_overlay' kubeflow/platform/tests/*.py || echo "tests-clean"
```

Expected: `tests-clean`.

Append to `tests/test_kubeflow_layout.py`, before the `if __name__` line, the platform assertions:

```python
class KubeflowPlatformLayoutTests(unittest.TestCase):
    def test_platform_area_is_complete(self) -> None:
        for relative in (
            "platform/kustomization.yaml",
            "platform/patches/ingress-gateway-service.yaml",
            "platform/patches/trainer-controller-manager-node-selector.yaml",
            "platform/templates/dex-config-template.yaml",
            "platform/templates/kubeflow-tailnet-ingress.yaml",
            "platform/scripts/prepare.py",
            "platform/scripts/render.py",
            "platform/scripts/apply.py",
            "platform/scripts/check.py",
            "platform/tests/test_kubeflow_render.py",
            "platform/tests/test_kubeflow_apply.py",
            "platform/tests/test_kubeflow_access.py",
            "platform/tests/test_script_paths.py",
        ):
            with self.subTest(path=relative):
                self.assertTrue((KUBEFLOW / relative).is_file())

    def test_old_locations_are_gone(self) -> None:
        for old in ("scripts", "tests", "overlays"):
            with self.subTest(old=old):
                self.assertFalse((KUBEFLOW / old).exists())
```

- [ ] **Step 2: Run the platform tests to see them fail**

```bash
(cd kubeflow/platform && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests -p 'test_script_paths.py' 2>&1 | tail -5)
uv run --no-project --with pyyaml python -m unittest discover -s tests -p 'test_kubeflow_layout.py' 2>&1 | tail -4
```

Expected: FAIL (the scripts, kustomization, and templates are not in the platform area yet).

- [ ] **Step 3: Move and rename the platform files**

```bash
mkdir -p kubeflow/platform/scripts kubeflow/platform/patches kubeflow/platform/templates
git mv kubeflow/scripts/apply_kubeflow.py kubeflow/platform/scripts/apply.py
git mv kubeflow/scripts/check_kubeflow.py kubeflow/platform/scripts/check.py
git mv kubeflow/scripts/render_kubeflow.py kubeflow/platform/scripts/render.py
git mv kubeflow/scripts/prepare_kubeflow_overlay.py kubeflow/platform/scripts/prepare.py
git mv kubeflow/overlays/labclip/kustomization.yaml kubeflow/platform/kustomization.yaml
git mv kubeflow/overlays/labclip/patches/ingress-gateway-service.yaml kubeflow/platform/patches/ingress-gateway-service.yaml
git mv kubeflow/overlays/labclip/patches/trainer-controller-manager-node-selector.yaml kubeflow/platform/patches/trainer-controller-manager-node-selector.yaml
git mv kubeflow/overlays/labclip/patches/dex-config-template.yaml kubeflow/platform/templates/dex-config-template.yaml
git mv kubeflow/overlays/labclip/kubeflow-tailnet-ingress.yaml kubeflow/platform/templates/kubeflow-tailnet-ingress.yaml
rm -rf kubeflow/scripts kubeflow/overlays
ls kubeflow
```

Expected: `ls kubeflow` shows only `README.md infra platform`.

- [ ] **Step 4: Fix the script imports, constants, and the template lookup**

```bash
python3 - <<'EOF'
from pathlib import Path


def edit(path, pairs):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert text.count(old) == 1, (path, old[:80], text.count(old))
        text = text.replace(old, new)
    p.write_text(text, encoding="utf-8")


edit("kubeflow/platform/scripts/apply.py", [
    ("from prepare_kubeflow_overlay import RenderApprovalError, verify_render_approval",
     "from prepare import RenderApprovalError, verify_render_approval"),
    ('TERRAFORM_ROOT = REPOSITORY_ROOT / "terraform"',
     'TERRAFORM_ROOT = REPOSITORY_ROOT.parent / "infra" / "terraform"'),
])
edit("kubeflow/platform/scripts/check.py", [
    ("from apply_kubeflow import (", "from apply import ("),
    ("from prepare_kubeflow_overlay import RenderApprovalError", "from prepare import RenderApprovalError"),
])
edit("kubeflow/platform/scripts/render.py", [
    ("from prepare_kubeflow_overlay import (", "from prepare import ("),
])
edit("kubeflow/platform/scripts/prepare.py", [
    ('OVERLAY_ROOT = Path(__file__).resolve().parents[1] / "overlays/labclip"',
     "OVERLAY_ROOT = Path(__file__).resolve().parents[1]"),
    ('dex_template_path = site / "patches/dex-config-template.yaml"',
     'dex_template_path = site / "templates/dex-config-template.yaml"'),
    ('ingress_template_path = site / "kubeflow-tailnet-ingress.yaml"',
     'ingress_template_path = site / "templates/kubeflow-tailnet-ingress.yaml"'),
])
EOF
grep -n 'apply_kubeflow\|check_kubeflow\|prepare_kubeflow_overlay\|render_kubeflow\|overlays/labclip' kubeflow/platform/scripts/*.py || echo "scripts-clean"
```

Expected: `scripts-clean`. `REPOSITORY_ROOT = Path(__file__).resolve().parents[1]` and the `generated` defaults need no change, because `parents[1]` is now the `platform` directory.

- [ ] **Step 5: Run the platform tests and the layout test**

```bash
(cd kubeflow/platform && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -5)
uv run --no-project --with pyyaml python -m unittest discover -s tests 2>&1 | tail -4
```

Expected: platform tests `OK` (about 3 minutes); root tests `OK`. If a render test fails because it copies `scripts` or `tests` into the temporary site, confirm that the `ignore_patterns("generated", "scripts", "tests")` edit applied in both places.

- [ ] **Step 6: Commit**

```bash
git add -A kubeflow tests
git commit -m "refactor: move the kubeflow distribution tooling under platform

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: runbooks and README

**Files:**
- Modify: `README.md`, `kubeflow/README.md`, `kubeflow/infra/terraform/README.md`
- Modify: `kubeflow/infra/tests/test_stack_independence.py` (add a doc test)

**Interfaces:**
- Consumes: the final layout from Tasks 1 and 2.

- [ ] **Step 1: Write the failing documentation test**

Append this method to `KubeflowStackIndependenceTests` in `kubeflow/infra/tests/test_stack_independence.py`, before the `if __name__` line:

```python
    def test_runbooks_use_only_the_new_layout(self) -> None:
        stale = (
            "overlays/labclip",
            "kubeflow/terraform",
            "kubeflow/generated",
            "kubeflow/scripts",
            "apply_kubeflow",
            "check_kubeflow",
            "render_kubeflow",
            "prepare_kubeflow_overlay",
        )
        for readme in (
            STACK_ROOT / "README.md",
            STACK_ROOT / "infra" / "README.md",
            TERRAFORM_ROOT / "README.md",
        ):
            text = readme.read_text(encoding="utf-8")
            for forbidden in stale:
                with self.subTest(readme=str(readme.relative_to(STACK_ROOT)), forbidden=forbidden):
                    self.assertNotIn(forbidden, text)
```

Run it:

```bash
(cd kubeflow/infra && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests -p 'test_stack_independence.py' 2>&1 | tail -4)
```

Expected: FAIL (the runbooks still use the old paths).

- [ ] **Step 2: Apply the path substitutions**

```bash
sed -i \
  -e 's#kubeflow/overlays/labclip/#kubeflow/platform/#g' \
  -e 's#kubeflow/overlays/labclip#kubeflow/platform#g' \
  -e 's#kubeflow/generated/#kubeflow/platform/generated/#g' \
  -e 's#kubeflow/scripts/prepare_terraform_inputs.py#kubeflow/infra/scripts/prepare_terraform_inputs.py#g' \
  -e 's#kubeflow/scripts/prepare_kubeflow_overlay.py#kubeflow/platform/scripts/prepare.py#g' \
  -e 's#kubeflow/scripts/render_kubeflow.py#kubeflow/platform/scripts/render.py#g' \
  -e 's#kubeflow/scripts/apply_kubeflow.py#kubeflow/platform/scripts/apply.py#g' \
  -e 's#kubeflow/scripts/check_kubeflow.py#kubeflow/platform/scripts/check.py#g' \
  -e 's#kubeflow/terraform#kubeflow/infra/terraform#g' \
  -e 's#apply_kubeflow\.py#apply.py#g' \
  -e 's#check_kubeflow\.py#check.py#g' \
  kubeflow/README.md kubeflow/infra/terraform/README.md README.md
sed -i \
  -e 's#](terraform/README.md#](infra/terraform/README.md#g' \
  kubeflow/README.md
sed -i \
  -e 's#](\.\./README.md#](../../README.md#g' \
  kubeflow/infra/terraform/README.md
grep -nE 'overlays|scripts/|generated/|\.\./README|terraform/README' kubeflow/README.md kubeflow/infra/terraform/README.md README.md | head -40
```

Read the output and fix by hand any remaining line that names a moved file with a form the substitutions missed, for example a prose reference to the `overlays` directory or to `kubeflow/scripts`. Prose that says "Kustomize overlay" as a concept may stay.

- [ ] **Step 3: Update the root README table**

In `README.md`, replace the row

```
| [`kubeflow/`](kubeflow/) | Pinned Kubeflow Community Distribution 26.03.1: its own Terraform root, Kustomize overlay, scripts, tests, and runbook. |
```

with

```
| [`kubeflow/`](kubeflow/) | Pinned Kubeflow Community Distribution 26.03.1. `kubeflow/infra/` holds its Terraform root and helpers; `kubeflow/platform/` holds the Kustomize distribution and its prepare, render, apply, and check tools. |
```

Also replace the sentence `Files such as ... are intentionally duplicated in both stacks.` so that it still names `kubeflow/infra/scripts` as the duplicate's location: append " In `kubeflow/`, these live under `infra/`." to that sentence.

- [ ] **Step 4: Run the documentation test and the gates**

```bash
(cd kubeflow/infra && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -4)
(cd kubeflow/platform && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests -p 'test_kubeflow_access.py' 2>&1 | tail -4)
grep -rnE 'overlays/labclip|kubeflow/terraform|kubeflow/generated|kubeflow/scripts|apply_kubeflow|check_kubeflow|render_kubeflow|prepare_kubeflow_overlay' . --include='*.md' --include='*.py' --include='*.sh' --include='*.tf' --include='*.yaml' --include='*.yml' --exclude-dir=.git --exclude-dir=.terraform --exclude-dir=.worktrees --exclude-dir=docs --exclude-dir=__pycache__ | grep -v 'test_stack_independence.py\|test_kubeflow_layout.py\|test_kubeconfig_contract.py' || echo "gate-clean"
```

Expected: both test runs `OK`; the grep prints `gate-clean`. `test_kubeflow_access.py` asserts text in `kubeflow/README.md`; if it fails, restore the sentence it checks rather than weakening the test.

- [ ] **Step 5: Commit**

```bash
git add -A README.md kubeflow
git commit -m "docs: document the kubeflow infra and platform layout

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: IaC verification

**Files:**
- No new files.

**Interfaces:**
- Consumes: Tasks 1-3.

- [ ] **Step 1: Run every test area and the Terraform checks**

```bash
git status --short | head -3
uv run --no-project --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3
(cd argo && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3)
(cd kubeflow/infra && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3)
(cd kubeflow/platform && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3)
for root in argo/terraform kubeflow/infra/terraform; do terraform -chdir=$root fmt -check && terraform -chdir=$root init -backend=false -input=false >/dev/null 2>&1; terraform -chdir=$root validate 2>&1 | grep -E 'Success|Error'; done
```

Expected: clean tree, four `OK` results, two `Success!` lines.

- [ ] **Step 2: Confirm that the rendered manifest inputs are byte-identical**

```bash
git diff -M100% --summary origin/develop...HEAD | grep -E 'kustomization.yaml|templates/|patches/'
```

Expected: five lines, each starting with `rename`, covering `kustomization.yaml`, `dex-config-template.yaml`, `kubeflow-tailnet-ingress.yaml`, `ingress-gateway-service.yaml`, and `trainer-controller-manager-node-selector.yaml`. A `-M100%` rename means the content is unchanged, so the rendered Kubeflow manifest is unchanged by construction.

- [ ] **Step 3: Read-only plan of the Kubeflow Terraform root with synthetic inputs**

```bash
SC=/tmp/claude-1000/-mnt-data-lab-clip/4fb07f5d-a1a5-4547-848e-f9c9cfa1dfb1/scratchpad
ls $SC/synthetic.tfvars.json
terraform -chdir=kubeflow/infra/terraform plan -input=false -lock=false -no-color -var-file=$SC/synthetic.tfvars.json 2>&1 | grep -E '^(Plan:|Error)'
```

Expected: `Plan: 18 to add, 0 to change, 0 to destroy.` If `synthetic.tfvars.json` is missing, recreate it with the structure the earlier verification used (MinIO root and per-store credentials of at least 16 characters, researcher keys of 8 to 40 characters, a ghcr JSON, W&B values, `kubeconfig_path` of `/home/jwchoi/.kube/config`).

- [ ] **Step 4: Push the IaC branch without opening the pull request yet**

```bash
git push -u origin claude/kubeflow-infra-platform-layout
```

The pull request is opened in Task 6 after the `lab_clip` gate is merged.

---

### Task 5: lab_clip gate

**Files (in the `lab_clip` worktree):**
- Modify: `scripts/iac_submodule_sync.py`, `tests/test_iac_submodule_sync.py`, `docs/agents/iac-submodule-sync.md`

**Interfaces:**
- Produces: `IAC_STACK_TERRAFORM_ROOTS` and `IAC_STACK_TEST_DIRECTORIES` module constants in `scripts/iac_submodule_sync.py`; the gate runs each stack's `terraform` commands against its root and each test directory's unit tests.

- [ ] **Step 1: Create the worktree**

```bash
cd /mnt/data/lab_clip
git fetch --prune -q origin
git worktree add .worktrees/claude-iac-sync-kubeflow-layout -b claude/iac-sync-kubeflow-layout origin/develop
cd .worktrees/claude-iac-sync-kubeflow-layout
```

- [ ] **Step 2: Update the gate test to the new layout (RED)**

In `tests/test_iac_submodule_sync.py`, replace the whole function `test_gate_validates_both_stacks_and_the_shared_ansible_layer` with:

```python
def test_gate_validates_both_stacks_and_the_shared_ansible_layer(tmp_path) -> None:
    steps: list[tuple[str, list[str], Path]] = []

    def record(step, command, *, cwd, environment):
        steps.append((step, command, cwd))

    iac_root = tmp_path / "3t-clip-pipeline"
    with patch.object(sync, "run_gate_step", side_effect=record):
        with patch.object(sync, "verify_labclip_compatibility"):
            receiver().run_no_cluster_gate(
                iac_root, tmp_path / "lab_clip", gate_home=tmp_path / "home"
            )

    commands = [" ".join(command) for _, command, _ in steps]
    assert not any("-chdir=terraform" in command for command in commands)
    assert not any("-chdir=kubeflow/terraform" in command for command in commands)
    for root in ("argo/terraform", "kubeflow/infra/terraform"):
        for action in ("fmt -check -recursive", "init -backend=false", "validate -no-color"):
            assert any(
                f"-chdir={root} {action}" in command for command in commands
            ), (root, action)
    unit_directories = {cwd for step, command, cwd in steps if "unittest" in command}
    assert unit_directories == {
        iac_root,
        iac_root / "argo",
        iac_root / "kubeflow" / "infra",
        iac_root / "kubeflow" / "platform",
    }
    assert any("ansible-playbook" in command for command in commands)
```

Also replace the whole function `test_sync_runbook_describes_the_two_stack_layout` with:

```python
def test_sync_runbook_describes_the_two_stack_layout() -> None:
    runbook = (ROOT / "docs/agents/iac-submodule-sync.md").read_text(encoding="utf-8")
    assert "-chdir=terraform" not in runbook
    assert "-chdir=kubeflow/terraform" not in runbook
    assert "kubeflow/overlays/labclip" not in runbook
    assert "infra/3t-clip-pipeline/terraform" not in runbook
    assert re.search(r"(?<!\.\./)\.\./\.\./lab_clip/pipeline", runbook) is None
    for root in ("argo/terraform", "kubeflow/infra/terraform"):
        assert f"terraform -chdir={root} fmt -check -recursive" in runbook
    assert "kubeflow/platform/kustomization.yaml" in runbook
    assert "kubeflow/platform/tests/test_kubeflow_render.py" in runbook
    assert "infra/3t-clip-pipeline/argo/terraform/../../../../pipeline/k8s/generated/labclip-train.yaml" in runbook
    assert 'labclip_workflow_template_path = "../../../../pipeline/k8s/generated/labclip-train.yaml"' in runbook
```

Run:

```bash
uv run --no-project --with pytest --with pyyaml python -m pytest tests/test_iac_submodule_sync.py -q 2>&1 | tail -4
```

Expected: 2 FAILED, the rest pass.

- [ ] **Step 3: Update the gate**

In `scripts/iac_submodule_sync.py` apply these edits:

```bash
python3 - <<'EOF'
from pathlib import Path

path = Path("scripts/iac_submodule_sync.py")
text = path.read_text(encoding="utf-8")


def swap(old, new):
    global text
    assert text.count(old) == 1, old[:80]
    text = text.replace(old, new)


swap(
    'IAC_STACKS = ("argo", "kubeflow")\n',
    'IAC_STACKS = ("argo", "kubeflow")\n'
    'IAC_STACK_TERRAFORM_ROOTS = {\n'
    '    "argo": "argo/terraform",\n'
    '    "kubeflow": "kubeflow/infra/terraform",\n'
    '}\n'
    'IAC_STACK_TEST_DIRECTORIES = {\n'
    '    "argo": ("argo",),\n'
    '    "kubeflow": ("kubeflow/infra", "kubeflow/platform"),\n'
    '}\n',
)
swap(
    '    for stack in IAC_STACKS:\n'
    '        run_gate_step(\n'
    '            f"IaC {stack} stack unit tests",\n'
    '            unit_command,\n'
    '            cwd=iac_root / stack,\n'
    '            environment=environment,\n'
    '        )\n',
    '    for stack in IAC_STACKS:\n'
    '        for test_directory in IAC_STACK_TEST_DIRECTORIES[stack]:\n'
    '            run_gate_step(\n'
    '                f"IaC {test_directory} unit tests",\n'
    '                unit_command,\n'
    '                cwd=iac_root / test_directory,\n'
    '                environment=environment,\n'
    '            )\n',
)
swap(
    '        terraform_chdir = f"-chdir={stack}/terraform"\n',
    '        terraform_chdir = f"-chdir={IAC_STACK_TERRAFORM_ROOTS[stack]}"\n',
)
path.write_text(text, encoding="utf-8")
EOF
```

- [ ] **Step 4: Update the runbook**

In `docs/agents/iac-submodule-sync.md`:

```bash
python3 - <<'EOF'
from pathlib import Path

path = Path("docs/agents/iac-submodule-sync.md")
text = path.read_text(encoding="utf-8")


def swap(old, new):
    global text
    assert text.count(old) == 1, old[:80]
    text = text.replace(old, new)


swap(
    "(cd kubeflow && uv run --no-project --with PyYAML --with bcrypt==4.2.1 python -m unittest discover -s tests)\n",
    "(cd kubeflow/infra && uv run --no-project --with PyYAML --with bcrypt==4.2.1 python -m unittest discover -s tests)\n"
    "(cd kubeflow/platform && uv run --no-project --with PyYAML --with bcrypt==4.2.1 python -m unittest discover -s tests)\n",
)
swap(
    "terraform -chdir=kubeflow/terraform fmt -check -recursive\nterraform -chdir=kubeflow/terraform init -backend=false -input=false -lockfile=readonly\nterraform -chdir=kubeflow/terraform validate -no-color\n",
    "terraform -chdir=kubeflow/infra/terraform fmt -check -recursive\nterraform -chdir=kubeflow/infra/terraform init -backend=false -input=false -lockfile=readonly\nterraform -chdir=kubeflow/infra/terraform validate -no-color\n",
)
swap(
    "The Kubeflow stack's unit suite includes `kubeflow/tests/test_kubeflow_render.py`.",
    "The Kubeflow platform's unit suite includes `kubeflow/platform/tests/test_kubeflow_render.py`.",
)
swap(
    "`kubeflow/overlays/labclip/kustomization.yaml`.",
    "`kubeflow/platform/kustomization.yaml`.",
)
swap(
    "root, except that each stack's unit tests run from that stack's own directory.",
    "root, except that each stack's unit tests run from that stack's own test directories (`argo/`, `kubeflow/infra/`, and `kubeflow/platform/`).",
)
path.write_text(text, encoding="utf-8")
EOF
```

- [ ] **Step 5: Run the tests and a compatibility check against the real IaC tree**

```bash
uv run --no-project --with pytest --with pyyaml python -m pytest -q 2>&1 | tail -3
grep -rnE 'kubeflow/terraform|overlays/labclip|kubeflow/tests' docs/agents scripts tests .github 2>/dev/null | grep -v 'docs/superpowers' || echo "gate-clean"
```

Expected: the whole suite passes (557 before this change plus no new failures); `gate-clean`.

- [ ] **Step 6: Commit, push, and open the pull request**

```bash
git add docs/agents/iac-submodule-sync.md scripts/iac_submodule_sync.py tests/test_iac_submodule_sync.py
git commit -m "fix: validate the kubeflow infra and platform layout in the sync gate

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push -u origin claude/iac-sync-kubeflow-layout
```

Open the pull request against `develop` with `gh pr create` (outside the sandbox), titled `fix: IaC 동기화 게이트를 kubeflow infra/platform 구조에 맞게 수정`, with a Korean body that states what changed (stack roots and test directories), why (the IaC repository moves `kubeflow/terraform` to `kubeflow/infra/terraform` and splits its tests), the impact (merge this pull request before the IaC pull request, or the synchronization locks), and the verification (full `pytest` result). End it with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

### Task 6: rollout

**Interfaces:**
- Consumes: the pull request from Task 5 and the pushed IaC branch from Task 4.

- [ ] **Step 1: Ask the user to approve merging the `lab_clip` pull request first**

State that the gate pull request must merge before the IaC pull request, because the gate runs only when the IaC repository's `develop` receives a push. Wait for approval, then run `gh pr merge <number> --repo jayn2u/lab_clip --merge`.

- [ ] **Step 2: Open the IaC pull request**

Run `gh pr create --repo jayn2u/3t-clip-pipeline --base develop --head claude/kubeflow-infra-platform-layout` (outside the sandbox) with a Korean title `refactor: kubeflow를 infra와 platform 영역으로 재구성` and a Korean body with what changed, why, impact, and verification (the four test areas, both Terraform roots, the `plan` result, the 100% rename check). Mention that Task 5's `lab_clip` pull request was merged first. End it with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 3: Ask the user to approve merging the IaC pull request, then merge it**

After approval run `gh pr merge <number> --repo jayn2u/3t-clip-pipeline --merge`.

- [ ] **Step 4: Verify the synchronization**

```bash
gh run list --repo jayn2u/lab_clip --limit 1 | cat
gh variable list --repo jayn2u/lab_clip | cat
git -C /mnt/data/lab_clip fetch -q origin
git -C /mnt/data/lab_clip log origin/develop --oneline -2
```

Expected: the run `IaC submodule sync` succeeds, `IAC_SYNC_STATE` is `clear`, and `origin/develop` has a `chore(infra): sync 3t-clip-pipeline to <sha>` commit. If the run fails, read the failing step, fix the gate in a new pull request, and ask the user before dispatching the recovery workflow with the candidate SHA.

- [ ] **Step 5: Clean up**

Ask the user, then remove the two worktrees and merged local branches the same way as in the previous change, sync `/mnt/data/lab_clip` to `develop`, and update its submodule.
