# /// script
# requires-python = ">=3.12,<3.13"
# ///
# How to run: OUTPUT_ROOT=/tmp/hello-output uv run examples/consumer/hello/run.py

"""Deterministic consumer process used by the portable conformance slice."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final

_RESULT: Final = {"message": "hello portable pipeline", "schema": "hello-result/v1"}
_PROVENANCE: Final = {"consumer": "hello", "schema": "consumer-provenance/v1"}


def main() -> int:
    """Write the required result and provenance files, then return the requested status."""
    output_root = Path(os.environ["OUTPUT_ROOT"])
    output_root.mkdir(parents=True, exist_ok=True)
    _ = (output_root / "result.json").write_text(
        json.dumps(_RESULT, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    _ = (output_root / "provenance.json").write_text(
        json.dumps(_PROVENANCE, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return int(os.environ.get("HELLO_EXIT_CODE", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
