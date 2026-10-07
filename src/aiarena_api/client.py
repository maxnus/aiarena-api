import asyncio
import logging
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, Final, Self

import httpx

from aiarena_api.pacer import Pacer
from aiarena_api.schema import (
    Bot,
    Competition,
    CompetitionParticipation,
    Map,
    Match,
    MatchParticipation,
    Round,
    User,
)

log = logging.getLogger(__name__)

DEFAULT_BASE_URL: Final[str] = "https://aiarena.net/api"

# Where the client looks for a token when none is passed.
TOKEN_ENV: Final[str] = "AIARENA_API_TOKEN"

_RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

# Floor for the shrinking bulk page size. Below this the request count climbs faster than the per-request saving is
# worth.
MIN_BULK_PAGE_SIZE: Final[int] = 250


class AiArenaClient:
    """Async client for the aiarena.net API, with retries, pacing and paging.

    Every endpoint needs a token, which an aiarena user gets from https://aiarena.net/profile/token/. Use the client
    as an async context manager, or call `close`, to release its connections.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        concurrency: int = 8,
        timeout: float = 30.0,
        rate_per_minute: float | None = None,
        page_size: int = 500,
        bulk_page_size: int = 2000,
    ) -> None:
        """Create a client; `token` falls back to the AIARENA_API_TOKEN environment variable.

        The API honours large page sizes, so `page_size` is large to save round trips. `bulk_page_size` is where
        `list_bot_match_participations` starts: bigger pages pay the cost of a deep offset fewer times, and the
        client shrinks them when the server starts failing.
        """
        token = token or os.environ.get(TOKEN_ENV)
        if not token:
            raise ValueError(f"no aiarena API token: pass token= or set {TOKEN_ENV}")
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.bulk_page_size = bulk_page_size
        self.pacer = Pacer(rate_per_minute)
        self._client = httpx.AsyncClient(timeout=timeout, headers={"Authorization": f"Token {token}"})
        self._sem = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        """Release the client's connections."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def set_rate_per_minute(self, rate: float | None) -> None:
        """Cap how often requests may start, across all concurrent callers; None or 0 means unpaced."""
        self.pacer.set_rate_per_minute(rate)

    # --- Generic requests

    async def get(self, path: str, params: Mapping[str, Any] | None = None, *, max_attempts: int = 5) -> Any:
        """GET the JSON at `path`, which is relative to the base URL or absolute.

        `max_attempts` is 5 by default, where a 5xx is usually a blip worth riding out. A caller that can make the
        request cheaper instead should pass fewer: retrying an over-expensive query unchanged costs the server the
        same failed work every time.
        """
        response = await self._send("GET", path, params={"format": "json", **(params or {})}, max_attempts=max_attempts)
        return response.json()

    async def paginate(self, path: str, params: Mapping[str, Any] | None = None) -> AsyncIterator[Any]:
        """Yield every item of a list endpoint, following its `next` links."""
        url: str | None = path
        query: Mapping[str, Any] | None = {"limit": self.page_size, **(params or {})}
        while url:
            page = await self.get(url, query)
            for item in page.get("results", []):
                yield item
            url = page.get("next")
            query = None  # The next link carries the query.

    async def count(self, path: str, params: Mapping[str, Any] | None = None) -> int:
        """How many items a list endpoint holds, for the price of a one-item request."""
        page = await self.get(path, {**(params or {}), "limit": 1})
        return int(page["count"])

    # --- Competitions, rounds and matches

    async def get_competition(self, competition_id: int) -> Competition:
        """One competition."""
        return await self.get(f"/competitions/{competition_id}/")

    async def list_competitions(self) -> AsyncIterator[Competition]:
        """Every competition, open or closed."""
        async for item in self.paginate("/competitions/"):
            yield item

    async def list_competition_participations(self, competition_id: int) -> AsyncIterator[CompetitionParticipation]:
        """Every bot's standing in one competition."""
        async for item in self.paginate("/competition-participations/", {"competition": competition_id}):
            yield item

    async def list_rounds(self, competition_id: int) -> AsyncIterator[Round]:
        """Every round of one competition."""
        async for item in self.paginate("/rounds/", {"competition": competition_id}):
            yield item

    async def list_matches_for_round(self, round_id: int) -> AsyncIterator[Match]:
        """Every match of one round, each with its result embedded once it has one."""
        async for item in self.paginate("/matches/", {"round": round_id}):
            yield item

    async def get_match(self, match_id: int) -> Match:
        """One match, with its result embedded once it has one."""
        return await self.get(f"/matches/{match_id}/")

    async def list_match_participations(self, match_id: int) -> AsyncIterator[MatchParticipation]:
        """Both bots' sides of one match."""
        async for item in self.paginate("/match-participations/", {"match": match_id}):
            yield item

    async def list_bot_match_participations(
        self, bot_id: int, *, newest_first: bool = False, page_size: int | None = None
    ) -> AsyncIterator[MatchParticipation]:
        """Every match participation of one bot, oldest or newest first.

        This is the bulk path, one request per page instead of one per match. The endpoint takes no competition
        filter and ignores its id and match range filters, so a bot's whole career is the narrowest slice it serves:
        keep the rows you want and stop iterating once you have them. Filtering by bot is also what keeps the offsets
        manageable; paging the endpoint unfiltered fails with 504s past about a million rows.

        Ordering by id is what makes offset paging well defined; without it pages overlap and skip. A row already
        yielded is skipped, so a match that finishes mid-iteration, which shifts a newest-first listing by one row,
        yields no duplicate.

        Pages shrink as they get expensive. Server cost grows with offset, so a page size that is comfortable at the
        start of a long career fails deep into it. On a server error the page size drops and the same offset is
        retried, which is safe because offsets count rows; it never grows back, since offsets only get deeper.
        """
        limit = page_size or self.bulk_page_size
        ordering = "-id" if newest_first else "id"
        offset = 0
        total: int | None = None
        last_id: int | None = None
        while total is None or offset < total:
            params = {"limit": limit, "offset": offset, "bot": bot_id, "ordering": ordering}
            try:
                page = await self.get("/match-participations/", params, max_attempts=2)
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                client_error = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500
                if client_error or limit <= MIN_BULK_PAGE_SIZE:
                    raise
                limit = max(MIN_BULK_PAGE_SIZE, limit // 4)
                log.warning("Dropping to %d-row pages for bot %s at offset %d", limit, bot_id, offset)
                continue
            total = int(page.get("count") or 0)
            results = page.get("results", [])
            if not results:
                break
            for item in results:
                if last_id is None or (item["id"] < last_id if newest_first else item["id"] > last_id):
                    last_id = item["id"]
                    yield item
            offset += len(results)

    async def list_maps(self) -> AsyncIterator[Map]:
        """Every map aiarena has ever used."""
        async for item in self.paginate("/maps/"):
            yield item

    # --- Bots and users

    async def get_bot(self, bot_id: int) -> Bot:
        """One bot."""
        return await self.get(f"/bots/{bot_id}/")

    async def get_user(self, user_id: int) -> User:
        """One user, such as a bot's author."""
        return await self.get(f"/users/{user_id}/")

    async def update_bot(
        self,
        bot_id: int,
        *,
        bot_zip: Path | None = None,
        bot_data: Path | None = None,
        wiki_article_content: str | None = None,
        bot_zip_publicly_downloadable: bool | None = None,
        bot_data_publicly_downloadable: bool | None = None,
        bot_data_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Change a bot the token's user owns: upload a zip or data, replace its wiki article, or set its flags.

        Only the arguments given are sent. The server refuses new bot data while the data is frozen, which it is
        while the bot plays a match.
        """
        fields = {
            "wiki_article_content": wiki_article_content,
            "bot_zip_publicly_downloadable": bot_zip_publicly_downloadable,
            "bot_data_publicly_downloadable": bot_data_publicly_downloadable,
            "bot_data_enabled": bot_data_enabled,
        }
        data = {name: value for name, value in fields.items() if value is not None}
        # Read whole, so that a retry can send the file again.
        files = {
            name: (path.name, path.read_bytes(), "application/zip")
            for name, path in (("bot_zip", bot_zip), ("bot_data", bot_data))
            if path is not None
        }
        if not data and not files:
            raise ValueError("update_bot needs at least one field to change")
        response = await self._send("PATCH", f"/bots/{bot_id}/", data=data, files=files or None)
        return response.json()

    # --- Files

    async def download_bot_zip(self, bot_id: int) -> bytes:
        """A bot's zip, if it is public or the token's user owns the bot."""
        return (await self._send("GET", f"/bots/{bot_id}/zip/")).content

    async def download_bot_data(self, bot_id: int) -> bytes:
        """A bot's data zip, if it is public or the token's user owns the bot."""
        return (await self._send("GET", f"/bots/{bot_id}/data/")).content

    async def download_match_log(self, participation_id: int) -> bytes:
        """The zipped log a bot wrote during one match, if the token's user owns the bot.

        aiarena deletes match logs after a while. A participation whose log is gone, or not the token user's to
        download, has `match_log` set to None.
        """
        return (await self._send("GET", f"/match-participations/{participation_id}/match-log/")).content

    # --- Private

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        max_attempts: int = 5,
    ) -> httpx.Response:
        """Send a request with retries and backoff, and feed the server's distress back into the pacing."""
        url = self._url(path)
        await self.pacer.wait()
        async with self._sem:
            last = max_attempts - 1
            for attempt in range(max_attempts):
                try:
                    response = await self._client.request(method, url, params=params, data=data, files=files)
                except httpx.TransportError as exc:
                    self.pacer.slow_down()
                    if attempt == last:
                        raise
                    delay = 2**attempt
                    log.warning("Transport error %s, retrying in %ss", exc, delay)
                    await asyncio.sleep(delay)
                    continue
                if response.status_code in _RETRY_STATUSES:
                    self.pacer.slow_down()
                    if attempt < last:
                        delay = 2**attempt
                        log.warning("HTTP %s on %s, retrying in %ss", response.status_code, url, delay)
                        await asyncio.sleep(delay)
                        continue
                response.raise_for_status()
                return response
        raise RuntimeError("unreachable")
