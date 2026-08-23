# Resource ownership ledger

`config/resource-ownership.yaml` is the machine-readable handoff boundary for every row in the platform plan's resource ownership matrix. Each entry has one scalar owner and is keyed by `(apiVersion, kind, namespace, name)`. Selector and multi-object matrix rows use platform ledger kinds such as `HostBootstrapSet` or `KustomizeResourceSet`; these are inventory identities, not Kubernetes APIs to apply.

The ledger is deliberately non-adopting. Existing objects remain with their prior owner until inventory, freeze, independent backup, server-side diff, and separate cutover authorization all exist. `check-only`, `documentation-only`, and `no-adoption` never permit mutation. `create-ephemeral-only` is limited to isolated QA. Per-run resources may only use the named submit or conditional-write mechanism.

`backupRequirement` records evidence required before handoff. A Kubernetes `Retain` reclaim policy is not a backup, and a `DirectoryOrCreate` host path is not mount proof. Live adoption is never the default. Rollback stays with the named prior or operational owner until an authorized handoff completes.

Validate the ledger with:

```console
uv run python -m three_t_clip_pipeline.policy.check_ownership config/resource-ownership.yaml
```

Success prints exactly `OWNERSHIP_OK`. Invalid schema or a repeated identity fails with a stable policy code such as `ownership_schema_invalid` or `ownership_conflict`.
