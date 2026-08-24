# /// script
# requires-python = ">=3.12,<3.13"
# ///
# How to run:
# OUTPUT_ROOT=/tmp/python-job-output uv run examples/consumer/python-job/job.py \
#   --config examples/consumer/python-job/app-config.json

"""Standalone consumer process that owns and interprets its application config."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final, TypedDict

from pydantic import TypeAdapter

_CLI_ARGUMENT_COUNT: Final = 3


class AppConfig(TypedDict):
    """Consumer-owned application configuration."""

    message: str
    resultSchema: str


def main() -> int:
    """Read consumer config and write the platform-declared output files."""
    if len(sys.argv) != _CLI_ARGUMENT_COUNT or sys.argv[1] != "--config":
        _ = sys.stderr.write("usage: job.py --config PATH\n")
        return 2
    config = TypeAdapter(AppConfig).validate_json(Path(sys.argv[2]).read_bytes())
    output_root = Path(os.environ["OUTPUT_ROOT"])
    output_root.mkdir(parents=True, exist_ok=True)
    result = {"message": config["message"], "schema": config["resultSchema"]}
    provenance = {"consumer": "python-job", "schema": "consumer-provenance/v1"}
    _ = (output_root / "result.json").write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _ = (output_root / "provenance.json").write_text(
        json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
