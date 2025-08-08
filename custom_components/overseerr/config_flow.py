from __future__ import annotations

from typing import Any, Dict, Optional
import json
import voluptuous as vol

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
    CONF_PRESETS, CONF_DEFAULT_TV_SEASONS, DEFAULT_TV_SEASONS_CHOICES,
    CONF_OVERSEERR_SERVER_ID, CONF_OVERSEERR_PROFILE_ID_MOVIE, CONF_OVERSEERR_PROFILE_ID_TV,
)
from .api_overseerr import OverseerrClient
from .api_arr import RadarrClient, SonarrClient

BACKEND_OPTIONS = ["overseerr", "arr"]


class OverseerrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 5

    async def async_step_user(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            self._backend_choice = user_input[CONF_BACKEND]
            if self._backend_choice == "overseerr":
                return await self.async_step_ovsr_creds()
            return await self.async_step_arr_backend()

        schema = vol.Schema({vol.Required(CONF_BACKEND, default="overseerr"): vol.In(BACKEND_OPTIONS)})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # ---- Overseerr: step 1 (credentials) ----
    async def async_step_ovsr_creds(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip()
            api_key = user_input[CONF_API_KEY].strip()
            default_tv = user_input[CONF_DEFAULT_TV_SEASONS]

            session = async_get_clientsession(self.hass)
            client = OverseerrClient(base_url, api_key, session)
            if await client.ping():
                # fetch services + profiles for step 2
                radarr = await client.list_radarr()
                sonarr = await client.list_sonarr()

                movie_profiles = {}
                tv_profiles = {}
                try:
                    default_radarr = next((s for s in radarr if s.get("isDefault")), radarr[0] if radarr else None)
                    default_sonarr = next((s for s in sonarr if s.get("isDefault")), sonarr[0] if sonarr else None)
                    if default_radarr:
                        det = await client.get_radarr_details(default_radarr["id"])
                        movie_profiles = {str(p["id"]): p["name"] for p in (det.get("profiles") or [])}
                    if default_sonarr:
                        det = await client.get_sonarr_details(default_sonarr["id"])
                        tv_profiles = {str(p["id"]): p["name"] for p in (det.get("profiles") or [])}
                except Exception:
                    # If details fetch fails, keep empty choices
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

        schema = vol.Schema({
            vol.Required(CONF_BASE_URL): str,
            vol.Required(CONF_API_KEY): str,
            vol.Required(CONF_DEFAULT_TV_SEASONS, default="season1"): vol.In(DEFAULT_TV_SEASONS_CHOICES),
        })
        return self.async_show_form(step_id="ovsr_creds", data_schema=schema, errors=errors)

    # ---- Overseerr: step 2 (optional selects) ----
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

            await self.async_set_unique_id(f"overseerr:{ctx['base_url']}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Media Requests (Overseerr)", data=data)

        # Build dynamic choices
        radarr = ctx.get("radarr") or []
        sonarr = ctx.get("sonarr") or []
        server_choices = {}
        for s in radarr:
            server_choices[str(s["id"])] = f"Radarr: {s.get('name','Unnamed')} (#{s['id']})"
        for s in sonarr:
            server_choices[str(s["id"])] = f"Sonarr: {s.get('name','Unnamed')} (#{s['id']})"

        movie_profile_choices = ctx.get("movie_profiles") or {}
        tv_profile_choices = ctx.get("tv_profiles") or {}

        schema = vol.Schema({
            vol.Optional(CONF_OVERSEERR_SERVER_ID): vol.In(server_choices) if server_choices else str,
            vol.Optional(CONF_OVERSEERR_PROFILE_ID_MOVIE): vol.In(movie_profile_choices) if movie_profile_choices else str,
            vol.Optional(CONF_OVERSEERR_PROFILE_ID_TV): vol.In(tv_profile_choices) if tv_profile_choices else str,
        })
        return self.async_show_form(step_id="ovsr_selects", data_schema=schema, errors=errors)

    # ---- ARR backend (single step) ----
    async def async_step_arr_backend(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        session = async_get_clientsession(self.hass)

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

            rc = RadarrClient(radarr_url, radarr_key, session)
            sc = SonarrClient(sonarr_url, sonarr_key, session)
            if await rc.ping() and await sc.ping():
                await self.async_set_unique_id(f"arr:{radarr_url}|{sonarr_url}")
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
                return self.async_create_entry(title="Media Requests (Sonarr/Radarr)", data=data)
            errors["base"] = "cannot_connect"

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
            vol.Required(CONF_DEFAULT_TV_SEASONS, default="season1"): vol.In(DEFAULT_TV_SEASONS_CHOICES),
        })
        return self.async_show_form(step_id="arr_backend", data_schema=schema, errors=errors)


@callback
def async_get_options_flow(config_entry):
    return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        errors = {}

        # Common current values
        current_presets = self.entry.options.get(CONF_PRESETS, [])
        current_default = self.entry.options.get(
            CONF_DEFAULT_TV_SEASONS,
            self.entry.data.get(CONF_DEFAULT_TV_SEASONS, "season1"),
        )

        # Try to fetch Overseerr servers/profiles if backend is Overseerr
        ovsr_server_choices = {}
        ovsr_movie_profiles = {}
        ovsr_tv_profiles = {}
        if self.entry.data.get(CONF_BACKEND) == "overseerr":
            try:
                session = async_get_clientsession(self.hass)
                from .api_overseerr import OverseerrClient
                client = OverseerrClient(self.entry.data[CONF_BASE_URL], self.entry.data[CONF_API_KEY], session)
                if await client.ping():
                    radarr = await client.list_radarr()
                    sonarr = await client.list_sonarr()
                    for s in radarr:
                        ovsr_server_choices[str(s["id"])] = f"Radarr: {s.get('name','Unnamed')} (#{s['id']})"
                    for s in sonarr:
                        ovsr_server_choices[str(s["id"])] = f"Sonarr: {s.get('name','Unnamed')} (#{s['id']})"

                    default_radarr = next((s for s in radarr if s.get("isDefault")), radarr[0] if radarr else None)
                    default_sonarr = next((s for s in sonarr if s.get("isDefault")), sonarr[0] if sonarr else None)
                    if default_radarr:
                        det = await client.get_radarr_details(default_radarr["id"])
                        ovsr_movie_profiles = {str(p["id"]): p["name"] for p in (det.get("profiles") or [])}
                    if default_sonarr:
                        det = await client.get_sonarr_details(default_sonarr["id"])
                        ovsr_tv_profiles = {str(p["id"]): p["name"] for p in (det.get("profiles") or [])}
            except Exception:
                pass

        # Prepare defaults from existing options or data
        def get_opt_or_data(key):
            return self.entry.options.get(key, self.entry.data.get(key))

        if user_input is not None:
            # Parse presets JSON first
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
                # Optional Overseerr fields (only if backend is Overseerr)
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
            except Exception:
                errors["base"] = "invalid_json"

        # Build schema
        schema_dict = {
            vol.Required(CONF_DEFAULT_TV_SEASONS, default=current_default): vol.In(DEFAULT_TV_SEASONS_CHOICES),
            vol.Required("presets_json", default=json.dumps(current_presets, indent=2) if current_presets else "[]"): str,
        }

        if self.entry.data.get(CONF_BACKEND) == "overseerr":
            # Use dropdowns if we have choices, else free-form numbers
            server_default = str(get_opt_or_data(CONF_OVERSEERR_SERVER_ID)) if get_opt_or_data(CONF_OVERSEERR_SERVER_ID) else None
            movie_prof_default = str(get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_MOVIE)) if get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_MOVIE) else None
            tv_prof_default = str(get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_TV)) if get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_TV) else None

            schema_dict[vol.Optional(CONF_OVERSEERR_SERVER_ID, default=server_default)] = (
                vol.In(ovsr_server_choices) if ovsr_server_choices else vol.Coerce(int)
            )
            schema_dict[vol.Optional(CONF_OVERSEERR_PROFILE_ID_MOVIE, default=movie_prof_default)] = (
                vol.In(ovsr_movie_profiles) if ovsr_movie_profiles else vol.Coerce(int)
            )
            schema_dict[vol.Optional(CONF_OVERSEERR_PROFILE_ID_TV, default=tv_prof_default)] = (
                vol.In(ovsr_tv_profiles) if ovsr_tv_profiles else vol.Coerce(int)
            )

        schema = vol.Schema(schema_dict)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
