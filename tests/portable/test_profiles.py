"""Generic profile acquisition invariants."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from three_t_clip_pipeline.services.profile import (
    AcquisitionRequest,
    AcquisitionResult,
    ProfileState,
    acquire_profile,
)


@dataclass
class Clock:
    """Injected deterministic monotonic clock."""

    now: float = 0
    events: list[str] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def wait(self, seconds: float) -> None:
        self.events.append(f"wait:{seconds}")
        self.now += seconds


@dataclass
class Client:
    """Lease client exposing ordering and timeout observables."""

    ready_after: int
    inspections: int = 0
    events: list[str] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)

    def acquire(self, profile: str, timeout_seconds: float) -> bool:
        self.events.append(f"acquire:{profile}")
        self.timeouts.append(timeout_seconds)
        return True

    def inspect(self, profile: str, timeout_seconds: float) -> ProfileState:
        self.events.append(f"inspect:{profile}")
        self.timeouts.append(timeout_seconds)
        self.inspections += 1
        return ProfileState.READY if self.inspections >= self.ready_after else ProfileState.PENDING

    def release(self, profile: str, timeout_seconds: float) -> None:
        self.events.append(f"release:{profile}")
        self.timeouts.append(timeout_seconds)


@pytest.mark.parametrize("profile", ["gpu-small", "gpu-batch", "cpu-cache"])
def test_profile_names_are_generic_and_lease_is_released_before_wait(profile: str) -> None:
    # Given
    clock = Clock()
    client = Client(ready_after=2)

    # When
    outcome = acquire_profile(
        AcquisitionRequest(profile=profile, deadline_seconds=30),
        client,
        clock,
        lambda: False,
    )

    # Then
    assert outcome == AcquisitionResult.ACQUIRED
    assert client.events[:4] == [
        f"acquire:{profile}",
        f"inspect:{profile}",
        f"release:{profile}",
        f"acquire:{profile}",
    ]
    assert max(client.timeouts) <= 15
    assert clock.events == ["wait:5.0"]


def test_cancellation_returns_stable_code_after_lease_release() -> None:
    # Given
    clock = Clock()
    client = Client(ready_after=99)
    checks = iter((False, True))

    # When
    outcome = acquire_profile(
        AcquisitionRequest(profile="accelerator-a", deadline_seconds=20),
        client,
        clock,
        lambda: next(checks),
    )

    # Then
    assert outcome == AcquisitionResult.CANCELLED
    assert client.events[-1] == "release:accelerator-a"
