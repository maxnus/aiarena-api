"""Pacing spreads a long job over hours instead of firing it in a burst. It is off by default."""

import asyncio

import pytest

from aiarena_api.pacer import MAX_PENALTY, PENALTY_HALFLIFE_SECONDS, Pacer


def test_unpaced_by_default() -> None:
    assert Pacer().min_interval == 0.0


def test_rate_becomes_an_interval_between_request_starts() -> None:
    assert Pacer(30).min_interval == 2.0


def test_concurrent_callers_share_one_rate_rather_than_multiplying_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eight workers at 60/min make 60 requests a minute between them, not 480."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def _run() -> None:
        pacer = Pacer(60)
        monkeypatch.setattr("aiarena_api.pacer.asyncio.sleep", fake_sleep)
        await asyncio.gather(*[pacer.wait() for _ in range(8)])

    asyncio.run(_run())
    # Eight slots a second apart: the first goes at once, and each later one waits a second longer than the last.
    assert [round(s) for s in sorted(slept)] == [1, 2, 3, 4, 5, 6, 7]


def test_pacing_does_not_hold_the_lock_while_waiting() -> None:
    """Claiming a slot is instant and only the waiting is staggered; a lock held across the sleep would serialise
    the workers on each other and make the effective rate drift."""

    async def _run() -> bool:
        pacer = Pacer(60)
        await pacer.wait()  # Takes the first slot, so the next one has to wait.
        task = asyncio.create_task(pacer.wait())
        await asyncio.sleep(0)  # Let it claim its slot and start waiting.
        locked_during_wait = pacer._lock.locked()
        task.cancel()
        return locked_during_wait

    assert asyncio.run(_run()) is False


def test_backoff_compounds_while_the_server_keeps_failing() -> None:
    pacer = Pacer(60)
    for _ in range(4):
        pacer.slow_down()
    # 2^4, give or take the sliver of real time that passed between the calls.
    assert 15.9 < pacer.penalty <= 16.0


def test_backoff_is_capped() -> None:
    pacer = Pacer(60)
    for _ in range(40):
        pacer.slow_down()
    assert pacer.penalty > MAX_PENALTY * 0.99


def test_recovery_is_measured_in_time_not_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    """A heavily paced job makes few requests, so recovery tied to responses would take it hours."""
    clock = {"t": 1000.0}
    monkeypatch.setattr("aiarena_api.pacer.time.monotonic", lambda: clock["t"])
    pacer = Pacer(60)
    for _ in range(3):
        pacer.slow_down()
    assert pacer.penalty == 8.0

    clock["t"] += PENALTY_HALFLIFE_SECONDS  # One half-life without a single request.
    assert pacer.penalty == 4.0
    clock["t"] += PENALTY_HALFLIFE_SECONDS * 2
    assert pacer.penalty == 1.0


def test_a_fresh_failure_compounds_on_the_decayed_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backing off again does not restart from the original spike."""
    clock = {"t": 1000.0}
    monkeypatch.setattr("aiarena_api.pacer.time.monotonic", lambda: clock["t"])
    pacer = Pacer(60)
    for _ in range(3):
        pacer.slow_down()  # 8x
    clock["t"] += PENALTY_HALFLIFE_SECONDS  # Decayed to 4x.
    pacer.slow_down()
    assert pacer.penalty == 8.0


def test_an_unpaced_pacer_never_waits_even_after_failures() -> None:
    """The penalty multiplies an interval that is zero, so backoff costs an unpaced client nothing."""
    pacer = Pacer()
    pacer.slow_down()

    async def _run() -> float:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await pacer.wait()
        return loop.time() - t0

    assert asyncio.run(_run()) < 0.05
