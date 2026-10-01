# Split Argo and Kubeflow IaC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single `terraform/` root and its `platform_mode` switch with two independent stacks, `argo/` and `kubeflow/`, each owning its Terraform root, scripts, tests, and runbook.

**Architecture:** `argo/terraform` is the Kubeflow-free Terraform tree from commit `5b01f5d` plus the later unrelated Tailscale precondition. `kubeflow/terraform` is the current tree with the Argo resources and the `platform_mode` switch removed. Each stack carries its own copies of the shared MinIO, device-plugin, and input-preparation files. Each root labels the `argo` namespace with its stack name and refuses to plan when another stack owns it. Only `ansible/` is shared, and only through the kubeconfig at `ansible/generated/kubeconfig`.

**Tech Stack:** Terraform 1.16 (providers kubernetes ~2.31, helm ~2.14, kubectl ~1.14), Python 3.12 `unittest`, bash, Ansible group vars, git.

**Spec:** `docs/superpowers/specs/2026-10-01-split-argo-kubeflow-iac-design.md`

## Global Constraints

- Work only in the worktree `/mnt/data/3t-clip-pipeline/.worktrees/split-argo-kubeflow-iac` on branch `claude/split-argo-kubeflow-iac`. Every path below is relative to that directory.
- Do not write comments in code (project rule). This applies to HCL, Python, and bash.
- Use `git mv` for moves so history follows the files. Copies of shared files use `cp` before the original is moved.
- Do not run `terraform apply` or `terraform destroy`. `terraform plan` is read-only and allowed.
- Do not copy or modify anything under `/mnt/data/3t-clip-pipeline/terraform/` (the operator's existing state and private inputs).
- Test command (baseline: 82 tests pass in about 160 s on `develop`). Run it from the directory named in each step:
  `uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests`
- Every commit message ends with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Kubeflow script logic changes only in path constants and in the removal of the `platform_mode` inventory field and its two guards.

## Review Focus

- A second stack applied over a cluster that the first stack owns must fail at plan time. Task 3 tests that both guards exist, name their own stack, and read the other stack's label.
- A namespace without the label (legacy or fresh) must pass. Task 3 tests that the missing-label default is the stack's own name.
- The Argo root must not inherit any Kubeflow variable, file, or resource. Task 2 tests absence of each.
- Kubeflow scripts must not keep defaults that point at the removed `terraform/`, `kubeflow/generated`, or `kubeflow/overlays` paths. Task 4 adds a test that pins every default path to the new layout.
- Ansible's kubeconfig output and each stack's `kubeconfig_path` default must resolve to the same file. Task 5 tests this.

---

### Task 1: Kubeflow Terraform root

**Files:**
- Move: `terraform/` -> `kubeflow/terraform/`
- Delete: `kubeflow/terraform/argo_workflows.tf`, `kubeflow/terraform/rbac.tf`
- Modify: `kubeflow/terraform/variables.tf`, `kubeflow/terraform/kubeflow_integration.tf:2`, `kubeflow/terraform/storage.tf`, `kubeflow/terraform/outputs.tf`
- Move and modify: `tests/test_terraform_platform_mode.py` -> `kubeflow/tests/test_kubeflow_terraform.py`
- Create: `kubeflow/tests/test_stack_independence.py`

**Interfaces:**
- Produces: the directory `kubeflow/terraform/` with a root module that has no `platform_mode` variable and no Argo resources. Later tasks add `ownership_guard.tf` here and rely on `var.argo_namespace`, `local.kubeflow_run_bindings_enabled`, and `kubernetes_namespace.argo`, all of which keep their current names.

- [ ] **Step 1: Write the failing independence test**

Create `kubeflow/tests/test_stack_independence.py`:

```python
import re
import unittest
from pathlib import Path


STACK_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = STACK_ROOT / "terraform"
CODE_SUFFIXES = {".tf", ".py", ".sh"}
OTHER_STACK_PATH = re.compile(r"(\.\./)+argo/|(^|[\s\"'=(])argo/(terraform|scripts|tests)")


def terraform_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TERRAFORM_ROOT.glob("*.tf"))
    }


class KubeflowStackIndependenceTests(unittest.TestCase):
    def test_root_exists_in_stack_directory(self) -> None:
        self.assertTrue((TERRAFORM_ROOT / "variables.tf").is_file())

    def test_no_platform_mode_switch(self) -> None:
        for name, source in terraform_sources().items():
            with self.subTest(file=name):
                self.assertNotIn("platform_mode", source)

    def test_no_standalone_argo_resources(self) -> None:
        names = set(terraform_sources())
        self.assertNotIn("argo_workflows.tf", names)
        self.assertNotIn("rbac.tf", names)
        combined = "\n".join(terraform_sources().values())
        self.assertNotIn('helm_release" "argo_workflows"', combined)
        self.assertNotIn('kubectl_manifest" "labclip_train"', combined)
        self.assertNotIn("labclip_workflow_template_path", combined)
        self.assertNotIn("argo_workflows_chart_version", combined)

    def test_code_does_not_reference_the_argo_stack_directory(self) -> None:
        this_file = Path(__file__).resolve()
        for path in sorted(STACK_ROOT.rglob("*")):
            parts = set(path.parts)
            if not path.is_file() or path.suffix not in CODE_SUFFIXES:
                continue
            if parts & {"generated", ".terraform", "__pycache__"} or path.resolve() == this_file:
                continue
            with self.subTest(file=str(path.relative_to(STACK_ROOT))):
                self.assertIsNone(OTHER_STACK_PATH.search(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Move the root and run the test to see it fail**

```bash
git mv terraform kubeflow/terraform
mkdir -p kubeflow/tests
cd kubeflow && uv run --no-project python -m unittest tests.test_stack_independence 2>&1 | tail -8; cd ..
```

Expected: FAIL (`platform_mode` still present, `argo_workflows.tf` and `rbac.tf` still exist).

- [ ] **Step 3: Strip the Argo resources and the switch**

```bash
git rm -q kubeflow/terraform/argo_workflows.tf kubeflow/terraform/rbac.tf
python3 - <<'EOF'
import re
from pathlib import Path

path = Path("kubeflow/terraform/variables.tf")
source = path.read_text(encoding="utf-8")
for name in ("platform_mode", "argo_workflows_chart_version", "labclip_workflow_template_path"):
    match = re.search(rf'variable "{name}" \{{', source)
    depth = 0
    end = None
    for index in range(match.start(), len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    source = source[: match.start()] + source[end:].lstrip("\n")
path.write_text(source, encoding="utf-8")
EOF
```

Edit `kubeflow/terraform/kubeflow_integration.tf`, replacing the line

```
  kubeflow_run_bindings_enabled    = var.platform_mode == "kubeflow" && var.enable_kubeflow_run_bindings
```

with

```
  kubeflow_run_bindings_enabled    = var.enable_kubeflow_run_bindings
```

Edit `kubeflow/terraform/storage.tf`, replacing the whole `locals { ... }` block above `resource "kubernetes_persistent_volume_claim" "cache"` with:

```
locals {
  cache_claims          = local.kubeflow_run_bindings_enabled ? var.nodes : {}
  cache_claim_namespace = var.labclip_run_namespace
}
```

Edit `kubeflow/terraform/outputs.tf`, deleting the block

```
output "platform_mode" {
  value = var.platform_mode
}

```

Then:

```bash
terraform -chdir=kubeflow/terraform fmt
grep -rn 'platform_mode\|labclip_train\|argo_workflows\|labclip_runner' kubeflow/terraform --include='*.tf' || echo "clean"
```

Expected: `clean` (README files are handled in Task 6).

- [ ] **Step 4: Move and update the Terraform structure test**

```bash
git mv tests/test_terraform_platform_mode.py kubeflow/tests/test_kubeflow_terraform.py
```

In `kubeflow/tests/test_kubeflow_terraform.py`:

1. Delete the whole methods `test_default_mode_keeps_argo` and `test_kubeflow_mode_has_no_standalone_argo`.
2. Rename the class `TerraformPlatformModeTests` to `KubeflowTerraformTests`.
3. In `test_run_cache_claim_has_one_namespace`, replace the two `assertRegex(storage_locals, ...)` calls with:

```python
        self.assertRegex(
            storage_locals,
            r'cache_claims\s*=\s*local\.kubeflow_run_bindings_enabled\s*\?\s*var\.nodes\s*:\s*\{\}',
        )
        self.assertRegex(
            storage_locals,
            r'cache_claim_namespace\s*=\s*var\.labclip_run_namespace',
        )
```

4. In the same method, replace the `kubeflow_run_bindings_enabled` regex (the one containing `var\.platform_mode\s*==\s*"kubeflow"\s*&&\s*`) with:

```python
        self.assertRegex(
            kubeflow_integration,
            r'kubeflow_run_bindings_enabled\s*=\s*var\.enable_kubeflow_run_bindings',
        )
```

5. In the same method, replace `self.assertRegex(outputs, r'output\s+"platform_mode"')` with `self.assertNotRegex(outputs, r'output\s+"platform_mode"')`.
6. Run `grep -n platform_mode kubeflow/tests/test_kubeflow_terraform.py`. Every remaining hit is an assertion that the old switch exists; remove the `var.platform_mode == "kubeflow" && ` fragment from its regex or delete the assertion. Expected final result: only the `assertNotRegex` line from item 5 mentions `platform_mode`.

- [ ] **Step 5: Run the tests and the Terraform checks**

```bash
cd kubeflow && uv run --no-project python -m unittest tests.test_stack_independence tests.test_kubeflow_terraform 2>&1 | tail -6; cd ..
terraform -chdir=kubeflow/terraform init -backend=false -input=false >/dev/null && terraform -chdir=kubeflow/terraform validate
```

Expected: tests `OK`; `Success! The configuration is valid.`

- [ ] **Step 6: Commit**

```bash
git add -A kubeflow terraform tests
git commit -m "refactor: make terraform root Kubeflow-only under kubeflow/terraform

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Argo Terraform root

**Files:**
- Create: `argo/terraform/` (extracted from `5b01f5d:terraform`), `argo/tests/test_stack_independence.py`
- Modify: `argo/terraform/variables.tf` (`labclip_workflow_template_path` default), `argo/terraform/tailscale.tf` (taken from `1aba0a6`)

**Interfaces:**
- Produces: `argo/terraform/` root with `kubernetes_namespace.argo`, `var.argo_namespace`, and no Kubeflow names. Task 3 adds `ownership_guard.tf` and edits `namespace.tf`; Task 5 edits the `kubeconfig_path` default.

- [ ] **Step 1: Write the failing independence test**

Create `argo/tests/test_stack_independence.py`:

```python
import re
import unittest
from pathlib import Path


STACK_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = STACK_ROOT / "terraform"
CODE_SUFFIXES = {".tf", ".py", ".sh"}
OTHER_STACK_PATH = re.compile(r"(\.\./)+kubeflow/|(^|[\s\"'=(])kubeflow/(terraform|scripts|tests|overlays)")
KUBEFLOW_NAMES = (
    "platform_mode",
    "enable_kubeflow_run_bindings",
    "confirm_kubeflow_cache_pv_rebind",
    "labclip_run_namespace",
    "kubeflow_run_bindings_guard",
    "kubeflow_cache",
)


def terraform_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TERRAFORM_ROOT.glob("*.tf"))
    }


class ArgoStackIndependenceTests(unittest.TestCase):
    def test_root_exists_in_stack_directory(self) -> None:
        self.assertTrue((TERRAFORM_ROOT / "argo_workflows.tf").is_file())

    def test_no_kubeflow_files_or_names(self) -> None:
        self.assertNotIn("kubeflow_integration.tf", set(terraform_sources()))
        for name, source in terraform_sources().items():
            for forbidden in KUBEFLOW_NAMES:
                with self.subTest(file=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_argo_resources_are_unconditional(self) -> None:
        source = terraform_sources()["argo_workflows.tf"]
        self.assertIn('resource "helm_release" "argo_workflows"', source)
        self.assertIn('resource "kubectl_manifest" "labclip_train"', source)
        self.assertNotIn("count", source)

    def test_code_does_not_reference_the_kubeflow_stack_directory(self) -> None:
        this_file = Path(__file__).resolve()
        for path in sorted(STACK_ROOT.rglob("*")):
            parts = set(path.parts)
            if not path.is_file() or path.suffix not in CODE_SUFFIXES:
                continue
            if parts & {"generated", ".terraform", "__pycache__"} or path.resolve() == this_file:
                continue
            with self.subTest(file=str(path.relative_to(STACK_ROOT))):
                self.assertIsNone(OTHER_STACK_PATH.search(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to see it fail**

```bash
mkdir -p argo/tests && cd argo && uv run --no-project python -m unittest tests.test_stack_independence 2>&1 | tail -5; cd ..
```

Expected: FAIL (`argo/terraform` does not exist).

- [ ] **Step 3: Create the Argo root from the Argo-only history**

```bash
git archive 5b01f5d terraform | tar -x -C argo
git show 1aba0a6:terraform/tailscale.tf > argo/terraform/tailscale.tf
```

Edit `argo/terraform/variables.tf`, replacing

```
  default     = "../../lab_clip/pipeline/k8s/generated/labclip-train.yaml"
```

with

```
  default     = "../../../lab_clip/pipeline/k8s/generated/labclip-train.yaml"
```

- [ ] **Step 4: Confirm the tree differs from `5b01f5d` only where intended**

```bash
for f in $(ls argo/terraform); do git show 5b01f5d:terraform/$f 2>/dev/null | diff -q - argo/terraform/$f >/dev/null || echo "differs: $f"; done
```

Expected output lists exactly `differs: tailscale.tf` and `differs: variables.tf`.

- [ ] **Step 5: Run the tests and the Terraform checks**

```bash
cd argo && uv run --no-project python -m unittest tests.test_stack_independence 2>&1 | tail -5; cd ..
terraform -chdir=argo/terraform fmt -check
terraform -chdir=argo/terraform init -backend=false -input=false >/dev/null && terraform -chdir=argo/terraform validate
```

Expected: tests `OK`; `fmt -check` prints nothing; `Success! The configuration is valid.`

- [ ] **Step 6: Commit**

```bash
git add argo
git commit -m "feat: add Argo-only terraform root under argo/terraform

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Cross-stack ownership guard

**Files:**
- Create: `argo/terraform/ownership_guard.tf`, `kubeflow/terraform/ownership_guard.tf`
- Modify: `argo/terraform/namespace.tf`, `kubeflow/terraform/namespace.tf`
- Modify: `argo/tests/test_stack_independence.py`, `kubeflow/tests/test_stack_independence.py`

**Interfaces:**
- Consumes: `var.argo_namespace`, `kubernetes_namespace.argo` from Tasks 1 and 2.
- Produces: `terraform_data.ownership_guard`, `local.iac_stack_name` (`"argo"` or `"kubeflow"`), label key `labclip.io/iac-stack` on the namespace.

- [ ] **Step 1: Write the failing guard tests**

Append this method to `ArgoStackIndependenceTests` in `argo/tests/test_stack_independence.py`:

```python
    def test_ownership_guard_names_this_stack(self) -> None:
        guard = (TERRAFORM_ROOT / "ownership_guard.tf").read_text(encoding="utf-8")
        self.assertRegex(guard, r'iac_stack_name\s*=\s*"argo"')
        self.assertIn('data "kubernetes_resources" "ownership_namespace"', guard)
        self.assertIn(
            'try(ns.metadata.labels["labclip.io/iac-stack"], local.iac_stack_name) != local.iac_stack_name',
            guard,
        )
        self.assertIn("precondition", guard)
        namespace = (TERRAFORM_ROOT / "namespace.tf").read_text(encoding="utf-8")
        self.assertIn('"labclip.io/iac-stack" = local.iac_stack_name', namespace)
        self.assertIn("terraform_data.ownership_guard", namespace)
```

Append the same method to `KubeflowStackIndependenceTests` in `kubeflow/tests/test_stack_independence.py` with `"argo"` replaced by `"kubeflow"` in the `iac_stack_name` regex only.

- [ ] **Step 2: Run both to see them fail**

```bash
(cd argo && uv run --no-project python -m unittest tests.test_stack_independence 2>&1 | tail -4)
(cd kubeflow && uv run --no-project python -m unittest tests.test_stack_independence 2>&1 | tail -4)
```

Expected: both FAIL (`ownership_guard.tf` missing).

- [ ] **Step 3: Add the guard and the namespace label**

Create `argo/terraform/ownership_guard.tf`:

```hcl
data "kubernetes_resources" "ownership_namespace" {
  api_version    = "v1"
  kind           = "Namespace"
  field_selector = "metadata.name=${var.argo_namespace}"
}

locals {
  iac_stack_name = "argo"
  foreign_stack_namespaces = [
    for ns in data.kubernetes_resources.ownership_namespace.objects : ns.metadata.name
    if try(ns.metadata.labels["labclip.io/iac-stack"], local.iac_stack_name) != local.iac_stack_name
  ]
}

resource "terraform_data" "ownership_guard" {
  input = local.iac_stack_name

  lifecycle {
    precondition {
      condition     = length(local.foreign_stack_namespaces) == 0
      error_message = "The ${var.argo_namespace} namespace belongs to another LabCLIP IaC stack. Destroy that stack before applying this one."
    }
  }
}
```

Create `kubeflow/terraform/ownership_guard.tf` with identical content except `iac_stack_name = "kubeflow"`.

Replace the whole content of `argo/terraform/namespace.tf` and `kubeflow/terraform/namespace.tf` with:

```hcl
resource "kubernetes_namespace" "argo" {
  metadata {
    name = var.argo_namespace
    labels = {
      "labclip.io/iac-stack" = local.iac_stack_name
    }
  }

  depends_on = [terraform_data.ownership_guard]
}
```

- [ ] **Step 4: Run the tests and the Terraform checks**

```bash
(cd argo && uv run --no-project python -m unittest tests.test_stack_independence 2>&1 | tail -4)
(cd kubeflow && uv run --no-project python -m unittest tests.test_stack_independence 2>&1 | tail -4)
for stack in argo kubeflow; do terraform -chdir=$stack/terraform fmt -check && terraform -chdir=$stack/terraform validate; done
```

Expected: tests `OK`; both validations report `Success! The configuration is valid.`

- [ ] **Step 5: Commit**

```bash
git add argo kubeflow
git commit -m "feat: guard against applying the other IaC stack over the argo namespace

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Relocate scripts and tests, fix paths

**Files:**
- Create (copy, then edit): `kubeflow/scripts/__init__.py`, `kubeflow/scripts/prepare_terraform_inputs.py`, `kubeflow/scripts/bootstrap_minio.sh`
- Move: `scripts/{__init__,prepare_terraform_inputs,smoke_labclip_runtime,smoke_labclip_worker,smoke_cleanup_markers}.py`, `scripts/bootstrap_minio.sh` -> `argo/scripts/`
- Move: `scripts/{apply_kubeflow,check_kubeflow,prepare_kubeflow_overlay,render_kubeflow}.py` -> `kubeflow/scripts/`
- Move: `tests/test_terraform_inputs.py` (copied to both stacks), `tests/test_smoke_*.py` -> `argo/tests/`; `tests/test_kubeflow_{access,apply,render}.py` -> `kubeflow/tests/`
- Create: `kubeflow/tests/test_script_paths.py`

**Interfaces:**
- Produces: in `kubeflow/scripts/apply_kubeflow.py`, `GENERATED_ROOT = REPOSITORY_ROOT / "generated"` and `TERRAFORM_ROOT = REPOSITORY_ROOT / "terraform"`, where `REPOSITORY_ROOT` is now the `kubeflow/` directory; `TerraformOwnerInventory` without a `platform_mode` field. `prepare_terraform_inputs.py` in both stacks writes `kubeconfig_path` as `<repo>/ansible/generated/kubeconfig`.

- [ ] **Step 1: Write the failing path-contract test**

Create `kubeflow/tests/test_script_paths.py`:

```python
import importlib.util
import sys
import unittest
from pathlib import Path


STACK_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = STACK_ROOT / "scripts"


def load(name: str):
    if str(SCRIPTS_ROOT) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_ROOT))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class KubeflowScriptPathTests(unittest.TestCase):
    def test_apply_defaults_point_inside_the_stack(self) -> None:
        apply = load("apply_kubeflow")
        self.assertEqual(STACK_ROOT / "generated", apply.GENERATED_ROOT)
        self.assertEqual(STACK_ROOT / "terraform", apply.TERRAFORM_ROOT)
        self.assertTrue(apply.TERRAFORM_ROOT.is_dir())

    def test_check_defaults_point_inside_the_stack(self) -> None:
        check = load("check_kubeflow")
        self.assertEqual(STACK_ROOT / "generated", check.GENERATED_ROOT)

    def test_overlay_root_points_at_the_moved_overlay(self) -> None:
        prepare = load("prepare_kubeflow_overlay")
        self.assertEqual(STACK_ROOT / "overlays" / "labclip", prepare.OVERLAY_ROOT)
        self.assertTrue((prepare.OVERLAY_ROOT / "kustomization.yaml").is_file())

    def test_inventory_has_no_platform_mode(self) -> None:
        apply = load("apply_kubeflow")
        self.assertNotIn("platform_mode", apply.TerraformOwnerInventory.__dataclass_fields__)
        self.assertNotIn("platform_mode", apply.TERRAFORM_CONSOLE_EXPRESSION)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Copy shared files, move everything, run the test to see it fail**

