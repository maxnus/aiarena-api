import pytest


@pytest.fixture()
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub asyncio.sleep so that retry backoff and pacing don't actually wait."""

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _instant)
