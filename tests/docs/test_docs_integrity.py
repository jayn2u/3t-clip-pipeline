from __future__ import annotations

import re
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[2]
LINK: Final[re.Pattern[str]] = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")


def _links(document: Path) -> list[str]:
    matches: list[re.Match[str]] = list(LINK.finditer(document.read_text(encoding="utf-8")))
    return [match.group(1) for match in matches]


def test_relative_markdown_links_resolve() -> None:
    # Given: every Task 15 Markdown entry point.
    documents = [
        ROOT / name for name in ("README.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md")
    ]
    documents.extend((ROOT / "docs").rglob("*.md"))

    # When: relative Markdown targets are resolved from their source document.
    missing = [
        f"{document.relative_to(ROOT)} -> {target}"
        for document in documents
        for target in _links(document)
        if "://" not in target and not (document.parent / target).resolve().exists()
    ]

    # Then: no local link is broken.
    assert missing == []


def test_release_document_pins_machine_consumed_markers() -> None:
    # Given: the release interface and its operator guide.
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    guide = (ROOT / "docs/operator/release-handoff.md").read_text(encoding="utf-8")

    # When: stable automation markers are compared.
    markers = (
        "LOCAL_COMPLETE_REMOTE_DEFERRED",
        "REMOTE_AUTHORITY_REQUIRED",
        "REMOTE_BASE_CONFLICT",
        "PR_READY",
    )

    # Then: both surfaces expose the same state-machine vocabulary.
    assert all(marker in makefile and marker in guide for marker in markers)


def test_ansible_sync_is_task_local_and_manifest_pinned() -> None:
    # Given: the Makefile integration used before the complete test suite.
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    # When: its Ansible cache and manifest boundary are inspected.
    requirements_hash = "70d34763e23d33b90d322bcb89f4ebfef1889b7bef5411725b622631b7360bc6"
    required = (
        "test: ansible-sync",
        f"ANSIBLE_REQUIREMENTS_SHA256 = {requirements_hash}",
        "$(CURDIR)/.cache/ansible/collections",
        "ansible/requirements.yml",
    )

    # Then: the complete gate uses only the pinned task-local collection cache.
    assert all(fragment in makefile for fragment in required)
    assert "$(HOME)/.ansible" not in makefile