```bash
mkdir -p kubeflow/scripts argo/scripts argo/tests kubeflow/tests
cp scripts/__init__.py scripts/prepare_terraform_inputs.py scripts/bootstrap_minio.sh kubeflow/scripts/
cp tests/test_terraform_inputs.py kubeflow/tests/test_terraform_inputs.py
git add kubeflow/scripts kubeflow/tests/test_terraform_inputs.py
git mv scripts/__init__.py scripts/prepare_terraform_inputs.py scripts/bootstrap_minio.sh \
  scripts/smoke_labclip_runtime.py scripts/smoke_labclip_worker.py scripts/smoke_cleanup_markers.py argo/scripts/
git mv scripts/apply_kubeflow.py scripts/check_kubeflow.py scripts/prepare_kubeflow_overlay.py scripts/render_kubeflow.py kubeflow/scripts/
git mv tests/test_terraform_inputs.py tests/test_smoke_cleanup_markers.py tests/test_smoke_labclip_runtime.py argo/tests/
git mv tests/test_kubeflow_access.py tests/test_kubeflow_apply.py tests/test_kubeflow_render.py kubeflow/tests/
rm -rf scripts/__pycache__ && rmdir scripts
ls tests
(cd kubeflow && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest tests.test_script_paths 2>&1 | tail -6)
```

