from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

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
    # ARR defaults
    CONF_RADARR_ROOT, CONF_RADARR_PROFILE,
    CONF_SONARR_ROOT, CONF_SONARR_PROFILE, CONF_SONARR_LANG_PROFILE,
    # options/presets
    CONF_PRESETS, CONF_DEFAULT_TV_SEASONS,
    CONF_PROFILE_PRESET, CONF_QUALITY_PROFILE_ID, CONF_LANGUAGE_PROFILE_ID, CONF_ROOT_FOLDER_PATH,
    STORAGE_BACKEND, STORAGE_CLIENT,
)
from .api_common import OverseerrClient, OverseerrError, RadarrClient, SonarrClient, ArrError

_LOGGER = logging.getLogger(__name__)

# More strict seasons validation: list of positive ints OR the string "all"
SEASONS_SCHEMA = vol.Any("all", [cv.positive_int])

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
        vol.Optional(CONF_PROFILE_PRESET): cv.string,
        vol.Optional(CONF_QUALITY_PROFILE_ID): cv.positive_int,
        vol.Optional(CONF_LANGUAGE_PROFILE_ID): cv.positive_int,
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

    if backend == "overseerr":
        client = OverseerrClient(entry.data[CONF_BASE_URL], entry.data[CONF_API_KEY], session)
        store[STORAGE_CLIENT] = client
    else:
        radarr = RadarrClient(entry.data["radarr_url"], entry.data["radarr_api_key"], session)
        sonarr = SonarrClient(entry.data["sonarr_url"], entry.data["sonarr_api_key"], session)
        store["radarr"] = radarr
        store["sonarr"] = sonarr
        store["radarr_root"] = entry.data[CONF_RADARR_ROOT]
        store["radarr_profile"] = int(entry.data[CONF_RADARR_PROFILE])
        store["sonarr_root"] = entry.data[CONF_SONARR_ROOT]
        store["sonarr_profile"] = int(entry.data[CONF_SONARR_PROFILE])
        store["sonarr_lang"] = entry.data.get(CONF_SONARR_LANG_PROFILE)

    hass.data[DOMAIN][entry.entry_id] = store

    async def _svc_request(call: ServiceCall) -> None:
        data = SERVICE_REQUEST_SCHEMA(call.data)
        media_type = data["media_type"].lower()
        mt = "tv" if media_type == "show" else media_type

        seasons_param = _resolve_seasons_default(entry, mt, data.get("seasons"))

        try:
            if backend == "overseerr":
                client: OverseerrClient = store[STORAGE_CLIENT]
                # Choose server by media type with backward-compat fallback
                if mt == "movie":
                    server_id = (
                        data.get(CONF_OVERSEERR_SERVER_ID_OVERRIDE)
                        or entry.options.get(CONF_OVERSEERR_SERVER_ID_RADARR)
                        or entry.data.get(CONF_OVERSEERR_SERVER_ID_RADARR)
                        or entry.options.get(CONF_OVERSEERR_SERVER_ID)  # legacy
                        or entry.data.get(CONF_OVERSEERR_SERVER_ID)      # legacy
                    )
                else:
                    server_id = (
                        data.get(CONF_OVERSEERR_SERVER_ID_OVERRIDE)
                        or entry.options.get(CONF_OVERSEERR_SERVER_ID_SONARR)
                        or entry.data.get(CONF_OVERSEERR_SERVER_ID_SONARR)
                        or entry.options.get(CONF_OVERSEERR_SERVER_ID)  # legacy
                        or entry.data.get(CONF_OVERSEERR_SERVER_ID)      # legacy
                    )
                if mt == "movie":
                    profile_id = data.get(CONF_OVERSEERR_PROFILE_ID_OVERRIDE) or entry.options.get(CONF_OVERSEERR_PROFILE_ID_MOVIE) or entry.data.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
                else:
                    profile_id = data.get(CONF_OVERSEERR_PROFILE_ID_OVERRIDE) or entry.options.get(CONF_OVERSEERR_PROFILE_ID_TV) or entry.data.get(CONF_OVERSEERR_PROFILE_ID_TV)

                resp = await client.request_media(
                    query=data["query"],
                    media_type=mt,
                    seasons=seasons_param,
                    is_4k=data.get("is_4k", False),
                    server_id=server_id,
                    profile_id=profile_id,
                )
                _fire_event(hass, EVENT_REQUEST_COMPLETE, {
                    "backend": backend,
                    "media_type": mt,
                    "query": data["query"],
                    "tmdb_id": resp.get("media", {}).get("tmdbId") or resp.get("mediaId"),
                    "response": resp,
                })
            else:
                if mt == "movie":
                    sel = _resolve_preset(entry, mt, data)
                    radarr: RadarrClient = store["radarr"]
                    tmdb_id = await _ensure_tmdb_id_for_movie(radarr, data["query"])
                    resp = await radarr.add_movie(
                        tmdb_id=tmdb_id,
                        root=sel["root"],
                        profile_id=sel["quality_profile_id"],
                    )
                else:
                    sel = _resolve_preset(entry, mt, data)
                    sonarr: SonarrClient = store["sonarr"]
                    tmdb_id = await _ensure_tmdb_id_for_series(sonarr, data["query"])
                    resp = await sonarr.add_series(
                        tmdb_id=tmdb_id,
                        root=sel["root"],
                        quality_profile_id=sel["quality_profile_id"],
                        language_profile_id=sel.get("language_profile_id"),
                        seasons=seasons_param,
                    )
                _fire_event(hass, EVENT_REQUEST_COMPLETE, {
                    "backend": backend,
                    "media_type": mt,
                    "query": data["query"],
                    "tmdb_id": tmdb_id,
                    "response": resp,
                })
            _LOGGER.info("Request processed for %s: %s", mt, data["query"])
        except (OverseerrError, ArrError) as e:
            _LOGGER.error("Request failed (%s): %s", type(e).__name__, e)
            _fire_event(hass, EVENT_REQUEST_FAILED, {
                "backend": backend,
                "media_type": mt,
                "query": data["query"],
                "error": str(e),
            })
            raise

    hass.services.async_register(DOMAIN, SERVICE_REQUEST_MEDIA, _svc_request)
    entry.async_on_unload(entry.add_update_listener(_reload_on_update))
    return True


