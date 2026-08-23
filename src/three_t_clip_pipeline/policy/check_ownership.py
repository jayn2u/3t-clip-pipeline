"""CLI validator for the resource ownership ledger."""

import sys
from pathlib import Path
from typing import Final

from three_t_clip_pipeline.policy.ownership import OwnershipPolicyError, load_ownership

EXPECTED_ARGUMENT_COUNT: Final = 2


def main() -> int:
    """Validate the ledger named on the command line."""
    if len(sys.argv) != EXPECTED_ARGUMENT_COUNT:
        _ = sys.stderr.write(
            "usage: python -m three_t_clip_pipeline.policy.check_ownership LEDGER\n"
        )
        return 2
    try:
        _ = load_ownership(Path(sys.argv[1]))
    except OwnershipPolicyError as error:
        _ = sys.stderr.write(f"{error}\n")
        return 1
    _ = sys.stdout.write("OWNERSHIP_OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
