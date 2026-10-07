# aiarena-api

An async Python client for the [aiarena.net](https://aiarena.net) API, the ladder where StarCraft II bots play each
other.

> **Status: alpha.** The API may still change between minor versions.

It does the parts every script against aiarena ends up rewriting:

- **Retries:** server errors and dropped connections are retried with backoff; client errors are raised at once.
  Uploads are sent once, since the server may have stored one it failed to answer for.
- **Paging:** list endpoints are read to the end with `async for`.
- **Pacing:** an optional cap on requests per minute, shared by every concurrent caller, which tightens on its own
  while the server returns errors. aiarena is run by volunteers, so a long job should use it.
- **Bulk history:** `list_bot_match_participations` reads a bot's whole match history in large pages, ordered so that
  offset paging neither skips nor repeats rows, and shrinks the pages when deep offsets start failing.
- **Types:** responses are typed with TypedDicts that mirror aiarena's serializers (`aiarena_api.schema`).

## Install

```bash
pip install "aiarena-api @ git+https://github.com/maxnus/aiarena-api@v0.1.0"
```

The distribution is `aiarena-api` and the import name is `aiarena_api`. The bare `aiarena` name on PyPI belongs to an
unrelated project.

## Use

Every endpoint needs a token, from [your aiarena profile](https://aiarena.net/profile/token/). Pass it as `token=`, or
set `AIARENA_API_TOKEN`.

```python
import asyncio

from aiarena_api import AiArenaClient


async def main() -> None:
    async with AiArenaClient() as client:
        bot = await client.get_bot(961)
        print(bot["name"], bot["plays_race"]["label"])

        # The bot's last ten games, newest first.
        n = 0
        async for participation in client.list_bot_match_participations(961, newest_first=True, page_size=10):
            print(participation["match"], participation["result"], participation["result_cause"])
            n += 1
            if n == 10:
                break


asyncio.run(main())
```

For a bot you own you can also upload a new version and download its data and match logs:

```python
from pathlib import Path

async with AiArenaClient() as client:
    await client.update_bot(961, bot_zip=Path("build/MyBot.zip"), wiki_article_content="# MyBot\n...")
    data_zip = await client.download_bot_data(961)
    log_zip = await client.download_match_log(participation_id)
```

A long job, such as importing a season, should be paced:

```python
async with AiArenaClient(rate_per_minute=10) as client:
    ...
```

Endpoints without a method of their own are reachable with `client.get(path, params)`, `client.paginate(path, params)`
and `client.count(path, params)`, which take a path relative to `https://aiarena.net/api/`. An absolute URL is accepted
only on the API's own server, because every request carries your token; fetch anything else, such as a replay's
signed download URL, with a plain HTTP client.

## Develop

```bash
uv sync
uv run pytest
uv run ruff check
```

Releases are made by tagging: the version is read from the latest git tag.
