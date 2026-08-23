"""Repository scanner that reports secret classes without echoing matched bytes."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_VAULT_HEADER: Final = b"$ANSIBLE_VAULT;1.2;AES256;"
_HEX_BYTES: Final = frozenset(b"0123456789abcdef\r\n")
_SECRET_ASSIGNMENT_PREFIX: Final = (
    rb"(?im)^\s*(?:password|rootPassword|secretKey|accessKey|token|clientSecret)"
)
_SECRET_ASSIGNMENT_VALUE: Final = (
    rb"\s*:\s*(?![\"']?\{\{)(?!<redacted>)(?!null\s*$)[\"']?[^\s#][^#\r\n]*"
)
_RULES: Final = (
    (
        "private_key",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("aws_access_key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "literal_secret_assignment",
        re.compile(_SECRET_ASSIGNMENT_PREFIX + _SECRET_ASSIGNMENT_VALUE),
    ),
    ("literal_secret_argv", re.compile(rb"--from-literal(?:=|\s)")),
)


@dataclass(frozen=True, slots=True)
class Finding:
    """A redacted finding identified only by file position and rule."""

    file_index: int
    rule: str


def _scan(content: bytes, file_index: int) -> tuple[Finding, ...]:
    if content.startswith(b"$ANSIBLE_VAULT;"):
        ciphertext = content.partition(b"\n")[2]
        if content.startswith(_VAULT_HEADER) and ciphertext and set(ciphertext) <= _HEX_BYTES:
            return ()
        return (Finding(file_index=file_index, rule="vault_ciphertext_invalid"),)
    return tuple(
        Finding(file_index=file_index, rule=rule)
        for rule, pattern in _RULES
        if pattern.search(content) is not None
    )


def scan_paths(paths: tuple[Path, ...]) -> tuple[Finding, ...]:
    """Scan paths without retaining or returning matched bytes."""
    findings: list[Finding] = []
    for file_index, path in enumerate(paths):
        try:
            content = path.read_bytes()
        except OSError:
            findings.append(Finding(file_index=file_index, rule="unreadable"))
            continue
        findings.extend(_scan(content, file_index))
    return tuple(findings)


def main() -> int:
    """Run the redacted command-line scanner."""
    findings = scan_paths(tuple(Path(argument) for argument in sys.argv[1:]))
    for finding in findings:
        _ = sys.stderr.write(f"SECRET_SCAN_FAILED file={finding.file_index} rule={finding.rule}\n")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