Expected: `scripts/` is gone; `ls tests` shows only the `test_ansible_*.py` files (plus any untracked `__pycache__`); the new test FAILs (paths still point at the old layout).

- [ ] **Step 3: Fix the Kubeflow script path constants**

In `kubeflow/scripts/apply_kubeflow.py`:
- `GENERATED_ROOT = REPOSITORY_ROOT / "kubeflow/generated"` becomes `GENERATED_ROOT = REPOSITORY_ROOT / "generated"`.

In `kubeflow/scripts/check_kubeflow.py`:
- `GENERATED_ROOT = REPOSITORY_ROOT / "kubeflow/generated"` becomes `GENERATED_ROOT = REPOSITORY_ROOT / "generated"`.

In `kubeflow/scripts/render_kubeflow.py`:
- `default=REPOSITORY_ROOT / "kubeflow/generated/rendered.yaml",` becomes `default=REPOSITORY_ROOT / "generated/rendered.yaml",`.

In `kubeflow/scripts/prepare_kubeflow_overlay.py`:
- `OVERLAY_ROOT = Path(__file__).resolve().parents[1] / "kubeflow/overlays/labclip"` becomes `OVERLAY_ROOT = Path(__file__).resolve().parents[1] / "overlays/labclip"`.
- `default=_repository_root() / "kubeflow/generated/identity.json",` becomes `default=_repository_root() / "generated/identity.json",`.

