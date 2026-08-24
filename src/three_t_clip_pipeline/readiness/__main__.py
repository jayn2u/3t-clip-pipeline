"""Generate a cutover report from independently captured collector evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from three_t_clip_pipeline.readiness.evaluator import evaluate_readiness, rejected_bundle_report
from three_t_clip_pipeline.readiness.models import JsonDocument, ReadinessBundle

EXPECTED_ARGUMENT_COUNT: Final = 2


def main() -> int:
    """Read one snapshot path and emit a deterministic report to standard output."""
    if len(sys.argv) != EXPECTED_ARGUMENT_COUNT:
        _ = sys.stderr.write("usage: python -m three_t_clip_pipeline.readiness BUNDLE.json\n")
        return 2
    bundle_path = Path(sys.argv[1])
    try:
        raw = bundle_path.read_text(encoding="utf-8")
        parsed = JsonDocument.model_validate_json(raw).root
    except (OSError, ValidationError) as error:
        _ = sys.stderr.write(f"CUTOVER_READINESS_INVALID {type(error).__name__}\n")
        return 2
    try:
        bundle = ReadinessBundle.model_validate(parsed)
    except ValidationError:
        report = rejected_bundle_report("bundle_valid")
    else:
        report = evaluate_readiness(bundle, bundle_path)
    output = json.dumps(report.model_dump(by_alias=True, mode="json"), separators=(",", ":"))
    _ = sys.stdout.write(f"{output}\n")
    return 0 if report.ready else 1


raise SystemExit(main())
