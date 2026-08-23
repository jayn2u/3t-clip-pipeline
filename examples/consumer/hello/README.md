# Hello portable consumer

This fixture owns its consumer code and writes two deterministic files to `OUTPUT_ROOT`:
`result.json` and `provenance.json`. The workload contract declares both paths as required.
The platform smoke invokes `run.py` as a separate process and gives it no platform package API.

Run it directly with:

```bash
OUTPUT_ROOT=/tmp/hello-output .venv/bin/python examples/consumer/hello/run.py
```