`REPOSITORY_ROOT = Path(__file__).resolve().parents[1]` and `TERRAFORM_ROOT = REPOSITORY_ROOT / "terraform"` stay as they are; `parents[1]` is now `kubeflow/`.

- [ ] **Step 4: Remove `platform_mode` from the Kubeflow scripts**

In `kubeflow/scripts/apply_kubeflow.py`:

1. In `TERRAFORM_CONSOLE_EXPRESSION`, delete the line `  platform_mode = var.platform_mode,`.
2. In `TerraformOwnerInventory`, delete the field line `    platform_mode: str`.
3. In `terraform_owner_inventory`, delete the line `        platform_mode = payload["platform_mode"]`, delete the two lines
```
        if platform_mode not in {"argo", "kubeflow"}:
            raise TypeError
```
and delete the line `        platform_mode=platform_mode,` from the returned `TerraformOwnerInventory(...)`.
4. In `apply_distribution`, delete the guard
```
    if owner_inventory.platform_mode != "kubeflow":
        raise KubeflowApplyError(
            "Kubeflow apply requires Terraform platform_mode=kubeflow; apply is blocked."
        )
```

In `kubeflow/scripts/check_kubeflow.py`:

1. Delete the function `_require_kubeflow_mode` entirely.
2. Delete every call `_require_kubeflow_mode(owner_inventory)` (one in `check_distribution`, one in `check_readiness`).

