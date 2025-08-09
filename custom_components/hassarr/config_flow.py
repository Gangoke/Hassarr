from __future__ import annotations

from typing import Any, Dict
import json
import re
import voluptuous as vol
import logging
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers import selector

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
    CONF_OVERSEERR_SERVER_ID, CONF_OVERSEERR_SERVER_ID_RADARR, CONF_OVERSEERR_SERVER_ID_SONARR,
    CONF_OVERSEERR_PROFILE_ID_MOVIE, CONF_OVERSEERR_PROFILE_ID_TV,
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
            # Normalize: handle either canonical value ("overseerr"/"arr") or translated label
            if sel not in ("overseerr", "arr"):
                try:
                    label_to_value = await _option_labels(
                        self.hass,
                        category="config",
                        path="step.user.data_options.backend",
                        values=["overseerr", "arr"],
                    )
                    sel = label_to_value.get(sel, sel)
                except Exception:  # noqa: BLE001
                    pass
            self._backend_choice = sel
            if self._backend_choice == "overseerr":
                return await self.async_step_ovsr_creds()
            return await self.async_step_arr_backend()

        schema = vol.Schema({
            vol.Required(CONF_BACKEND, default="overseerr"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["overseerr", "arr"],
                    translation_key="backend",
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_ovsr_creds(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        schema = vol.Schema({
            vol.Required(CONF_BASE_URL): str,
            vol.Required(CONF_API_KEY): str,
            vol.Required(CONF_DEFAULT_TV_SEASONS, default="season1"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["season1", "all"],
                    translation_key="default_tv_seasons",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
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
                try:
                    if not await client.ping():
                        raise RuntimeError("ping failed")
                    radarr = await client.list_radarr()
                    sonarr = await client.list_sonarr()
                    movie_profiles: dict[str, str] = {}
                    tv_profiles: dict[str, str] = {}
                    # Try to get profiles from defaults or first available
                    default_radarr = next((s for s in radarr if s.get("isDefault")), radarr[0] if radarr else None)
                    default_sonarr = next((s for s in sonarr if s.get("isDefault")), sonarr[0] if sonarr else None)
                    if default_radarr:
                        det_r = await client.get_radarr_details(default_radarr["id"])
                        movie_profiles = {str(p["id"]): p["name"] for p in (det_r.get("profiles") or [])}
                    if default_sonarr:
                        det_s = await client.get_sonarr_details(default_sonarr["id"])
                        tv_profiles = {str(p["id"]): p["name"] for p in (det_s.get("profiles") or [])}
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
                except Exception:  # noqa: BLE001
                    errors["base"] = "cannot_connect"

        return self.async_show_form(step_id="ovsr_creds", data_schema=schema, errors=errors)

    async def async_step_ovsr_selects(self, user_input: Dict[str, Any] | None = None):
        ctx = getattr(self, "_ovsr_ctx", {})
        errors: Dict[str, str] = {}

        # Build choices for servers (fetch if not present)
        radarr = ctx.get("radarr") or []
        sonarr = ctx.get("sonarr") or []
        if not radarr and not sonarr and ctx.get("base_url") and ctx.get("api_key"):
            try:
                from .api_common import OverseerrClient
                session = async_get_clientsession(self.hass)
                client = OverseerrClient(ctx["base_url"], ctx["api_key"], session)
                if await client.ping():
                    radarr = await client.list_radarr()
                    sonarr = await client.list_sonarr()
                    ctx["radarr"], ctx["sonarr"] = radarr, sonarr
            except Exception:  # noqa: BLE001
                pass

        # Choices for servers
        radarr_server_choices: dict[str, str] = {}
        for s in radarr:
            radarr_server_choices[f"Radarr: {s.get('name','Unnamed')} (#{s['id']})"] = str(s["id"])  # label -> id
        sonarr_server_choices: dict[str, str] = {}
        for s in sonarr:
            sonarr_server_choices[f"Sonarr: {s.get('name','Unnamed')} (#{s['id']})"] = str(s["id"])  # label -> id

        if not radarr_server_choices and not sonarr_server_choices:
            errors["base"] = "overseerr_choices_missing"

        # Determine selected server IDs (from user input or defaults) and fetch profiles for them
        sel_radarr_id = None
        sel_sonarr_id = None
        try:
            if user_input and user_input.get(CONF_OVERSEERR_SERVER_ID_RADARR) is not None:
                sel_radarr_id = int(user_input[CONF_OVERSEERR_SERVER_ID_RADARR])
            else:
                sel_radarr_id = int(ctx.get("selected_radarr_id") or (radarr[0]["id"] if radarr else 0)) or None
        except Exception:
            sel_radarr_id = None
        try:
            if user_input and user_input.get(CONF_OVERSEERR_SERVER_ID_SONARR) is not None:
                sel_sonarr_id = int(user_input[CONF_OVERSEERR_SERVER_ID_SONARR])
            else:
                sel_sonarr_id = int(ctx.get("selected_sonarr_id") or (sonarr[0]["id"] if sonarr else 0)) or None
        except Exception:
            sel_sonarr_id = None
        if sel_radarr_id:
            ctx["selected_radarr_id"] = sel_radarr_id
        if sel_sonarr_id:
            ctx["selected_sonarr_id"] = sel_sonarr_id

        # Fetch profiles for the selected servers
        movie_profile_choices: dict[str, str] = {}
        tv_profile_choices: dict[str, str] = {}
        if ctx.get("base_url") and ctx.get("api_key"):
            try:
                from .api_common import OverseerrClient
                session = async_get_clientsession(self.hass)
                client = OverseerrClient(ctx["base_url"], ctx["api_key"], session)
                if sel_radarr_id:
                    det_r = await client.get_radarr_details(sel_radarr_id)
                    movie_profile_choices = {p["name"]: str(p["id"]) for p in (det_r.get("profiles") or [])}
                if sel_sonarr_id:
                    det_s = await client.get_sonarr_details(sel_sonarr_id)
                    tv_profile_choices = {p["name"]: str(p["id"]) for p in (det_s.get("profiles") or [])}
            except Exception:  # noqa: BLE001
                pass

        if not movie_profile_choices or not tv_profile_choices:
            errors["base"] = errors.get("base") or "overseerr_choices_missing"

        # Defaults: selected servers and first profile for each
        radarr_server_default = str(sel_radarr_id) if sel_radarr_id else (str(radarr[0]["id"]) if radarr else None)
        sonarr_server_default = str(sel_sonarr_id) if sel_sonarr_id else (str(sonarr[0]["id"]) if sonarr else None)
        movie_default = (
            (user_input.get(CONF_OVERSEERR_PROFILE_ID_MOVIE) if user_input else None)
            or next(iter(movie_profile_choices.values()), None)
        )
        tv_default = (
            (user_input.get(CONF_OVERSEERR_PROFILE_ID_TV) if user_input else None)
            or next(iter(tv_profile_choices.values()), None)
        )

        # Helper for required keys with defaults
        def req(key: Any, default: str | None):
            return vol.Required(key, default=default) if default is not None else vol.Required(key)

        # Build option lists
        radarr_server_options = [{"label": l, "value": v} for l, v in radarr_server_choices.items()]
        sonarr_server_options = [{"label": l, "value": v} for l, v in sonarr_server_choices.items()]
        movie_profile_options = [{"label": l, "value": v} for l, v in movie_profile_choices.items()]
        tv_profile_options = [{"label": l, "value": v} for l, v in tv_profile_choices.items()]

        schema = vol.Schema({
            req(CONF_OVERSEERR_SERVER_ID_RADARR, radarr_server_default): selector.SelectSelector(
                selector.SelectSelectorConfig(options=radarr_server_options, mode=selector.SelectSelectorMode.LIST)
            ),
            req(CONF_OVERSEERR_SERVER_ID_SONARR, sonarr_server_default): selector.SelectSelector(
                selector.SelectSelectorConfig(options=sonarr_server_options, mode=selector.SelectSelectorMode.LIST)
            ),
            req(CONF_OVERSEERR_PROFILE_ID_MOVIE, movie_default): selector.SelectSelector(
                selector.SelectSelectorConfig(options=movie_profile_options, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            req(CONF_OVERSEERR_PROFILE_ID_TV, tv_default): selector.SelectSelector(
                selector.SelectSelectorConfig(options=tv_profile_options, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
        })

        # Finalize only when all four values are provided by the user (ensures profile lists reflect chosen servers)
        if user_input is not None and all(
            k in user_input for k in (
                CONF_OVERSEERR_SERVER_ID_RADARR,
                CONF_OVERSEERR_SERVER_ID_SONARR,
                CONF_OVERSEERR_PROFILE_ID_MOVIE,
                CONF_OVERSEERR_PROFILE_ID_TV,
            )
        ) and not errors:
            data = {
                CONF_BACKEND: "overseerr",
                CONF_BASE_URL: ctx["base_url"],
                CONF_API_KEY: ctx["api_key"],
                CONF_DEFAULT_TV_SEASONS: ctx["default_tv"],
                CONF_OVERSEERR_SERVER_ID_RADARR: int(user_input[CONF_OVERSEERR_SERVER_ID_RADARR]),
                CONF_OVERSEERR_SERVER_ID_SONARR: int(user_input[CONF_OVERSEERR_SERVER_ID_SONARR]),
                CONF_OVERSEERR_PROFILE_ID_MOVIE: int(user_input[CONF_OVERSEERR_PROFILE_ID_MOVIE]),
                CONF_OVERSEERR_PROFILE_ID_TV: int(user_input[CONF_OVERSEERR_PROFILE_ID_TV]),
            }
            # Preserve legacy single server id if both selected servers are identical to allow fallback elsewhere
            if data[CONF_OVERSEERR_SERVER_ID_RADARR] == data[CONF_OVERSEERR_SERVER_ID_SONARR]:
                data[CONF_OVERSEERR_SERVER_ID] = data[CONF_OVERSEERR_SERVER_ID_RADARR]
            host_id = ctx["base_url"].split("://", 1)[-1].rstrip('/')
            await self.async_set_unique_id(f"overseerr:{host_id}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Hassarr (Overseerr)", data=data)

        return self.async_show_form(step_id="ovsr_selects", data_schema=schema, errors=errors)

    async def async_step_arr_backend(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        session = async_get_clientsession(self.hass)
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
            vol.Required(CONF_DEFAULT_TV_SEASONS, default="season1"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["season1", "all"],
                    translation_key="default_tv_seasons",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
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

        ovsr_radarr_choices: dict[str, str] = {}
        ovsr_sonarr_choices: dict[str, str] = {}
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
                        ovsr_radarr_choices[f"Radarr: {s.get('name','Unnamed')} (#{s['id']})"] = str(s["id"])  # label -> id
                    for s in sonarr:
                        ovsr_sonarr_choices[f"Sonarr: {s.get('name','Unnamed')} (#{s['id']})"] = str(s["id"])  # label -> id
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
                    # New separate defaults; keep legacy if provided
                    sid_r = user_input.get(CONF_OVERSEERR_SERVER_ID_RADARR)
                    if sid_r:
                        out[CONF_OVERSEERR_SERVER_ID_RADARR] = int(sid_r)
                    sid_s = user_input.get(CONF_OVERSEERR_SERVER_ID_SONARR)
                    if sid_s:
                        out[CONF_OVERSEERR_SERVER_ID_SONARR] = int(sid_s)
                    sid_legacy = user_input.get(CONF_OVERSEERR_SERVER_ID)
                    if sid_legacy:
                        out[CONF_OVERSEERR_SERVER_ID] = int(sid_legacy)
                    mp = user_input.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
                    if mp:
                        out[CONF_OVERSEERR_PROFILE_ID_MOVIE] = int(mp)
                    tp = user_input.get(CONF_OVERSEERR_PROFILE_ID_TV)
                    if tp:
                        out[CONF_OVERSEERR_PROFILE_ID_TV] = int(tp)
                return self.async_create_entry(title="Options", data=out)
            except Exception:  # noqa: BLE001
                errors["base"] = "invalid_json"

        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_DEFAULT_TV_SEASONS, default=current_default): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["season1", "all"],
                    translation_key="default_tv_seasons",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required("presets_json", default=json.dumps(current_presets, indent=2) if current_presets else "[]"): str,
        }

        if self.entry.data.get(CONF_BACKEND) == "overseerr":
            server_default_r = str(get_opt_or_data(CONF_OVERSEERR_SERVER_ID_RADARR)) if get_opt_or_data(CONF_OVERSEERR_SERVER_ID_RADARR) else None
            server_default_s = str(get_opt_or_data(CONF_OVERSEERR_SERVER_ID_SONARR)) if get_opt_or_data(CONF_OVERSEERR_SERVER_ID_SONARR) else None
            # Backward compat: fall back to legacy single selection
            legacy_default = str(get_opt_or_data(CONF_OVERSEERR_SERVER_ID)) if get_opt_or_data(CONF_OVERSEERR_SERVER_ID) else None
            movie_prof_default = str(get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_MOVIE)) if get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_MOVIE) else None
            tv_prof_default = str(get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_TV)) if get_opt_or_data(CONF_OVERSEERR_PROFILE_ID_TV) else None
            # Build dropdown options for profiles: label (name) -> value (id)
            ovsr_movie_options = [
                {"label": name, "value": mid} for mid, name in ovsr_movie_profiles.items()
            ]
            ovsr_tv_options = [
                {"label": name, "value": tid} for tid, name in ovsr_tv_profiles.items()
            ]
            # Radarr server dropdown
            schema_dict[vol.Optional(CONF_OVERSEERR_SERVER_ID_RADARR, default=server_default_r or legacy_default)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": l, "value": v} for l, v in ovsr_radarr_choices.items()],
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
            # Sonarr server dropdown
            schema_dict[vol.Optional(CONF_OVERSEERR_SERVER_ID_SONARR, default=server_default_s or legacy_default)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": l, "value": v} for l, v in ovsr_sonarr_choices.items()],
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
            schema_dict[vol.Optional(CONF_OVERSEERR_PROFILE_ID_MOVIE, default=movie_prof_default)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=ovsr_movie_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[vol.Optional(CONF_OVERSEERR_PROFILE_ID_TV, default=tv_prof_default)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=ovsr_tv_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        schema = vol.Schema(schema_dict)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
