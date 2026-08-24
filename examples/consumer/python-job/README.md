# Standalone Python job consumer

This example owns `app-config.json` and the code that interprets it. The platform sees only `workload.yaml`: an immutable bundle and image, argv command, external environment references, generic cache mappings and resources, lifecycle timeouts, and required output paths.

Run the consumer process directly:

```bash
OUTPUT_ROOT=/tmp/python-job-output \
  uv run examples/consumer/python-job/job.py \
  --config examples/consumer/python-job/app-config.json
```

Validate and render the platform contract:

```bash
uv run 3t-pipeline contract validate examples/consumer/python-job/workload.yaml
uv run 3t-pipeline plan examples/consumer/python-job/workload.yaml
```

Changing application behavior belongs in `app-config.json` or `job.py`. The workload changes only when an actual platform contract field changes.
