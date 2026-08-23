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
