#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = []
# ///
# ─── How to run ───
# .venv/bin/python scripts/publish-platform-artifact.py STAGED FINAL

"""Atomically publish one staged platform artifact on its destination filesystem."""

import os
import sys
from pathlib import Path
from typing import Final

ARGUMENT_COUNT: Final = 3
ARGUMENTS_INVALID: Final = "publication_arguments_invalid"
PARENT_MISMATCH: Final = "publication_parent_mismatch"


class _PublicationError(Exception):
    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _main() -> int:
    if len(sys.argv) != ARGUMENT_COUNT:
        raise _PublicationError(ARGUMENTS_INVALID)
    staged = Path(sys.argv[1])
    final = Path(sys.argv[2])
    if staged.parent.resolve() != final.parent.resolve():
        raise _PublicationError(PARENT_MISMATCH)
    with staged.open("rb") as handle:
        os.fsync(handle.fileno())
    _ = staged.replace(final)
    directory = os.open(final.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except (OSError, _PublicationError) as error:
        _ = sys.stderr.write(f"PLATFORM_PUBLISH_INVALID {error}\n")
        raise SystemExit(2) from error
