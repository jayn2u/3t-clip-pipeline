"""Bounded, cancellable cache-profile acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

_MAX_DEADLINE_SECONDS: Final = 7200
_MAX_API_TIMEOUT_SECONDS: Final = 15.0


@unique
class ProfileState(StrEnum):
    """Observed cache-profile readiness."""

    PENDING = "pending"
    READY = "ready"


@unique
class AcquisitionResult(StrEnum):
    """Stable profile acquisition outcomes."""

    ACQUIRED = "acquired"
    TIMEOUT = "profile_timeout"
    CANCELLED = "profile_cancelled"
    API_ERROR = "profile_api_error"


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    """Profile name and bounded monotonic budget."""

    profile: str
    deadline_seconds: float = 900

    def __post_init__(self) -> None:
        """Reject non-positive and contract-exceeding acquisition budgets."""
        if not 0 < self.deadline_seconds <= _MAX_DEADLINE_SECONDS:
            message = "profile deadline must be in (0, 7200] seconds"
            raise ValueError(message)


class ProfileApiError(Exception):
    """Expected infrastructure API failure."""


class ProfileClient(Protocol):
    """Lease and profile API operations, each bounded by a caller timeout."""

    def acquire(self, profile: str, timeout_seconds: float) -> bool:
        """Acquire the profile Lease when available."""
        ...

    def inspect(self, profile: str, timeout_seconds: float) -> ProfileState:
        """Inspect profile readiness."""
        ...

    def release(self, profile: str, timeout_seconds: float) -> None:
        """Release a previously acquired Lease."""
        ...


class MonotonicClock(Protocol):
    """Injected monotonic clock and cancellable wait boundary."""

    def monotonic(self) -> float:
        """Read monotonic seconds."""
        ...

    def wait(self, seconds: float) -> None:
        """Perform a cancellable bounded wait."""
        ...


def acquire_profile(
    request: AcquisitionRequest,
    client: ProfileClient,
    clock: MonotonicClock,
    cancelled: Callable[[], bool],
) -> AcquisitionResult:
    """Acquire a profile without holding its Lease during waits or beyond the deadline."""
    deadline = clock.monotonic() + request.deadline_seconds
    while clock.monotonic() < deadline:
        acquire_timeout = min(_MAX_API_TIMEOUT_SECONDS, deadline - clock.monotonic())
        acquired = False
        try:
            acquired = client.acquire(request.profile, acquire_timeout)
            if cancelled():
                return AcquisitionResult.CANCELLED
            inspect_remaining = deadline - clock.monotonic()
            if acquired and inspect_remaining > 0:
                inspect_timeout = min(_MAX_API_TIMEOUT_SECONDS, inspect_remaining)
                if client.inspect(request.profile, inspect_timeout) == ProfileState.READY:
                    return AcquisitionResult.ACQUIRED
        except ProfileApiError:
            return AcquisitionResult.API_ERROR
        finally:
            if acquired:
                client.release(request.profile, _MAX_API_TIMEOUT_SECONDS)
        if cancelled():
            return AcquisitionResult.CANCELLED
        remaining_after_call = deadline - clock.monotonic()
        if remaining_after_call <= 0:
            return AcquisitionResult.TIMEOUT
        clock.wait(min(5.0, remaining_after_call))
    return AcquisitionResult.TIMEOUT
