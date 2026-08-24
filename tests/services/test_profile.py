from __future__ import annotations

from dataclasses import dataclass, field

from three_t_clip_pipeline.services.profile import (
    AcquisitionRequest,
    AcquisitionResult,
    ProfileApiError,
    ProfileState,
    acquire_profile,
)


@dataclass
class Clock:
    now: float = 0.0
    waits: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


@dataclass
class LeaseClient:
    acquired: bool = False
    released: int = 0
    timeouts: list[float] = field(default_factory=list)

    def acquire(self, profile: str, timeout_seconds: float) -> bool:
        del profile
        self.timeouts.append(timeout_seconds)
        self.acquired = True
        return True

    def inspect(self, profile: str, timeout_seconds: float) -> ProfileState:
        del profile
        self.timeouts.append(timeout_seconds)
        return ProfileState.PENDING

    def release(self, profile: str, timeout_seconds: float) -> None:
        del profile
        self.timeouts.append(timeout_seconds)
        self.acquired = False
        self.released += 1


@dataclass
class ClockConsumingClient:
    clock: Clock
    calls: list[tuple[str, float]] = field(default_factory=list)
    acquired: bool = False

    def _consume(self, operation: str, timeout_seconds: float) -> None:
        self.calls.append((operation, timeout_seconds))
        self.clock.now += timeout_seconds

    def acquire(self, profile: str, timeout_seconds: float) -> bool:
        del profile
        self._consume("acquire", timeout_seconds)
        self.acquired = True
        return True

    def inspect(self, profile: str, timeout_seconds: float) -> ProfileState:
        del profile
        self._consume("inspect", timeout_seconds)
        return ProfileState.PENDING

    def release(self, profile: str, timeout_seconds: float) -> None:
        del profile
        self._consume("release", timeout_seconds)
        self.acquired = False


def test_deadline_cancels_and_releases_lease() -> None:
    clock = Clock()
    client = LeaseClient()

    result = acquire_profile(
        AcquisitionRequest(profile="large", deadline_seconds=9),
        client,
        clock,
        lambda: False,
    )

    assert result == AcquisitionResult.TIMEOUT
    assert client.released >= 1
    assert client.acquired is False
    assert max(clock.waits) <= 5
    assert max(client.timeouts) <= 15
    assert clock.now <= 9


def test_cancellation_reaches_caller_after_release() -> None:
    clock = Clock()
    client = LeaseClient()
    cancellation_checks = iter((False, True))

    result = acquire_profile(
        AcquisitionRequest(profile="large", deadline_seconds=30),
        client,
        clock,
        lambda: next(cancellation_checks),
    )

    assert result == AcquisitionResult.CANCELLED
    assert client.released == 1
    assert client.acquired is False


def test_ready_profile_succeeds_and_releases_lease() -> None:
    clock = Clock()
    client = LeaseClient()
    client.inspect = lambda profile, timeout_seconds: ProfileState.READY

    result = acquire_profile(
        AcquisitionRequest(profile="large", deadline_seconds=30), client, clock, lambda: False
    )

    assert result == AcquisitionResult.ACQUIRED
    assert client.released == 1
    assert client.acquired is False


def test_api_error_has_stable_result_and_releases_lease() -> None:
    clock = Clock()
    client = LeaseClient()

    def fail(profile: str, timeout_seconds: float) -> ProfileState:
        del profile, timeout_seconds
        raise ProfileApiError

    client.inspect = fail

    result = acquire_profile(
        AcquisitionRequest(profile="large", deadline_seconds=30), client, clock, lambda: False
    )

    assert result == AcquisitionResult.API_ERROR
    assert client.released == 1
    assert client.acquired is False


def test_slow_calls_never_exceed_deadline_plus_one_api_timeout() -> None:
    clock = Clock()
    client = ClockConsumingClient(clock)

    result = acquire_profile(
        AcquisitionRequest(profile="large", deadline_seconds=9), client, clock, lambda: False
    )

    assert result == AcquisitionResult.TIMEOUT
    assert clock.now <= 24
    assert client.calls == [("acquire", 9), ("release", 15)]
    assert client.acquired is False
