# Secret lifecycle

The `platform_secrets` role is the sole field owner for the five runtime Secrets in
namespace `three-t-pipeline`. Supply `platform_context` and
`platform_environment_class` explicitly; the role never reads the current context.

## Preparing vaulted input

Create a local YAML document that validates against
`schemas/bootstrap-secrets.schema.json`, encrypt the complete file with Ansible Vault,
then remove the plaintext. Only Vault ciphertext may be committed. Do not pass Vault
passwords or Secret values through command arguments or environment variables. Prefer
an interactive password prompt or an operator-controlled descriptor.

The role accepts decrypted values only in Ansible memory. Every value-bearing task has
`no_log: true` and `diff: false`; it sends an in-memory `Secret` definition directly to
`kubernetes.core.k8s`. It does not invoke a shell, write a manifest, or read decoded
Secret data. Evidence is limited to UID, resourceVersion, Secret name, namespace, key
names, and SHA-256 of sorted key names.

## Authorization gates

- `production` always returns `production_mutation_forbidden` before any Kubernetes
  mutation.
- `development` performs strict API validation in server dry-run/check mode and never
  persists a Secret.
- `ephemeral` is the only environment class permitted to create or idempotently update
  Secrets during automated QA.
- `platform_k3s_secrets_encryption_enabled` must come from a fresh, read-only
  `k3s secrets-encrypt status` inspection and must be true before values are evaluated
  or sent to Kubernetes. A false or stale state returns
  `k3s_secret_encryption_required` with zero mutations.

## Future rotation authorization

Rotation is not implemented by this role. It requires a separately approved operation
that records old/new workload readiness, confirms dependent workload restarts, verifies
the new credentials, and retains an explicit rollback checkpoint. This repository has
no default deletion or rotation task.

After a failed run, remove any operator-created plaintext using the host's secure
deletion facility, unset any Vault-related shell variables, and confirm no credential
file remains. The role itself creates no temporary plaintext file.