Verify: `grep -rn platform_mode kubeflow/scripts || echo clean` prints `clean`.

- [ ] **Step 5: Fix the input-preparation script and bootstrap script in both stacks**

In `argo/scripts/prepare_terraform_inputs.py` and `kubeflow/scripts/prepare_terraform_inputs.py`, replace

```python
    result["kubeconfig_path"] = str(ROOT / "terraform" / "generated" / "kubeconfig")
```

with

```python
    result["kubeconfig_path"] = str(ROOT.parent / "ansible" / "generated" / "kubeconfig")
```

(`ROOT = Path(__file__).resolve().parents[1]` stays; it is now the stack directory, and `DEFAULT_OUTPUT = ROOT / "terraform" / "terraform.generated.auto.tfvars.json"` stays correct.)

In `argo/scripts/bootstrap_minio.sh` and `kubeflow/scripts/bootstrap_minio.sh`, replace

```bash
  KUBECONFIG="${repo_root}/terraform/generated/kubeconfig"
```

with

```bash
  KUBECONFIG="${repo_root}/../ansible/generated/kubeconfig"
```

- [ ] **Step 6: Fix the moved tests**

`argo/tests/test_terraform_inputs.py` and `kubeflow/tests/test_terraform_inputs.py`: replace

```python
                str(Path(__file__).resolve().parents[1] / "terraform" / "generated" / "kubeconfig"),
```

with

```python
                str(Path(__file__).resolve().parents[2] / "ansible" / "generated" / "kubeconfig"),
```

`kubeflow/tests/test_kubeflow_access.py`:
- `INGRESS_TEMPLATE = REPOSITORY_ROOT / "kubeflow/overlays/labclip/kubeflow-tailnet-ingress.yaml"` becomes `REPOSITORY_ROOT / "overlays/labclip/kubeflow-tailnet-ingress.yaml"`.
- `TERRAFORM_TAILSCALE = REPOSITORY_ROOT / "terraform/tailscale.tf"` stays.
- `KUBEFLOW_README = REPOSITORY_ROOT / "kubeflow/README.md"` becomes `REPOSITORY_ROOT / "README.md"`.

`kubeflow/tests/test_kubeflow_render.py`:
- `OVERLAY_ROOT = REPOSITORY_ROOT / "kubeflow/overlays/labclip"` becomes `REPOSITORY_ROOT / "overlays/labclip"`.
- `PREPARE_SCRIPT = REPOSITORY_ROOT / "scripts/prepare_kubeflow_overlay.py"` stays.

`kubeflow/tests/test_kubeflow_apply.py` (`REPOSITORY_ROOT / "terraform"` and `SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts"` stay correct):
1. Change `def default_terraform_owners(platform_mode="kubeflow"):` to `def default_terraform_owners():` and delete the line `        platform_mode=platform_mode,` inside it.
2. Delete the whole methods `test_apply_requires_kubeflow_terraform_mode` and `test_drift_and_readiness_require_kubeflow_terraform_mode` (the only users of `default_terraform_owners("argo")`).
3. In `test_inventory_reads_effective_names_without_emitting_secrets`: delete the `"platform_mode": "kubeflow",` entry from `configured`, delete the `self.assertIn("var.platform_mode", expression)` line, and delete `self.assertEqual("kubeflow", owners.platform_mode)`.
4. In `test_console_expression_is_submitted_as_one_line`: delete the `"platform_mode": "argo",` entry from `configured`, delete `self.assertIn("platform_mode = var.platform_mode", expression)`, and change the expected string to
```python
            "jsonencode({ argo_namespace = var.argo_namespace, labclip_run_namespace = var.labclip_run_namespace, enable_tailscale = var.enable_tailscale, nodes = { for node_name, node in var.nodes : node_name => { minio_role = node.minio_role, cache_claim = node.cache_claim } } })\n",
```
5. In `test_guard_uses_configured_argo_and_nvidia_namespaces` and any other `SimpleNamespace(...)` that sets `platform_mode="kubeflow",`, delete that line.
6. Verify: `grep -n platform_mode kubeflow/tests/test_kubeflow_apply.py || echo clean` prints `clean`.

- [ ] **Step 7: Run both stacks' tests**

```bash
(cd argo && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -4)
(cd kubeflow && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -4)
```

Expected: both `OK` with zero errors or failures. Do not try to match the baseline count of 82; tests were deleted, replaced, and added.

