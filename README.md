# 3T-CLIP Pipeline Platform

This repository contains the standalone Python 3.12 platform package for rendering,
validating, and operating portable 3T-CLIP workloads. It intentionally excludes the
LabCLIP training stack and application-specific datasets.

## Local development

```bash
make sync
make quality
make test
uv run 3t-pipeline --help
```

The dependency graph is frozen in `uv.lock`; use `make lock-check` to verify it without
changing the lockfile.

## Operator path

Start with the [ordered operator journey](docs/operator/journey.md), then use the
[ownership handoff](docs/migration/ownership-handoff.md) for any migration decision. The journey
separates offline validation, read-only cluster inspection, disposable isolated proof, and every
state-changing authorization boundary.

Local release handoff runs the complete repository gates without contacting a remote:

```bash
make release-handoff DELIVERY_MODE=local
```

It completes with `LOCAL_COMPLETE_REMOTE_DEFERRED`. Remote delivery is a later, externally
authorized operation documented in [Release and pull-request handoff](docs/operator/release-handoff.md).
