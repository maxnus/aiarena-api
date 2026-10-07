"""An async Python client for the aiarena.net API."""

from importlib.metadata import PackageNotFoundError, version

from aiarena_api import schema
from aiarena_api.client import DEFAULT_BASE_URL, TOKEN_ENV, AiArenaClient
from aiarena_api.pacer import Pacer

try:
    __version__ = version("aiarena-api")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["DEFAULT_BASE_URL", "TOKEN_ENV", "AiArenaClient", "Pacer", "__version__", "schema"]