- [ ] **Step 8: Commit**

```bash
git add -A argo kubeflow scripts tests
git commit -m "refactor: move scripts and tests into their owning stack

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Shared kubeconfig location, ignore rules, Ansible

**Files:**
- Modify: `ansible/group_vars/all.yml:6`, `tests/test_ansible_teardown.py:18`, `argo/terraform/variables.tf`, `kubeflow/terraform/variables.tf`, `.gitignore`
- Create: `tests/test_kubeconfig_contract.py`

**Interfaces:**
- Consumes: `kubeconfig_path` variables in both roots; `k3s_kubeconfig_fetch_dest` in `ansible/group_vars/all.yml`.
- Produces: one kubeconfig file, `ansible/generated/kubeconfig`, referenced by Ansible and by both stacks.

- [ ] **Step 1: Write the failing contract test**

Create `tests/test_kubeconfig_contract.py`:

```python
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (REPO_ROOT / "ansible" / "generated" / "kubeconfig").resolve()


def terraform_default(stack: str) -> Path:
    source = (REPO_ROOT / stack / "terraform" / "variables.tf").read_text(encoding="utf-8")
    block = re.search(r'variable "kubeconfig_path" \{[^}]*\}', source).group(0)
    default = re.search(r'default\s*=\s*"([^"]+)"', block).group(1)
    return (REPO_ROOT / stack / "terraform" / default).resolve()


class KubeconfigContractTests(unittest.TestCase):
    def test_ansible_writes_the_shared_kubeconfig(self) -> None:
        group_vars = (REPO_ROOT / "ansible" / "group_vars" / "all.yml").read_text(encoding="utf-8")
        destination = re.search(r'k3s_kubeconfig_fetch_dest:\s*"([^"]+)"', group_vars).group(1)
        resolved = (REPO_ROOT / "ansible" / "playbooks" / destination.replace("{{ playbook_dir }}/", "")).resolve()
        self.assertEqual(EXPECTED, resolved)

    def test_each_stack_reads_the_shared_kubeconfig(self) -> None:
        for stack in ("argo", "kubeflow"):
            with self.subTest(stack=stack):
                self.assertEqual(EXPECTED, terraform_default(stack))

    def test_each_stack_example_inputs_use_the_shared_kubeconfig(self) -> None:
        for stack in ("argo", "kubeflow"):
            with self.subTest(stack=stack):
                example = (REPO_ROOT / stack / "terraform" / "terraform.tfvars.example").read_text(encoding="utf-8")
                value = re.search(r'kubeconfig_path\s*=\s*"([^"]+)"', example).group(1)
                self.assertEqual(EXPECTED, (REPO_ROOT / stack / "terraform" / value).resolve())

    def test_each_stack_ignores_its_own_private_files(self) -> None:
        ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for stack in ("argo", "kubeflow"):
            for pattern in (
                f"{stack}/terraform/.terraform/",
                f"{stack}/terraform/terraform.tfstate",
                f"{stack}/terraform/terraform.tfvars",
                f"{stack}/terraform/*.auto.tfvars.json",
            ):
                with self.subTest(pattern=pattern):
                    self.assertIn(pattern, ignore)
        self.assertIn("ansible/generated/", ignore)
        self.assertNotIn("terraform/generated/", ignore)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Update the existing Ansible teardown test and run both to see failures**

In `tests/test_ansible_teardown.py` replace

```python
        kubeconfig = REPO_ROOT / "terraform/generated/kubeconfig"
```

with

```python
        kubeconfig = REPO_ROOT / "ansible/generated/kubeconfig"
```

```bash
uv run --no-project --with pyyaml python -m unittest tests.test_kubeconfig_contract tests.test_ansible_teardown 2>&1 | tail -8
```

Expected: FAIL on the four contract tests and on the teardown test.

- [ ] **Step 3: Apply the changes**

`ansible/group_vars/all.yml`, replace

```yaml
k3s_kubeconfig_fetch_dest: "{{ playbook_dir }}/../../terraform/generated/kubeconfig"
```

with

```yaml
k3s_kubeconfig_fetch_dest: "{{ playbook_dir }}/../generated/kubeconfig"
```

In `argo/terraform/variables.tf` and `kubeflow/terraform/variables.tf`, inside `variable "kubeconfig_path"`, replace

```
  default     = "./generated/kubeconfig"
```

with

```
  default     = "../../ansible/generated/kubeconfig"
```

In `argo/terraform/terraform.tfvars.example` and `kubeflow/terraform/terraform.tfvars.example`, replace

```
kubeconfig_path = "./generated/kubeconfig"
```

with

```
kubeconfig_path = "../../ansible/generated/kubeconfig"
```

Replace the entire `.gitignore` with:

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

