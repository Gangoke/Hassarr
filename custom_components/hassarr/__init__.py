from __future__ import annotations

import logging
from typing import Any
import re

import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    SERVICE_REQUEST_MEDIA,
    EVENT_REQUEST_COMPLETE, EVENT_REQUEST_FAILED,
    CONF_BACKEND,
    # Overseerr
    CONF_BASE_URL, CONF_API_KEY,
    CONF_OVERSEERR_SERVER_ID, CONF_OVERSEERR_PROFILE_ID_MOVIE, CONF_OVERSEERR_PROFILE_ID_TV,
    CONF_OVERSEERR_SERVER_ID_RADARR, CONF_OVERSEERR_SERVER_ID_SONARR,
    CONF_OVERSEERR_SERVER_ID_OVERRIDE, CONF_OVERSEERR_PROFILE_ID_OVERRIDE,
    # options/defaults
    CONF_DEFAULT_TV_SEASONS,
    CONF_QUALITY_PROFILE_ID, CONF_ROOT_FOLDER_PATH,
    # ARR config keys
    CONF_RADARR_URL, CONF_RADARR_KEY, CONF_RADARR_ROOT, CONF_RADARR_PROFILE,
    CONF_SONARR_URL, CONF_SONARR_KEY, CONF_SONARR_ROOT, CONF_SONARR_PROFILE,
    STORAGE_BACKEND, STORAGE_CLIENT,
)
from .api_common import OverseerrClient, OverseerrError, RadarrClient, SonarrClient, ArrError

_LOGGER = logging.getLogger(__name__)

# More strict seasons validation: list of positive ints OR the string "all"
SEASONS_SCHEMA = vol.Any("all", [cv.positive_int], cv.string)

