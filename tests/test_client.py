"""AiArenaClient against a mocked server: respx intercepts httpx without going to the network."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from aiarena_api import TOKEN_ENV, AiArenaClient
from aiarena_api.client import MIN_BULK_PAGE_SIZE

BASE = "https://example.test/api"


def run(coro_fn: Callable[[AiArenaClient], Awaitable[Any]]) -> Any:
    """Run `coro_fn(client)` against a fresh client."""

    async def _run() -> Any:
        async with AiArenaClient(base_url=BASE, token="test") as client:
            return await coro_fn(client)

    return asyncio.run(_run())


async def collect(iterator: AsyncIterator[Any]) -> list[Any]:
    """Gather an async iterator into a list."""
    return [item async for item in iterator]


class TestToken:
    @respx.mock
    def test_the_token_is_sent_as_the_authorization_header(self) -> None:
        route = respx.get(f"{BASE}/competitions/1/").mock(return_value=httpx.Response(200, json={"id": 1}))

        async def _run() -> None:
            async with AiArenaClient(base_url=BASE, token="secret-xyz") as client:
                await client.get_competition(1)

        asyncio.run(_run())
        assert route.calls.last.request.headers["Authorization"] == "Token secret-xyz"

    def test_the_token_falls_back_to_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TOKEN_ENV, "from-env")
        client = AiArenaClient()
        assert client._client.headers["Authorization"] == "Token from-env"
        asyncio.run(client.close())

    def test_no_token_at_all_fails_at_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(TOKEN_ENV, raising=False)
        with pytest.raises(ValueError, match=TOKEN_ENV):
            AiArenaClient()


class TestGet:
    @respx.mock
    def test_paths_are_relative_to_the_base_url_and_ask_for_json(self) -> None:
        route = respx.get(f"{BASE}/bots/7/").mock(return_value=httpx.Response(200, json={"id": 7}))
        assert run(lambda c: c.get("bots/7/")) == {"id": 7}
        request = route.calls.last.request
        assert request.headers["Accept"] == "application/json"
        assert not request.url.params

    @respx.mock
    def test_an_absolute_url_keeps_its_query(self) -> None:
        route = respx.get(f"{BASE}/rounds/").mock(return_value=httpx.Response(200, json={}))
        run(lambda c: c.get(f"{BASE}/rounds/?competition=37&offset=500"))
        assert dict(route.calls.last.request.url.params) == {"competition": "37", "offset": "500"}

    @respx.mock
    def test_a_url_on_another_server_is_refused_before_it_gets_the_token(self) -> None:
        route = respx.get("https://elsewhere.test/replay.SC2Replay").mock(return_value=httpx.Response(200))
        with pytest.raises(ValueError, match="elsewhere.test"):
            run(lambda c: c.get("https://elsewhere.test/replay.SC2Replay"))
        assert not route.called

    @respx.mock
    def test_a_5xx_is_retried_until_it_succeeds(self, fast_sleep: None) -> None:
        route = respx.get(f"{BASE}/competitions/1/").mock(
            side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"id": 1})]
        )
        assert run(lambda c: c.get_competition(1)) == {"id": 1}
        assert route.call_count == 3

    @respx.mock
    def test_a_5xx_raises_once_the_retries_run_out(self, fast_sleep: None) -> None:
        route = respx.get(f"{BASE}/competitions/1/").mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            run(lambda c: c.get_competition(1))
        assert route.call_count == 5

    @respx.mock
    def test_a_4xx_is_not_retried(self) -> None:
        route = respx.get(f"{BASE}/bots/1/").mock(return_value=httpx.Response(404))
        with pytest.raises(httpx.HTTPStatusError):
            run(lambda c: c.get_bot(1))
        assert route.call_count == 1

    @respx.mock
    def test_a_server_error_slows_the_pacing_down(self, fast_sleep: None) -> None:
        respx.get(f"{BASE}/thing/").mock(return_value=httpx.Response(503))

        async def _run(client: AiArenaClient) -> float:
            client.set_rate_per_minute(60)
            with pytest.raises(httpx.HTTPStatusError):
                await client.get("/thing/", max_attempts=1)
            return client.pacer.penalty

        assert run(_run) > 1.0


class TestPaging:
    @respx.mock
    def test_paginate_follows_the_next_links_with_their_query_intact(self) -> None:
        """A next link carries the offset and the filters; losing them re-reads the first page forever."""

        def responder(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("offset") == "500":
                return httpx.Response(200, json={"count": 3, "next": None, "results": [{"id": 3}]})
            page = {
                "count": 3,
                "next": f"{BASE}/things/?limit=500&offset=500&ordering=id&round=4",
                "results": [{"id": 1}, {"id": 2}],
            }
            return httpx.Response(200, json=page)

        route = respx.get(f"{BASE}/things/").mock(side_effect=responder)
        items = run(lambda c: collect(c.paginate("/things/", {"round": 4})))
        assert [i["id"] for i in items] == [1, 2, 3]
        first, second = (dict(call.request.url.params) for call in route.calls)
        assert first == {"limit": "500", "ordering": "id", "round": "4"}
        assert second == {"limit": "500", "offset": "500", "ordering": "id", "round": "4"}

    @respx.mock
    def test_a_listing_keeps_the_order_its_caller_asks_for(self) -> None:
        """Ordering by id is only the default; an order of the caller's own is just as stable."""
        route = respx.get(f"{BASE}/matches/").mock(return_value=httpx.Response(200, json={"next": None, "results": []}))
        run(lambda c: collect(c.paginate("/matches/", {"bot": 961, "ordering": "-id"})))
        assert route.calls.last.request.url.params["ordering"] == "-id"

    @respx.mock
    def test_every_listing_method_asks_for_an_order(self) -> None:
        """Rows that change while a listing is paged, such as matches finishing, move unless the listing is ordered."""
        route = respx.get(url__startswith=BASE).mock(
            return_value=httpx.Response(200, json={"next": None, "results": []})
        )

        async def _run(client: AiArenaClient) -> None:
            await collect(client.list_competitions())
            await collect(client.list_competition_participations(37))
            await collect(client.list_rounds(37))
            await collect(client.list_matches_for_round(41734))
            await collect(client.list_match_participations(5030848))
            await collect(client.list_maps())

        run(_run)
        assert [call.request.url.params.get("ordering") for call in route.calls] == ["id"] * 6

    @respx.mock
    def test_count_asks_for_a_single_item(self) -> None:
        route = respx.get(f"{BASE}/rounds/").mock(return_value=httpx.Response(200, json={"count": 42, "results": []}))
        assert run(lambda c: c.count("/rounds/", {"competition": 3})) == 42
        params = route.calls.last.request.url.params
        assert params["limit"] == "1"
        assert params["competition"] == "3"