kubeflow/terraform/.terraform/
kubeflow/terraform/terraform.tfstate
kubeflow/terraform/terraform.tfstate.backup
kubeflow/terraform/terraform.tfstate.*.backup
kubeflow/terraform/.terraform.tfstate.lock.info
kubeflow/terraform/terraform.tfvars
kubeflow/terraform/*.auto.tfvars.json
kubeflow/generated/
kubeflow/overlays/labclip/generated/

__pycache__/
*.py[cod]

.claude/
.worktrees
```

- [ ] **Step 4: Run the tests**

```bash
uv run --no-project --with pyyaml python -m unittest discover -s tests 2>&1 | tail -5
terraform -chdir=argo/terraform validate && terraform -chdir=kubeflow/terraform validate
```

Expected: root-level tests `OK`; both validations succeed. The teardown test needs `ansible-playbook`; if it errors because the tool is missing, report that and do not skip silently.

- [ ] **Step 5: Commit**

```bash
git add .gitignore ansible tests argo kubeflow
git commit -m "refactor: share one kubeconfig through ansible and split ignore rules per stack

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Runbooks and README paths

**Files:**
- Modify: `README.md`, `kubeflow/README.md`, `kubeflow/terraform/README.md`, `ansible/README.md`
- Create: `argo/README.md`
- Modify: `argo/terraform/README.md`

**Interfaces:**
- Consumes: the layout and commands from Tasks 1-5.

- [ ] **Step 1: Write the root README**

Replace `README.md` with:

````markdown
# 3T-CLIP Pipeline Infrastructure

Infrastructure-as-code for the LabCLIP k3s cluster (`vis-lab` control-plane+worker, `ubuntu` worker). Two independent platform stacks share one host layer. Deploy only one stack on the cluster at a time.

| Directory | Owns |
|---|---|
| [`ansible/`](ansible/) | Node and OS layer: k3s, NVIDIA container runtime, local storage directories. Writes the shared kubeconfig to `ansible/generated/kubeconfig`. |
| [`argo/`](argo/) | Argo Workflows platform: its own Terraform root, scripts, tests, and runbook. |
| [`kubeflow/`](kubeflow/) | Pinned Kubeflow Community Distribution 26.03.1: its own Terraform root, Kustomize overlay, scripts, tests, and runbook. |

The stacks do not read each other's files or state. Files such as `minio.tf`, `nvidia_device_plugin.tf`, `prepare_terraform_inputs.py`, and `bootstrap_minio.sh` are intentionally duplicated in both stacks. Each stack labels the `argo` namespace with `labclip.io/iac-stack`, and a plan fails when the other stack owns it. To switch platforms, destroy the deployed stack, then apply the other.

Do not add Kubernetes manifests to `ansible/` or host-provisioning tasks to a stack's Terraform root; see [`ansible/README.md`](ansible/README.md) for the host-layer contract.
````

- [ ] **Step 2: Write `argo/README.md`**

Create `argo/README.md`:

````markdown
# Argo Workflows stack

Independent Terraform root for standalone Argo Workflows, the LabCLIP WorkflowTemplate, MinIO, the NVIDIA device plugin, retained cache volumes, runner RBAC, and the optional Tailscale operator. It shares nothing with `kubeflow/` except the host layer under `ansible/`.

Run the commands below from the repository root.

```bash
python3 argo/scripts/prepare_terraform_inputs.py
terraform -chdir=argo/terraform init
terraform -chdir=argo/terraform plan
terraform -chdir=argo/terraform apply
```

`argo/terraform/terraform.tfvars` holds the MinIO root credentials and stays untracked. `prepare_terraform_inputs.py` writes `argo/terraform/terraform.generated.auto.tfvars.json` and points `kubeconfig_path` at `ansible/generated/kubeconfig`.

When this repository is vendored as a submodule of `lab_clip`, set `labclip_workflow_template_path` to the checked-in `pipeline/k8s/generated/labclip-train.yaml` of that checkout.

Tests run from the `argo/` directory so that its own `scripts` package is imported:

```bash
uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests
```

Stack state lives in `terraform/` and is untracked. Details of the resources and the MinIO bootstrap are in [`terraform/README.md`](terraform/README.md). The `argo` namespace carries `labclip.io/iac-stack=argo`; if the Kubeflow stack is deployed, destroy it first.
````

- [ ] **Step 3: Update the Kubeflow and Ansible runbooks with exact substitutions**

```bash
sed -i \
  -e 's#terraform -chdir=terraform#terraform -chdir=kubeflow/terraform#g' \
  -e 's#python3 scripts/#python3 kubeflow/scripts/#g' \
  -e 's#`terraform/terraform.tfvars`#`kubeflow/terraform/terraform.tfvars`#g' \
  kubeflow/README.md
sed -i 's#terraform/generated/kubeconfig#ansible/generated/kubeconfig#g' ansible/README.md
grep -n 'platform_mode' kubeflow/README.md kubeflow/terraform/README.md argo/terraform/README.md || echo "none"
grep -n 'terraform/generated\|\bscripts/' kubeflow/README.md ansible/README.md | head -20
```

Then edit by hand using the grep output:
- In `kubeflow/README.md`, delete every sentence that tells the operator to set `platform_mode = "kubeflow"` and keep the rest of the sentence (the other settings stay).
- In `kubeflow/terraform/README.md`, delete the `platform_mode` paragraphs and the `argo` rows of the cluster-contract table (Argo Workflows chart and app version rows); state at the top that this is the Kubeflow stack's Terraform root.
- Ensure the Ansible kubeconfig statement in `ansible/README.md` names `ansible/generated/kubeconfig`.

Gate: `grep -rn 'platform_mode' README.md argo kubeflow ansible --include='*.md'` prints nothing, and `grep -rn 'terraform/generated/kubeconfig' . --include='*.md' --include='*.yml' --include='*.py' --include='*.sh' --include='*.tf' --exclude-dir=docs --exclude-dir=.git` prints nothing.

- [ ] **Step 4: Run all three test suites and commit**

```bash
uv run --no-project --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3
(cd argo && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3)
(cd kubeflow && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3)
git add -A README.md argo kubeflow ansible
git commit -m "docs: document the independent argo and kubeflow stacks

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Expected: three `OK` results. `test_kubeflow_access` asserts text in `kubeflow/README.md`; if it fails after the edits, restore the sentence it checks rather than weakening the test.

---

### Task 7: Final verification and pull request

**Files:**
- No new files.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Full verification**

```bash
git status --short | head
uv run --no-project --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3
(cd argo && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3)
(cd kubeflow && uv run --no-project --with bcrypt==4.2.1 --with pyyaml python -m unittest discover -s tests 2>&1 | tail -3)
for stack in argo kubeflow; do terraform -chdir=$stack/terraform fmt -check && terraform -chdir=$stack/terraform validate; done
git diff --stat -M develop...HEAD | tail -5
ls terraform scripts 2>&1 | head -2
```

Expected: clean tree, three `OK`, two successful validations, moves reported as renames, and `ls` reporting that `terraform` and `scripts` no longer exist.

- [ ] **Step 2: Read-only plan of the Argo root against the empty cluster**

Use the operator's existing private inputs by path, without copying them.

```bash
ls /mnt/data/lab_clip/pipeline/k8s/generated/labclip-train.yaml
terraform -chdir=argo/terraform plan -input=false -lock=false \
  -var-file=/mnt/data/3t-clip-pipeline/terraform/terraform.tfvars \
  -var-file=/mnt/data/3t-clip-pipeline/terraform/terraform.generated.auto.tfvars.json \
  -var kubeconfig_path=$HOME/.kube/config \
  -var labclip_workflow_template_path=/mnt/data/lab_clip/pipeline/k8s/generated/labclip-train.yaml 2>&1 | tail -15
```

Expected: `Plan: N to add, 0 to change, 0 to destroy.` with only creations, and no error from `ownership_guard`. If the template file does not exist, report it and use a placeholder path only for this plan. Do not run `apply`.

- [ ] **Step 3 (needs the user's approval first because it writes to the cluster): prove the guard fails**

Ask the user before running. If approved:

```bash
kubectl create namespace argo
kubectl label namespace argo labclip.io/iac-stack=kubeflow
terraform -chdir=argo/terraform plan -input=false -lock=false \
  -var-file=/mnt/data/3t-clip-pipeline/terraform/terraform.tfvars \
  -var-file=/mnt/data/3t-clip-pipeline/terraform/terraform.generated.auto.tfvars.json \
  -var kubeconfig_path=$HOME/.kube/config \
  -var labclip_workflow_template_path=/mnt/data/lab_clip/pipeline/k8s/generated/labclip-train.yaml 2>&1 | grep -A3 'belongs to another'
kubectl delete namespace argo
```

Expected: the plan fails with the message `The argo namespace belongs to another LabCLIP IaC stack. Destroy that stack before applying this one.`, and the cluster is left empty again.

- [ ] **Step 4: Push and open the pull request**

```bash
git push -u origin claude/split-argo-kubeflow-iac
gh pr create --base develop --title "refactor: argo와 kubeflow IaC를 독립 스택으로 분리" --body "$(cat <<'EOF'
## 변경 내용
- `terraform/`의 `platform_mode` 스위치를 없애고 `argo/terraform`, `kubeflow/terraform` 두 개의 독립 Terraform 루트로 분리했습니다.
- Kubeflow 전용 overlay와 스크립트는 `kubeflow/`로, Argo 전용 스모크 스크립트는 `argo/`로 옮겼습니다. 이동은 `git mv`라 이력이 유지됩니다.
- 한 스택이 `argo` 네임스페이스를 소유하면 다른 스택의 plan이 실패하도록 소유권 가드를 추가했습니다.
- 두 스택이 공유하는 것은 `ansible/generated/kubeconfig` 하나뿐입니다.

## 변경 이유
- 하나의 루트와 state를 공유하면 모드 전환이 서로에게 영향을 줍니다. 한 번에 하나만 운영하는 조건에서 스택별 독립성을 확보하기 위함입니다.

## 영향
- `terraform -chdir=terraform ...` 명령은 `terraform -chdir=argo/terraform ...` 또는 `kubeflow/terraform`으로 바뀝니다.
- Kubeflow 스크립트에서 `platform_mode` 검사 두 곳을 제거했습니다. 디렉터리 분리가 같은 역할을 합니다.
- 기존 state와 비공개 입력은 추적되지 않으며 건드리지 않았습니다. 사용할 스택에서 입력을 새로 만들어야 합니다.
- `lab_clip`의 `docs/agents/iac-submodule-sync.md` 경로 1곳은 서브모듈 갱신 후 별도 PR로 수정합니다.

## 검증
- 루트, `argo/`, `kubeflow/` 각각 `unittest` 통과
- 두 루트 모두 `terraform fmt -check`, `terraform validate` 통과
- Argo 루트 `terraform plan`(읽기 전용)이 생성만 포함하고 변경과 삭제가 없음을 확인
- `terraform apply`는 실행하지 않았습니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Run `gh` outside the sandbox. Report the PR URL.

---

### Task 8: Follow-up pull request in `lab_clip` (after the submodule pointer updates)

**Files:**
- Modify: `/mnt/data/lab_clip/docs/agents/iac-submodule-sync.md:60`

**Interfaces:**
- Consumes: the merged Task 7 pull request and the submodule update opened by the existing `notify-labclip` workflow.

- [ ] **Step 1: Create a branch in a `lab_clip` worktree**

```bash
git -C /mnt/data/lab_clip fetch --prune
git -C /mnt/data/lab_clip worktree add .worktrees/claude-iac-template-path -b claude/iac-template-path origin/develop
```

- [ ] **Step 2: Fix the path**

In `docs/agents/iac-submodule-sync.md` replace

```
test -f infra/3t-clip-pipeline/terraform/../../../pipeline/k8s/generated/labclip-train.yaml
```

with

```
test -f infra/3t-clip-pipeline/argo/terraform/../../../../pipeline/k8s/generated/labclip-train.yaml
```

- [ ] **Step 3: Verify and open the pull request**

```bash
cd .worktrees/claude-iac-template-path
test -f infra/3t-clip-pipeline/argo/terraform/../../../../pipeline/k8s/generated/labclip-train.yaml && echo ok
git add docs/agents/iac-submodule-sync.md
git commit -m "docs: update IaC template path for the argo stack

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push -u origin claude/iac-template-path
gh pr create --base develop --title "docs: IaC 서브모듈 템플릿 경로를 argo 스택 기준으로 수정" --body "$(cat <<'EOF'
## 변경 내용
- `docs/agents/iac-submodule-sync.md`의 템플릿 경로 확인 명령을 새 `argo/terraform` 구조에 맞게 수정했습니다.

## 변경 이유
- IaC 저장소가 `argo/`와 `kubeflow/` 독립 스택으로 분리되어 Terraform 루트 경로가 한 단계 깊어졌습니다.

## 영향
- 문서 한 줄만 변경되며 코드 동작에는 영향이 없습니다.

## 검증
- 서브모듈이 갱신된 상태에서 수정된 `test -f` 명령이 `ok`를 출력함을 확인했습니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: `ok` printed, and the pull request URL reported. Skip this task until the submodule pointer in `lab_clip` points at a commit that contains `argo/terraform`.
