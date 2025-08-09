from __future__ import annotations

from typing import Any, Dict
import json
import re
import voluptuous as vol
import logging
from homeassistant.helpers.translation import async_get_translations

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_BACKEND,
    CONF_BASE_URL, CONF_API_KEY,
    CONF_RADARR_URL, CONF_RADARR_KEY, CONF_RADARR_ROOT, CONF_RADARR_PROFILE,
    CONF_SONARR_URL, CONF_SONARR_KEY, CONF_SONARR_ROOT, CONF_SONARR_PROFILE, CONF_SONARR_LANG_PROFILE,
    CONF_PRESETS, CONF_DEFAULT_TV_SEASONS,
    CONF_OVERSEERR_SERVER_ID, CONF_OVERSEERR_PROFILE_ID_MOVIE, CONF_OVERSEERR_PROFILE_ID_TV,
)

LOGGER = logging.getLogger(__name__)

URL_RE = re.compile(r"^https?://", re.I)


def _valid_url(url: str) -> bool:
    return bool(URL_RE.match(url.strip()))


async def _option_labels(
    hass,
    *,
    category: str,  # "config" or "options"
    path: str,      # e.g. "step.user.data.backend"
    values: list[str],
) -> dict[str, str]:
    """Return mapping of label->value using translations when available.
    
        Tries these translation keys in order for each value:
        1) component.<domain>.<category>.<path>.<value>
        2) component.<domain>.<category>.<path>.option.<value>
    
        This supports JSON structures like:
            { "config": { "step": { "user": { "data_options": { "backend": { "overseerr": "Overseerr" }}}}}}
    """
    lang = getattr(getattr(hass, "config", None), "language", None) or "en"
    try:
        trans = await async_get_translations(hass, lang, category, [DOMAIN])
    except Exception:  # noqa: BLE001
        trans = {}
    out: dict[str, str] = {}
    base_primary = f"component.{DOMAIN}.{category}.{path}"
    base_fallback = f"{base_primary}.option"
    for v in values:
        label = (
            trans.get(f"{base_primary}.{v}")
            or trans.get(f"{base_fallback}.{v}")
            or v
        )
        out[label] = v
    return out


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 6

    async def async_step_user(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            sel = user_input[CONF_BACKEND]
            self._backend_choice = sel  # already canonical value via selector
            if self._backend_choice == "overseerr":
                return await self.async_step_ovsr_creds()
            return await self.async_step_arr_backend()

        backend_map = await _option_labels(
            self.hass,
            category="config",
            path="step.user.data_options.backend",
            values=["overseerr", "arr"],
        )
        schema = vol.Schema({
            vol.Required(CONF_BACKEND, default="overseerr"): vol.In(backend_map)
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_ovsr_creds(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        seasons_map = await _option_labels(
            self.hass,
            category="config",
            path="step.ovsr_creds.data_options.default_tv_seasons",
            values=["season1", "all"],
        )
        schema = vol.Schema({
            vol.Required(CONF_BASE_URL): str,
            vol.Required(CONF_API_KEY): str,
            vol.Required(CONF_DEFAULT_TV_SEASONS, default="season1"): vol.In(seasons_map),
        })
        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip()
            api_key = user_input[CONF_API_KEY].strip()
            default_tv = user_input[CONF_DEFAULT_TV_SEASONS]

            if not _valid_url(base_url):
                errors["base"] = "invalid_url"
            else:
                from .api_common import OverseerrClient
                session = async_get_clientsession(self.hass)
                client = OverseerrClient(base_url, api_key, session)
                if await client.ping():
                    radarr = await client.list_radarr()
                    sonarr = await client.list_sonarr()
                    movie_profiles: dict[str, str] = {}
                    tv_profiles: dict[str, str] = {}
                    try:
                        default_radarr = next((s for s in radarr if s.get("isDefault")), radarr[0] if radarr else None)
                        default_sonarr = next((s for s in sonarr if s.get("isDefault")), sonarr[0] if sonarr else None)
                        if default_radarr:
                            det_r = await client.get_radarr_details(default_radarr["id"])
                            movie_profiles = {str(p["id"]): p["name"] for p in (det_r.get("profiles") or [])}
                        if default_sonarr:
                            det_s = await client.get_sonarr_details(default_sonarr["id"])
                            tv_profiles = {str(p["id"]): p["name"] for p in (det_s.get("profiles") or [])}
                    except Exception:  # noqa: BLE001
                        pass
                    self._ovsr_ctx = {
                        "base_url": base_url,
                        "api_key": api_key,
                        "default_tv": default_tv,
                        "radarr": radarr,
                        "sonarr": sonarr,
                        "movie_profiles": movie_profiles,
                        "tv_profiles": tv_profiles,
                    }
                    return await self.async_step_ovsr_selects()
                errors["base"] = "cannot_connect"

        return self.async_show_form(step_id="ovsr_creds", data_schema=schema, errors=errors)

    async def async_step_ovsr_selects(self, user_input: Dict[str, Any] | None = None):
        ctx = getattr(self, "_ovsr_ctx", {})
        errors: Dict[str, str] = {}

        if user_input is not None:
            data = {
                CONF_BACKEND: "overseerr",
                CONF_BASE_URL: ctx["base_url"],
                CONF_API_KEY: ctx["api_key"],
                CONF_DEFAULT_TV_SEASONS: ctx["default_tv"],
            }
            sid = user_input.get(CONF_OVERSEERR_SERVER_ID)
            if sid:
                data[CONF_OVERSEERR_SERVER_ID] = int(sid)
            mp = user_input.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
            if mp:
                data[CONF_OVERSEERR_PROFILE_ID_MOVIE] = int(mp)
            tp = user_input.get(CONF_OVERSEERR_PROFILE_ID_TV)
            if tp:
                data[CONF_OVERSEERR_PROFILE_ID_TV] = int(tp)
            host_id = ctx['base_url'].split('://',1)[-1].rstrip('/')
            await self.async_set_unique_id(f"overseerr:{host_id}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Hassarr (Overseerr)", data=data)

        radarr = ctx.get("radarr") or []
        sonarr = ctx.get("sonarr") or []
        server_choices: dict[str, str] = {}
        for s in radarr:
            label = f"Radarr: {s.get('name','Unnamed')} (#{s['id']})"
            server_choices[label] = str(s["id"])  # label -> id
        for s in sonarr:
            label = f"Sonarr: {s.get('name','Unnamed')} (#{s['id']})"
            server_choices[label] = str(s["id"])  # label -> id

        # ctx holds id->name; invert to label->id
        movie_profile_choices = {name: mid for mid, name in (ctx.get("movie_profiles") or {}).items()}
        tv_profile_choices = {name: tid for tid, name in (ctx.get("tv_profiles") or {}).items()}

        schema = vol.Schema({
            vol.Optional(CONF_OVERSEERR_SERVER_ID): vol.In(server_choices) if server_choices else str,
            vol.Optional(CONF_OVERSEERR_PROFILE_ID_MOVIE): vol.In(movie_profile_choices) if movie_profile_choices else str,
            vol.Optional(CONF_OVERSEERR_PROFILE_ID_TV): vol.In(tv_profile_choices) if tv_profile_choices else str,
        })
        return self.async_show_form(step_id="ovsr_selects", data_schema=schema, errors=errors)

    async def async_step_arr_backend(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        session = async_get_clientsession(self.hass)
        seasons_map = await _option_labels(
            self.hass,
            category="config",
            path="step.arr_backend.data_options.default_tv_seasons",
            values=["season1", "all"],
        )
        schema = vol.Schema({
            vol.Required(CONF_RADARR_URL): str,
            vol.Required(CONF_RADARR_KEY): str,
            vol.Required(CONF_RADARR_ROOT): str,
            vol.Required(CONF_RADARR_PROFILE): vol.Coerce(int),
            vol.Required(CONF_SONARR_URL): str,
            vol.Required(CONF_SONARR_KEY): str,
            vol.Required(CONF_SONARR_ROOT): str,
            vol.Required(CONF_SONARR_PROFILE): vol.Coerce(int),
            vol.Optional(CONF_SONARR_LANG_PROFILE): vol.Coerce(int),
            vol.Required(CONF_DEFAULT_TV_SEASONS, default="season1"): vol.In(seasons_map),
        })

        if user_input is not None:
            radarr_url = user_input[CONF_RADARR_URL].strip()
            radarr_key = user_input[CONF_RADARR_KEY].strip()
            radarr_root = user_input[CONF_RADARR_ROOT].strip()
            radarr_prof = int(user_input[CONF_RADARR_PROFILE])

            sonarr_url = user_input[CONF_SONARR_URL].strip()
            sonarr_key = user_input[CONF_SONARR_KEY].strip()
            sonarr_root = user_input[CONF_SONARR_ROOT].strip()
            sonarr_prof = int(user_input[CONF_SONARR_PROFILE])
            sonarr_lang = user_input.get(CONF_SONARR_LANG_PROFILE)
            default_tv = user_input[CONF_DEFAULT_TV_SEASONS]

            if not (_valid_url(radarr_url) and _valid_url(sonarr_url)):
                errors["base"] = "invalid_url"
            else:
                from .api_common import RadarrClient, SonarrClient
                rc = RadarrClient(radarr_url, radarr_key, session)
                sc = SonarrClient(sonarr_url, sonarr_key, session)
                if await rc.ping() and await sc.ping():
                    host_id = f"{radarr_url.split('://',1)[-1]}|{sonarr_url.split('://',1)[-1]}".rstrip('/')
                    await self.async_set_unique_id(f"arr:{host_id}")
                    self._abort_if_unique_id_configured()
                    data = {
                        CONF_BACKEND: "arr",
                        CONF_RADARR_URL: radarr_url,
                        CONF_RADARR_KEY: radarr_key,
                        CONF_RADARR_ROOT: radarr_root,
                        CONF_RADARR_PROFILE: radarr_prof,
                        CONF_SONARR_URL: sonarr_url,
                        CONF_SONARR_KEY: sonarr_key,
                        CONF_SONARR_ROOT: sonarr_root,
                        CONF_SONARR_PROFILE: sonarr_prof,
                        CONF_SONARR_LANG_PROFILE: int(sonarr_lang) if sonarr_lang else None,
                        CONF_DEFAULT_TV_SEASONS: default_tv,
                    }
                    return self.async_create_entry(title="Hassarr (Sonarr/Radarr)", data=data)
                errors["base"] = "cannot_connect"

        return self.async_show_form(step_id="arr_backend", data_schema=schema, errors=errors)


@callback
def async_get_options_flow(config_entry):  # noqa: D401
    return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        errors: Dict[str, str] = {}

        current_presets = self.entry.options.get(CONF_PRESETS, [])
        current_default = self.entry.options.get(
            CONF_DEFAULT_TV_SEASONS,
            self.entry.data.get(CONF_DEFAULT_TV_SEASONS, "season1"),
        )

        ovsr_server_choices: dict[str, str] = {}
        ovsr_movie_profiles: dict[str, str] = {}
        ovsr_tv_profiles: dict[str, str] = {}
        if self.entry.data.get(CONF_BACKEND) == "overseerr":
            try:
                from .api_common import OverseerrClient
                session = async_get_clientsession(self.hass)
                client = OverseerrClient(self.entry.data[CONF_BASE_URL], self.entry.data[CONF_API_KEY], session)
                if await client.ping():
                    radarr = await client.list_radarr()
                    sonarr = await client.list_sonarr()
                    for s in radarr:
                        ovsr_server_choices[f"Radarr: {s.get('name','Unnamed')} (#{s['id']})"] = str(s["id"])  # label -> id
                    for s in sonarr:
                        ovsr_server_choices[f"Sonarr: {s.get('name','Unnamed')} (#{s['id']})"] = str(s["id"])  # label -> id
                    default_radarr = next((s for s in radarr if s.get("isDefault")), radarr[0] if radarr else None)
                    default_sonarr = next((s for s in sonarr if s.get("isDefault")), sonarr[0] if sonarr else None)
                    if default_radarr:
                        det = await client.get_radarr_details(default_radarr["id"])
                        ovsr_movie_profiles = {str(p["id"]): p["name"] for p in (det.get("profiles") or [])}
                    if default_sonarr:
                        det = await client.get_sonarr_details(default_sonarr["id"])
                        ovsr_tv_profiles = {str(p["id"]): p["name"] for p in (det.get("profiles") or [])}
            except Exception:  # noqa: BLE001
                pass

        def get_opt_or_data(key):
            return self.entry.options.get(key, self.entry.data.get(key))

        if user_input is not None:
            text = user_input["presets_json"]
            try:
                data = json.loads(text)
                if not isinstance(data, list):
                    raise ValueError("Presets must be a JSON array")
                names = set()
                for p in data:
                    if not isinstance(p, dict) or "name" not in p:
                        raise ValueError("Each preset needs a 'name'")
                    if p["name"] in names:
                        raise ValueError(f"Duplicate preset name: {p['name']}")
                    names.add(p["name"])
                out = {
                    CONF_PRESETS: data,
                    CONF_DEFAULT_TV_SEASONS: user_input[CONF_DEFAULT_TV_SEASONS],
                }
                if self.entry.data.get(CONF_BACKEND) == "overseerr":
                    sid = user_input.get(CONF_OVERSEERR_SERVER_ID)
                    if sid:
                        out[CONF_OVERSEERR_SERVER_ID] = int(sid)
                    mp = user_input.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
                    if mp:
                        out[CONF_OVERSEERR_PROFILE_ID_MOVIE] = int(mp)
                    tp = user_input.get(CONF_OVERSEERR_PROFILE_ID_TV)
                    if tp:
                        out[CONF_OVERSEERR_PROFILE_ID_TV] = int(tp)
                return self.async_create_entry(title="Options", data=out)
            except Exception:  # noqa: BLE001
                errors["base"] = "invalid_json"

        seasons_map = await _option_labels(
            self.hass,
            category="options",
            path="step.init.data_options.default_tv_seasons",
            values=["season1", "all"],
        )
        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_DEFAULT_TV_SEASONS, default=current_default): vol.In(seasons_map),
            vol.Required("presets_json", default=json.dumps(current_presets, indent=2) if current_presets else "[]"): str,
        }

        if self.entry.data.get(CONF_BACKEND) == "overseerr":
            server_default = str(get_opt_or_data(CONF_OVERSEERR_SERVER_ID)) if get_opt_or_data(CONF_OVERSEERR_SERVER_ID) else None
            movie_prof_default = str(get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_MOVIE)) if get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_MOVIE) else None
            tv_prof_default = str(get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_TV)) if get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_TV) else None
            # invert id->name to label->id
            ovsr_movie_choices = {name: mid for mid, name in ovsr_movie_profiles.items()}
            ovsr_tv_choices = {name: tid for tid, name in ovsr_tv_profiles.items()}
            schema_dict[vol.Optional(CONF_OVERSEERR_SERVER_ID, default=server_default)] = (
                vol.In(ovsr_server_choices) if ovsr_server_choices else vol.Coerce(int)
            )
            schema_dict[vol.Optional(CONF_OVERSEERR_PROFILE_ID_MOVIE, default=movie_prof_default)] = (
                vol.In(ovsr_movie_choices) if ovsr_movie_choices else vol.Coerce(int)
            )
            schema_dict[vol.Optional(CONF_OVERSEERR_PROFILE_ID_TV, default=tv_prof_default)] = (
                vol.In(ovsr_tv_choices) if ovsr_tv_choices else vol.Coerce(int)
            )

        schema = vol.Schema(schema_dict)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