class TestBotParticipations:
    @respx.mock
    def test_pages_are_ordered_by_id_and_large(self) -> None:
        """Ordering by id is a correctness requirement: offset pages without it overlap and skip. The large page is
        what makes the sweep affordable, since each page pays for its offset."""
        route = respx.get(f"{BASE}/match-participations/").mock(
            return_value=httpx.Response(200, json={"count": 1, "results": [{"id": 1}]})
        )

        async def _run(client: AiArenaClient) -> tuple[list, int, int]:
            rows = await collect(client.list_bot_match_participations(42))
            return rows, client.bulk_page_size, client.page_size

        rows, bulk_page_size, page_size = run(_run)
        assert rows == [{"id": 1}]
        params = route.calls[0].request.url.params
        assert params["ordering"] == "id"
        assert params["bot"] == "42"
        assert int(params["limit"]) == bulk_page_size > page_size

    @respx.mock
    def test_newest_first_orders_by_descending_id(self) -> None:
        route = respx.get(f"{BASE}/match-participations/").mock(
            return_value=httpx.Response(200, json={"count": 1, "results": [{"id": 9}]})
        )
        run(lambda c: collect(c.list_bot_match_participations(42, newest_first=True, page_size=50)))
        params = route.calls[0].request.url.params
        assert params["ordering"] == "-id"
        assert params["limit"] == "50"

    @respx.mock
    def test_a_row_out_of_order_is_still_yielded(self) -> None:
        """Skipping repeats must not turn a page the server sorted differently into lost rows."""
        respx.get(f"{BASE}/match-participations/").mock(
            return_value=httpx.Response(200, json={"count": 3, "results": [{"id": 3}, {"id": 1}, {"id": 2}]})
        )
        rows = run(lambda c: collect(c.list_bot_match_participations(42)))
        assert [r["id"] for r in rows] == [3, 1, 2]

    @respx.mock
    def test_a_row_shifted_onto_the_next_page_is_not_yielded_twice(self) -> None:
        """A match finishing between two newest-first pages pushes every row down by one."""
        pages = {
            0: {"count": 4, "results": [{"id": 9}, {"id": 8}]},
            2: {"count": 5, "results": [{"id": 8}, {"id": 7}]},  # 10 arrived, so 8 is seen again.
            4: {"count": 5, "results": [{"id": 6}]},
        }
        respx.get(f"{BASE}/match-participations/").mock(
            side_effect=lambda request: httpx.Response(200, json=pages[int(request.url.params["offset"])])
        )
        rows = run(lambda c: collect(c.list_bot_match_participations(42, newest_first=True, page_size=2)))
        assert [r["id"] for r in rows] == [9, 8, 7, 6]

    @respx.mock
    @pytest.mark.parametrize("status", [502, 429])
    def test_the_page_shrinks_after_a_server_error_and_the_offset_is_retried(
        self, fast_sleep: None, status: int
    ) -> None:
        """Server cost grows with offset, so a page size that works early in a long career fails deep into it. A
        429 is the server asking for less, too."""
        big = 2000
        rows = [{"id": i} for i in range(1, big + 1)]

        def responder(request: httpx.Request) -> httpx.Response:
            limit = int(request.url.params["limit"])
            offset = int(request.url.params["offset"])
            if offset == 0:
                return httpx.Response(200, json={"count": big + 1, "results": rows})
            if limit == big:  # Deep down, only a smaller page is served.
                return httpx.Response(status)
            return httpx.Response(200, json={"count": big + 1, "results": [{"id": 9999}]})

        respx.get(f"{BASE}/match-participations/").mock(side_effect=responder)
        got = run(lambda c: collect(c.list_bot_match_participations(7)))
        assert got[-1] == {"id": 9999}
        assert len(got) == big + 1
        limits = [int(call.request.url.params["limit"]) for call in respx.calls]
        assert limits[0] == big
        assert MIN_BULK_PAGE_SIZE <= limits[-1] < big

    @respx.mock
    def test_it_gives_up_rather_than_shrinking_forever(self, fast_sleep: None) -> None:
        respx.get(f"{BASE}/match-participations/").mock(return_value=httpx.Response(502))
        with pytest.raises(httpx.HTTPStatusError):
            run(lambda c: collect(c.list_bot_match_participations(7)))

    @respx.mock
    def test_a_client_error_raises_without_shrinking(self) -> None:
        """A smaller page cannot fix a refused request."""
        route = respx.get(f"{BASE}/match-participations/").mock(return_value=httpx.Response(403))
        with pytest.raises(httpx.HTTPStatusError):
            run(lambda c: collect(c.list_bot_match_participations(7)))
        assert route.call_count == 1