SERVICE_REQUEST_SCHEMA = vol.Schema(
    {
        vol.Required("query"): cv.string,
        vol.Required("media_type"): vol.In(["movie", "tv", "show"]),
        vol.Optional("seasons"): SEASONS_SCHEMA,
        vol.Optional("is_4k", default=False): cv.boolean,
        # Overseerr overrides
        vol.Optional(CONF_OVERSEERR_SERVER_ID_OVERRIDE): cv.positive_int,
        vol.Optional(CONF_OVERSEERR_PROFILE_ID_OVERRIDE): cv.positive_int,
        # ARR-only optional overrides:
        vol.Optional(CONF_QUALITY_PROFILE_ID): cv.positive_int,
        vol.Optional(CONF_ROOT_FOLDER_PATH): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # noqa: D401
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)

    backend = entry.data[CONF_BACKEND]
    hass.data.setdefault(DOMAIN, {})
    store: dict[str, Any] = {STORAGE_BACKEND: backend}

    def _to_int(v: Any) -> int | None:
        try:
            return int(v) if v is not None else None
        except Exception:  # noqa: BLE001
            return None

    # Seed default TV seasons runtime mode for service behavior
    try:
        store["default_tv_seasons_mode"] = entry.options.get(CONF_DEFAULT_TV_SEASONS) or entry.data.get(CONF_DEFAULT_TV_SEASONS, "season1")
    except Exception:  # noqa: BLE001
        store["default_tv_seasons_mode"] = "season1"

    if backend == "overseerr":
        client = OverseerrClient(entry.data[CONF_BASE_URL], entry.data[CONF_API_KEY], session)
        store[STORAGE_CLIENT] = client
        # Runtime selections managed by select entities
        ovsr_selected = store.setdefault("ovsr_selected", {
            "radarr_server_id": None,
            "sonarr_server_id": None,
            "movie_profile_id": None,
            "tv_profile_id": None,
        })
        # Seed from saved config/options so selects show defaults immediately
        ovsr_selected["radarr_server_id"] = _to_int(
            entry.options.get(CONF_OVERSEERR_SERVER_ID_RADARR)
            or entry.data.get(CONF_OVERSEERR_SERVER_ID_RADARR)
            or entry.options.get(CONF_OVERSEERR_SERVER_ID)  # legacy single
            or entry.data.get(CONF_OVERSEERR_SERVER_ID)
        )
        ovsr_selected["sonarr_server_id"] = _to_int(
            entry.options.get(CONF_OVERSEERR_SERVER_ID_SONARR)
            or entry.data.get(CONF_OVERSEERR_SERVER_ID_SONARR)
            or entry.options.get(CONF_OVERSEERR_SERVER_ID)  # legacy single
            or entry.data.get(CONF_OVERSEERR_SERVER_ID)
        )
        ovsr_selected["movie_profile_id"] = _to_int(
            entry.options.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
            or entry.data.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
        )
        ovsr_selected["tv_profile_id"] = _to_int(
            entry.options.get(CONF_OVERSEERR_PROFILE_ID_TV)
            or entry.data.get(CONF_OVERSEERR_PROFILE_ID_TV)
        )
        hass.data[DOMAIN][entry.entry_id] = store
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.SELECT, Platform.SENSOR])
    else:
        radarr = RadarrClient(entry.data[CONF_RADARR_URL], entry.data[CONF_RADARR_KEY], session)
        sonarr = SonarrClient(entry.data[CONF_SONARR_URL], entry.data[CONF_SONARR_KEY], session)
        store["radarr"] = radarr
        store["sonarr"] = sonarr
        # Runtime selections for ARR, to be controlled by select entities
        arr_selected = store.setdefault("arr_selected", {
            "radarr_root": None,
            "radarr_quality_profile_id": None,
            "sonarr_root": None,
            "sonarr_quality_profile_id": None,
        })
        # Seed from saved config/options
        arr_selected["radarr_root"] = entry.options.get(CONF_RADARR_ROOT) or entry.data.get(CONF_RADARR_ROOT)
        arr_selected["radarr_quality_profile_id"] = _to_int(
            entry.options.get(CONF_RADARR_PROFILE) or entry.data.get(CONF_RADARR_PROFILE)
        )
        arr_selected["sonarr_root"] = entry.options.get(CONF_SONARR_ROOT) or entry.data.get(CONF_SONARR_ROOT)
        arr_selected["sonarr_quality_profile_id"] = _to_int(
            entry.options.get(CONF_SONARR_PROFILE) or entry.data.get(CONF_SONARR_PROFILE)
        )
        hass.data[DOMAIN][entry.entry_id] = store
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.SELECT, Platform.SENSOR])

    async def _svc_request(call: ServiceCall) -> None:
        # Validate against a plain dict copy to avoid mutating Home Assistant's ReadOnlyDict
        data = SERVICE_REQUEST_SCHEMA(dict(call.data))
        media_type = data["media_type"].lower()
        mt = "tv" if media_type == "show" else media_type

        # Prefer runtime Default TV Seasons entity when user doesn't provide seasons
        seasons_param = _resolve_seasons_default(entry, mt, data.get("seasons"))
        if mt == "tv" and data.get("seasons") is None:
            mode = hass.data[DOMAIN][entry.entry_id].get("default_tv_seasons_mode")
            if mode == "season1":
                seasons_param = [1]
            elif mode == "all":
                seasons_param = "all"

        try:
            if backend == "overseerr":
                client: OverseerrClient = hass.data[DOMAIN][entry.entry_id][STORAGE_CLIENT]
                selected = hass.data[DOMAIN][entry.entry_id].get("ovsr_selected", {})
                # Choose server by media type with backward-compat fallback
                if mt == "movie":
                    server_id = (
                        data.get(CONF_OVERSEERR_SERVER_ID_OVERRIDE)
                        or selected.get("radarr_server_id")
                        or entry.options.get(CONF_OVERSEERR_SERVER_ID_RADARR)
                        or entry.data.get(CONF_OVERSEERR_SERVER_ID_RADARR)
                        or entry.options.get(CONF_OVERSEERR_SERVER_ID)  # legacy
                        or entry.data.get(CONF_OVERSEERR_SERVER_ID)      # legacy
                    )
                    profile_id = (
                        data.get(CONF_OVERSEERR_PROFILE_ID_OVERRIDE)
                        or selected.get("movie_profile_id")
                        or entry.options.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
                        or entry.data.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
                    )
                else:
                    server_id = (
                        data.get(CONF_OVERSEERR_SERVER_ID_OVERRIDE)
                        or selected.get("sonarr_server_id")
                        or entry.options.get(CONF_OVERSEERR_SERVER_ID_SONARR)
                        or entry.data.get(CONF_OVERSEERR_SERVER_ID_SONARR)
                        or entry.options.get(CONF_OVERSEERR_SERVER_ID)  # legacy
                        or entry.data.get(CONF_OVERSEERR_SERVER_ID)      # legacy
                    )
                    profile_id = (
                        data.get(CONF_OVERSEERR_PROFILE_ID_OVERRIDE)
                        or selected.get("tv_profile_id")
                        or entry.options.get(CONF_OVERSEERR_PROFILE_ID_TV)
                        or entry.data.get(CONF_OVERSEERR_PROFILE_ID_TV)
                    )

                resp = await client.request_media(
                    query=data["query"],
                    media_type=mt,
                    seasons=seasons_param,
                    is_4k=data.get("is_4k", False),
                    server_id=server_id,
                    profile_id=profile_id,
                )
                # Emit redacted event payload to avoid leaking sensitive information
                redacted_query = (data["query"][:200] + "…") if isinstance(data.get("query"), str) and len(data["query"]) > 200 else data.get("query")
                _fire_event(hass, EVENT_REQUEST_COMPLETE, {
                    "backend": backend,
                    "media_type": mt,
                    "query": redacted_query,
                    "tmdb_id": resp.get("media", {}).get("tmdbId") or resp.get("mediaId"),
                    # Minimal response subset to avoid leaking sensitive data
                    "response": _minimal_event_subset(resp, backend, mt),
                })
            else:
                arr_sel = hass.data[DOMAIN][entry.entry_id].get("arr_selected", {})
                if mt == "movie":
                    radarr: RadarrClient = store["radarr"]
                    tmdb_id = await _ensure_tmdb_id_for_movie(radarr, data["query"])
                    root = data.get("root_folder_path") or arr_sel.get("radarr_root")
                    qprof = data.get("quality_profile_id") or arr_sel.get("radarr_quality_profile_id")
                    if not root or not qprof:
                        raise ArrError("Radarr root and quality profile must be selected via entities or provided in call")
                    resp = await radarr.add_movie(
                        tmdb_id=tmdb_id,
                        root=str(root),
                        profile_id=int(qprof),
                    )
                else:
                    sonarr: SonarrClient = store["sonarr"]
                    tmdb_id = await _ensure_tmdb_id_for_series(sonarr, data["query"])
                    root = data.get("root_folder_path") or arr_sel.get("sonarr_root")
                    qprof = data.get("quality_profile_id") or arr_sel.get("sonarr_quality_profile_id")
                    if not root or not qprof:
                        raise ArrError("Sonarr root and quality profile must be selected via entities or provided in call")
                    resp = await sonarr.add_series(
                        tmdb_id=tmdb_id,
                        root=str(root),
                        quality_profile_id=int(qprof),
                        seasons=seasons_param,
                    )
                redacted_query = (data["query"][:200] + "…") if isinstance(data.get("query"), str) and len(data["query"]) > 200 else data.get("query")
                _fire_event(hass, EVENT_REQUEST_COMPLETE, {
                    "backend": backend,
                    "media_type": mt,
                    "query": redacted_query,
                    "tmdb_id": tmdb_id,
                    "response": _minimal_event_subset(resp, backend, mt),
                })
            _LOGGER.info("Request processed for %s: %s", mt, data["query"])
        except (OverseerrError, ArrError) as e:
            _LOGGER.error("Request failed (%s): %s", type(e).__name__, e)
            redacted_query = (data.get("query")[:200] + "…") if isinstance(data.get("query"), str) and len(data.get("query")) > 200 else data.get("query")
            _fire_event(hass, EVENT_REQUEST_FAILED, {
                "backend": backend,
                "media_type": mt,
                "query": redacted_query,
                "error": _scrub_error_text(str(e)),
            })
            raise HomeAssistantError(str(e)) from e

    hass.services.async_register(DOMAIN, SERVICE_REQUEST_MEDIA, _svc_request)
    entry.async_on_unload(entry.add_update_listener(_reload_on_update))
    return True


