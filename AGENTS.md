# AGENTS.md

## Project overview

**aiarena-api** is an async Python client for the [aiarena.net](https://aiarena.net) REST API. Its consumers are
[ai-arena-recap](https://github.com/maxnus/ai-arena-recap) (the aiarenarecap.com website, which syncs the whole ladder),
[AvocaDOS](https://github.com/maxnus/AvocaDOS) (a bot, which uploads its releases and reads its own logs) and
[sc2-matchmaking-prototype](https://github.com/maxnus/sc2-matchmaking-prototype).

The server is [aiarena-web](https://github.com/aiarena/aiarena-web), a Django REST Framework app. When in doubt about an
endpoint, read its source there: views and filters in `aiarena/api/views/` and `aiarena/api/view_filters.py`, response
fields in `aiarena/api/views/serializers.py` and `aiarena/api/views/include.py`, and choices such as `result_cause` in
`aiarena/core/models/`.

## Layout

- `src/aiarena_api/client.py`: `AiArenaClient`. Generic `get`, `paginate` and `count`, one method per endpoint the
  consumers use, and `_send`, which every request goes through for retries and pacing.
- `src/aiarena_api/pacer.py`: `Pacer`, the shared request-rate cap with adaptive backoff.
- `src/aiarena_api/schema.py`: TypedDicts for the JSON the server returns.
- `tests/`: pytest; `respx` mocks httpx, so the tests never reach the network.

## Being a good client

aiarena is run by volunteers on one Django server, and replays are served from their S3 bucket at their cost. Keep
the defaults gentle and every bulk path pageable and paceable. Known behaviour of the server, which the client's
docstrings explain where it matters:

- Offset paging is only stable with `ordering=id` (or `-id`); without it pages overlap and skip, worst where rows
  change during the listing, such as matches finishing in a live round. `paginate` orders by id unless told otherwise.
- `/match-participations/` ignores its id and match range filters and takes no competition filter. Filter by `bot`.
- Server cost grows with offset: large pages fail deep into a listing, and an unfiltered listing fails past roughly a
  million rows.
- Declared filters are not proof that a filter works. Check a new one against the live API before relying on it.

And of httpx: passing `params=` replaces a URL's whole query rather than merging into it. The client asks for JSON with
an `Accept` header, never a `format` parameter, so that a `next` link is requested exactly as the server wrote it.
Don't add a query parameter to every request; a test that follows a `next` link must give that link a query.

## Conventions

- Python 3.11 is the floor (ai-arena-recap runs on it), so no `type X = ...` statements or `def f[T]()` generics.
- Keyword-only arguments for options (`*` in signatures), type hints everywhere, docstrings on public functions and
  classes without parameter or return sections.
- Separate stdlib, third-party and internal imports with a blank line.
- Logging through stdlib `logging`, never configured here: that is the application's choice.
- Comments say why the code is as it is now. The story of how something was found goes in the commit message.
- New endpoint methods return the server's JSON typed with a `schema` TypedDict, not a model object.

## Workflow

| Task | Command |
|---|---|
| Install | `uv sync` |
| Test | `uv run pytest` |
| Lint | `uv run ruff check` |
| Release | push a tag `vX.Y.Z`; `release.yml` publishes it to PyPI, with the version read from the tag by hatch-vcs |