class TestUpdateBot:
    @respx.mock
    def test_only_the_given_fields_are_sent(self, tmp_path: Path) -> None:
        bot_zip = tmp_path / "MyBot.zip"
        bot_zip.write_bytes(b"zip-bytes")
        route = respx.patch(f"{BASE}/bots/961/").mock(return_value=httpx.Response(200, json={}))

        run(lambda c: c.update_bot(961, bot_zip=bot_zip, wiki_article_content="# Hi", bot_data_enabled=False))
        body = route.calls.last.request.content
        assert b'name="bot_zip"; filename="MyBot.zip"' in body
        assert b"zip-bytes" in body
        assert b'name="wiki_article_content"\r\n\r\n# Hi' in body
        assert b'name="bot_data_enabled"\r\n\r\nfalse' in body
        assert b"bot_data_publicly_downloadable" not in body
        assert b'name="bot_data"' not in body

    @respx.mock
    def test_an_upload_is_sent_once(self, tmp_path: Path, fast_sleep: None) -> None:
        """The server may have stored an upload before it failed to answer, so a retry is the caller's decision."""
        bot_zip = tmp_path / "MyBot.zip"
        bot_zip.write_bytes(b"zip-bytes")
        route = respx.patch(f"{BASE}/bots/961/").mock(return_value=httpx.Response(502))

        with pytest.raises(httpx.HTTPStatusError):
            run(lambda c: c.update_bot(961, bot_zip=bot_zip))
        assert route.call_count == 1

    @respx.mock
    def test_an_upload_waits_longer_than_other_requests(self, tmp_path: Path) -> None:
        bot_zip = tmp_path / "MyBot.zip"
        bot_zip.write_bytes(b"zip-bytes")
        route = respx.patch(f"{BASE}/bots/961/").mock(return_value=httpx.Response(200, json={}))

        run(lambda c: c.update_bot(961, bot_zip=bot_zip, timeout=600.0))
        assert route.calls.last.request.extensions["timeout"]["read"] == 600.0

    def test_nothing_to_change_is_refused(self) -> None:
        with pytest.raises(ValueError):
            run(lambda c: c.update_bot(961))


class TestDownloads:
    @respx.mock
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("download_bot_zip", "/bots/5/zip/"),
            ("download_bot_data", "/bots/5/data/"),
            ("download_match_log", "/match-participations/5/match-log/"),
        ],
    )
    def test_a_download_returns_the_raw_bytes(self, method: str, path: str) -> None:
        route = respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, content=b"PK\x03\x04"))
        assert run(lambda c: getattr(c, method)(5)) == b"PK\x03\x04"
        assert "format" not in route.calls.last.request.url.params
