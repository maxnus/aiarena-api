"""The JSON the aiarena API returns, as TypedDicts.

These mirror the serializers in aiarena-web (`aiarena/api/views/serializers.py` and the field lists in
`aiarena/api/views/include.py`). They describe what the server sends; nothing checks it at runtime. Related objects
come as ids, timestamps as ISO 8601 strings, and files as URLs. A file the requesting user may not download, such as
another author's bot zip, comes as None.
"""

from typing import Generic, Literal, TypedDict, TypeVar

T = TypeVar("T")

ParticipationResult = Literal["none", "win", "loss", "tie"]
"""A match participation's result, from that bot's point of view."""

ResultCause = Literal[
    "game_rules",  # the game handed out the result
    "crash",
    "timeout",
    "race_mismatch",  # the bot joined with the wrong race
    "match_cancelled",
    "initialization_failure",
    "error",  # the match could not be run; pairs with result "none"
]
"""Why a match participation ended as it did."""

ResultType = Literal[
    "MatchCancelled",
    "InitializationError",
    "Error",
    "Player1Win",
    "Player2Win",
    "Player1Crash",
    "Player2Crash",
    "Player1TimeOut",
    "Player2TimeOut",
    "Player1RaceMismatch",
    "Player2RaceMismatch",
    "Player1Surrender",
    "Player2Surrender",
    "Tie",
]
"""A match result, from the match's point of view."""


class Page(TypedDict, Generic[T]):
    """One page of a list endpoint."""

    count: int
    next: str | None
    previous: str | None
    results: list[T]


class BotRace(TypedDict):
    id: int
    label: str  # "T", "Z", "P" or "R"


class Trophy(TypedDict):
    id: int
    icon: int
    bot: int
    name: str
    trophy_icon_name: str
    trophy_icon_image: str


class Bot(TypedDict):
    id: int
    user: int
    name: str
    created: str
    bot_zip: str | None
    bot_zip_updated: str
    bot_zip_md5hash: str | None
    bot_zip_publicly_downloadable: bool
    bot_data_enabled: bool
    bot_data: str | None
    bot_data_md5hash: str | None
    bot_data_publicly_downloadable: bool
    plays_race: BotRace
    type: str
    game_display_id: str
    trophies: list[Trophy]


class User(TypedDict):
    id: int
    username: str
    first_name: str
    last_name: str
    is_staff: bool
    is_active: bool
    date_joined: str
    patreon_level: str
    type: str


class Competition(TypedDict):
    id: int
    name: str
    game_mode: int
    date_created: str
    date_opened: str | None
    date_closed: str | None
    status: str
    max_active_rounds: int
    interest: int
    target_n_divisions: int
    n_divisions: int
    target_division_size: int
    rounds_per_cycle: int
    rounds_this_cycle: int
    n_placements: int


class CompetitionParticipation(TypedDict):
    id: int
    competition: int
    bot: int
    elo: int
    match_count: int
    win_perc: float
    win_count: int
    loss_perc: float
    loss_count: int
    tie_perc: float
    tie_count: int
    crash_perc: float
    crash_count: int
    elo_graph: str | None
    winrate_vs_duration_graph: str | None
    highest_elo: int | None
    slug: str
    active: bool
    division_num: int
    in_placements: bool


class Map(TypedDict):
    id: int
    name: str
    file: str
    game_mode: int
    competitions: list[int]
    enabled: bool


class Round(TypedDict):
    id: int
    number: int
    competition: int
    started: str
    finished: str | None
    complete: bool


class Result(TypedDict):
    id: int
    match: int
    winner: int | None
    type: ResultType
    created: str
    replay_file: str | None
    game_steps: int
    submitted_by: int | None
    arenaclient_log: str | None
    replay_file_has_been_cleaned: bool
    arenaclient_log_has_been_cleaned: bool
    bot1_name: str
    bot2_name: str


class MatchTag(TypedDict):
    user: int
    tag_name: str


class Match(TypedDict):
    id: int
    map: int
    created: str
    started: str | None
    assigned_to: int | None
    round: int | None
    requested_by: int | None
    require_trusted_arenaclient: bool
    result: Result | None
    tags: list[MatchTag]


class MatchParticipation(TypedDict):
    id: int
    match: int
    participant_number: int
    bot: int
    starting_elo: int | None
    resultant_elo: int | None
    elo_change: int | None
    match_log: str | None
    avg_step_time: float | None
    result: ParticipationResult | None
    result_cause: ResultCause | None
    use_bot_data: bool
    update_bot_data: bool
    match_log_has_been_cleaned: bool