def _fire_event(hass: HomeAssistant, event: str, data: dict[str, Any]) -> None:
    hass.bus.async_fire(event, data)


async def _reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_REQUEST_MEDIA)
    return True


def _resolve_seasons_default(entry: ConfigEntry, media_type: str, seasons_value: Any) -> list[int] | str | None:
    from .const import CONF_DEFAULT_TV_SEASONS
    mt = "tv" if media_type == "show" else media_type
    if mt != "tv":
        return None
    if seasons_value is not None:
        return seasons_value
    default_mode = entry.options.get(CONF_DEFAULT_TV_SEASONS) or entry.data.get(CONF_DEFAULT_TV_SEASONS, "season1")
    return [1] if default_mode == "season1" else "all"


def _resolve_preset(entry: ConfigEntry, media_type: str, call_data: dict) -> dict:
    from .const import (
        CONF_PRESETS, CONF_PROFILE_PRESET, CONF_ROOT_FOLDER_PATH,
        CONF_QUALITY_PROFILE_ID, CONF_LANGUAGE_PROFILE_ID,
    )
    mt = "tv" if media_type == "show" else media_type
    opts = entry.options or {}
    presets = opts.get(CONF_PRESETS, [])
    by_name = {p.get("name"): p for p in presets if isinstance(p, dict) and p.get("name")}

    chosen = by_name.get(call_data.get(CONF_PROFILE_PRESET)) if call_data.get(CONF_PROFILE_PRESET) else None
    chosen_app = (chosen or {}).get("sonarr" if mt == "tv" else "radarr", {})

    resolved = {
        "root": call_data.get(CONF_ROOT_FOLDER_PATH)
                 or chosen_app.get("root")
                 or (entry.data["sonarr_root"] if mt == "tv" else entry.data["radarr_root"]),
        "quality_profile_id": int(
            call_data.get(CONF_QUALITY_PROFILE_ID)
            or chosen_app.get("quality_profile_id")
            or (entry.data["sonarr_quality_profile_id"] if mt == "tv" else entry.data["radarr_quality_profile_id"])
        ),
    }

    if mt == "tv":
        lang_override = call_data.get(CONF_LANGUAGE_PROFILE_ID)
        lang_from_preset = chosen_app.get("language_profile_id")
        lang_default = entry.data.get("sonarr_language_profile_id")
        if lang_override is not None:
            resolved["language_profile_id"] = int(lang_override)
        elif lang_from_preset is not None:
            resolved["language_profile_id"] = int(lang_from_preset)
        elif lang_default is not None:
            resolved["language_profile_id"] = int(lang_default)

    return resolved


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