def _fire_event(hass: HomeAssistant, event: str, data: dict[str, Any]) -> None:
    hass.bus.async_fire(event, data)


def _redact_event_payload(value: Any, *, _depth: int = 0) -> Any:
    """Redact sensitive data in arbitrary structures for safe event emission.

    Rules:
    - Keys containing api, key, token, auth, password, secret, session are redacted.
    - Long strings are truncated to 200 chars.
    - Limits recursion to a sane depth to avoid heavy processing.
    """
    SENSITIVE_KEYS = ("api", "key", "token", "auth", "password", "secret", "session")
    MAX_DEPTH = 4
    if _depth > MAX_DEPTH:
        return "<redacted>"
    try:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                lk = str(k).lower()
                if any(s in lk for s in SENSITIVE_KEYS):
                    out[str(k)] = "<redacted>"
                else:
                    out[str(k)] = _redact_event_payload(v, _depth=_depth + 1)
            return out
        if isinstance(value, list):
            return [_redact_event_payload(v, _depth=_depth + 1) for v in value[:200]]  # cap size
        if isinstance(value, tuple):
            return tuple(_redact_event_payload(v, _depth=_depth + 1) for v in value[:200])
        if isinstance(value, (int, float, type(None), bool)):
            return value
        s = str(value)
        if len(s) > 200:
            return s[:200] + "…"
        return s
    except Exception:  # noqa: BLE001
        return "<redacted>"


