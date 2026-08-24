"""Portable conformance marker registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _pytest.config import Config
    from _pytest.nodes import Item


def pytest_configure(config: Config) -> None:
    """Register markers used by the portable acceptance slice."""
    config.addinivalue_line("markers", "contract: portable platform contract conformance")


def pytest_collection_modifyitems(items: list[Item]) -> None:
    """Classify every portable test as contract coverage."""
    for item in items:
        item.add_marker(pytest.mark.contract)