def _minimal_event_subset(value: Any, backend: str, media_type: str) -> dict[str, Any]:
    """Extract a minimal, non-sensitive subset for event emission.

    - For Overseerr: include id, mediaId, and status (short).
    - For Arr: include id (created resource id) if available.
    """
    out: dict[str, Any] = {}
    try:
        if not isinstance(value, dict):
            return out
        # Common id fields
        for k in ("id", "requestId", "movieId", "seriesId"):
            v = value.get(k)
            if isinstance(v, int):
                out["id"] = v
                break
        if backend == "overseerr":
            mid = value.get("mediaId")
            if isinstance(mid, int):
                out["mediaId"] = mid
            status = value.get("status")
            if isinstance(status, (str, int)):
                s = str(status)
                out["status"] = s[:50]
        return out
    except Exception:  # noqa: BLE001
        return {}


_URL_RE = re.compile(r"\bhttps?://[^\s]+", re.I)


def _scrub_error_text(msg: str) -> str:
    """Scrub URLs and credentials from error messages.

    - Replace any http(s) URLs with host:port only.
    - Strip userinfo if present.
    - Truncate to a reasonable length.
    """
    def _replace(m: re.Match) -> str:
        url = m.group(0)
        # Remove scheme
        no_scheme = url.split("://", 1)[-1]
        # Remove path/query/fragment
        netloc = no_scheme.split("/", 1)[0]
        # Remove userinfo
        netloc = netloc.split("@", 1)[-1]
        return netloc

    try:
        cleaned = _URL_RE.sub(_replace, msg)
        if len(cleaned) > 500:
            cleaned = cleaned[:500] + "…"
        return cleaned
    except Exception:  # noqa: BLE001
        return "<error>"


async def _reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, [Platform.SELECT, Platform.SENSOR])
    hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_REQUEST_MEDIA)
    return unload_ok


def _resolve_seasons_default(entry: ConfigEntry, media_type: str, seasons_value: Any) -> list[int] | str | None:
    from .const import CONF_DEFAULT_TV_SEASONS
    mt = "tv" if media_type == "show" else media_type
    if mt != "tv":
        return None
    if seasons_value is not None:
        parsed = _parse_seasons(seasons_value)
        return parsed
    default_mode = entry.options.get(CONF_DEFAULT_TV_SEASONS) or entry.data.get(CONF_DEFAULT_TV_SEASONS, "season1")
    return [1] if default_mode == "season1" else "all"


def _parse_seasons(seasons_value: Any) -> list[int] | str:
    """Parse seasons from UI input.

    Accepts:
    - "all" (case-insensitive)
    - list[int]
    - string like "[1,2,5]" or "1,2,5" or "1"
    Returns list of ints or "all".
    """
    if isinstance(seasons_value, str):
        s = seasons_value.strip().lower()
        if s == "all":
            return "all"
        try:
            # Try JSON first
            import json as _json

            val = _json.loads(seasons_value)
            if isinstance(val, list):
                return [int(x) for x in val]
            return [int(val)]
        except Exception:  # noqa: BLE001
            # Fallback simple csv
            parts = [p.strip() for p in seasons_value.replace("[", "").replace("]", "").split(",") if p.strip()]
            return [int(p) for p in parts] if parts else [1]
    if seasons_value == "all":
        return "all"
    # assume list
    return [int(x) for x in seasons_value]


async def _ensure_tmdb_id_for_movie(radarr: RadarrClient, query: str) -> int:
    if query.lower().startswith("tmdb:"):
        return int(query.split(":", 1)[1])
    results = await radarr.lookup(query)
    if not results:
        raise ArrError(f"No Radarr lookup results for '{query}'")
    return int(results[0].get("tmdbId"))


async def _ensure_tmdb_id_for_series(sonarr: SonarrClient, query: str) -> int:
    if query.lower().startswith("tmdb:"):
        return int(query.split(":", 1)[1])
    results = await sonarr.lookup(query)
    if not results:
        raise ArrError(f"No Sonarr lookup results for '{query}'")
    tmdb = results[0].get("tmdbId")
    if not tmdb:
        raise ArrError("No TMDB id in Sonarr lookup result. Provide title that resolves or use 'tmdb:<id>'.")
    return int(tmdb)
