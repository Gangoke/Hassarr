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
    CONF_SONARR_URL, CONF_SONARR_KEY, CONF_SONARR_ROOT, CONF_SONARR_PROFILE,
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
    _backend_choice: str | None = None
    _tmp_data: Dict[str, Any] | None = None
    _ovsr_servers: Dict[str, int] | None = None

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
        })
        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip()
            api_key = user_input[CONF_API_KEY].strip()
            if not _valid_url(base_url):
                errors["base"] = "invalid_url"
            else:
                from .api_common import OverseerrClient
                session = async_get_clientsession(self.hass)
                client = OverseerrClient(base_url, api_key, session)
                try:
                    if not await client.ping():
                        raise RuntimeError("ping failed")
                    host_id = base_url.split("://", 1)[-1].rstrip('/')
                    await self.async_set_unique_id(f"overseerr:{host_id}")
                    self._abort_if_unique_id_configured()
                    # Stash and go to server/profile selection
                    self._tmp_data = {
                        CONF_BACKEND: "overseerr",
                        CONF_BASE_URL: base_url,
                        CONF_API_KEY: api_key,
                    }
                    return await self.async_step_ovsr_select_servers()
                except Exception:  # noqa: BLE001
                    errors["base"] = "cannot_connect"

        return self.async_show_form(step_id="ovsr_creds", data_schema=schema, errors=errors)

    async def async_step_ovsr_select_servers(self, user_input: Dict[str, Any] | None = None):
        assert self._tmp_data and self._tmp_data.get(CONF_BACKEND) == "overseerr"
        errors: Dict[str, str] = {}
        # Fetch servers and default profiles
        from .api_common import OverseerrClient
        session = async_get_clientsession(self.hass)
        client = OverseerrClient(self._tmp_data[CONF_BASE_URL], self._tmp_data[CONF_API_KEY], session)
        try:
            radarr = await client.list_radarr()
            sonarr = await client.list_sonarr()
        except Exception:  # noqa: BLE001
            radarr, sonarr = [], []

        def _first_or_default(srvs: list[dict]) -> dict | None:
            return next((s for s in srvs if s.get("isDefault")), srvs[0] if srvs else None)

        default_radarr = _first_or_default(radarr)
        default_sonarr = _first_or_default(sonarr)

        schema = vol.Schema({
            vol.Required(CONF_OVERSEERR_SERVER_ID_RADARR, default=(str(default_radarr["id"]) if default_radarr else None)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": f"{s.get('name','Radarr')} (#{s['id']})", "value": str(s["id"]).strip()} for s in radarr],
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_OVERSEERR_SERVER_ID_SONARR, default=(str(default_sonarr["id"]) if default_sonarr else None)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": f"{s.get('name','Sonarr')} (#{s['id']})", "value": str(s["id"]).strip()} for s in sonarr],
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        })

        if user_input is not None:
            self._ovsr_servers = {
                "radarr": int(user_input[CONF_OVERSEERR_SERVER_ID_RADARR]),
                "sonarr": int(user_input[CONF_OVERSEERR_SERVER_ID_SONARR]),
            }
            return await self.async_step_ovsr_select_profiles()

        return self.async_show_form(step_id="ovsr_select_servers", data_schema=schema, errors=errors)

    async def async_step_ovsr_select_profiles(self, user_input: Dict[str, Any] | None = None):
        assert self._tmp_data and self._ovsr_servers
        errors: Dict[str, str] = {}
        # Fetch profiles for chosen servers
        from .api_common import OverseerrClient
        session = async_get_clientsession(self.hass)
        client = OverseerrClient(self._tmp_data[CONF_BASE_URL], self._tmp_data[CONF_API_KEY], session)
        movie_profiles: list[dict] = []
        tv_profiles: list[dict] = []
        try:
            det = await client.get_radarr_details(self._ovsr_servers["radarr"])
            movie_profiles = det.get("profiles") or []
        except Exception:  # noqa: BLE001
            pass
        try:
            det = await client.get_sonarr_details(self._ovsr_servers["sonarr"])
            tv_profiles = det.get("profiles") or []
        except Exception:  # noqa: BLE001
            pass

        schema = vol.Schema({
            vol.Required(CONF_OVERSEERR_PROFILE_ID_MOVIE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": p.get("name"), "value": str(p.get("id"))} for p in movie_profiles],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_OVERSEERR_PROFILE_ID_TV): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": p.get("name"), "value": str(p.get("id"))} for p in tv_profiles],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })

        if user_input is not None:
            # Stash for next step (TV seasons)
            data = dict(self._tmp_data)
            data[CONF_OVERSEERR_SERVER_ID_RADARR] = self._ovsr_servers["radarr"]
            data[CONF_OVERSEERR_SERVER_ID_SONARR] = self._ovsr_servers["sonarr"]
            data[CONF_OVERSEERR_PROFILE_ID_MOVIE] = int(user_input[CONF_OVERSEERR_PROFILE_ID_MOVIE])
            data[CONF_OVERSEERR_PROFILE_ID_TV] = int(user_input[CONF_OVERSEERR_PROFILE_ID_TV])
            self._tmp_data = data
            return await self.async_step_ovsr_tv_seasons()

        return self.async_show_form(step_id="ovsr_select_profiles", data_schema=schema, errors=errors)

    async def async_step_ovsr_tv_seasons(self, user_input: Dict[str, Any] | None = None):
        assert self._tmp_data and self._tmp_data.get(CONF_BACKEND) == "overseerr"
        errors: Dict[str, str] = {}
        schema = vol.Schema({
            vol.Required(CONF_DEFAULT_TV_SEASONS, default="season1"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["season1", "all"],
                    translation_key="default_tv_seasons",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        })
        if user_input is not None:
            data = dict(self._tmp_data)
            data[CONF_DEFAULT_TV_SEASONS] = user_input[CONF_DEFAULT_TV_SEASONS]
            title = "Hassarr (Overseerr)"
            self._tmp_data = None
            self._ovsr_servers = None
            return self.async_create_entry(title=title, data=data)
        return self.async_show_form(step_id="ovsr_tv_seasons", data_schema=schema, errors=errors)

    async def async_step_arr_backend(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}
        session = async_get_clientsession(self.hass)
        schema = vol.Schema({
            vol.Required(CONF_RADARR_URL): str,
            vol.Required(CONF_RADARR_KEY): str,
            vol.Required(CONF_SONARR_URL): str,
            vol.Required(CONF_SONARR_KEY): str,
        })

        if user_input is not None:
            radarr_url = user_input[CONF_RADARR_URL].strip()
            radarr_key = user_input[CONF_RADARR_KEY].strip()

            sonarr_url = user_input[CONF_SONARR_URL].strip()
            sonarr_key = user_input[CONF_SONARR_KEY].strip()

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
                    self._tmp_data = {
                        CONF_BACKEND: "arr",
                        CONF_RADARR_URL: radarr_url,
                        CONF_RADARR_KEY: radarr_key,
                        CONF_SONARR_URL: sonarr_url,
                        CONF_SONARR_KEY: sonarr_key,
                    }
                    return await self.async_step_arr_select_roots()
                errors["base"] = "cannot_connect"

        return self.async_show_form(step_id="arr_backend", data_schema=schema, errors=errors)

    async def async_step_arr_select_roots(self, user_input: Dict[str, Any] | None = None):
        assert self._tmp_data and self._tmp_data.get(CONF_BACKEND) == "arr"
        errors: Dict[str, str] = {}
        from .api_common import RadarrClient, SonarrClient
        session = async_get_clientsession(self.hass)
        rc = RadarrClient(self._tmp_data[CONF_RADARR_URL], self._tmp_data[CONF_RADARR_KEY], session)
        sc = SonarrClient(self._tmp_data[CONF_SONARR_URL], self._tmp_data[CONF_SONARR_KEY], session)
        radarr_roots = []
        sonarr_roots = []
        try:
            radarr_roots = await rc.list_root_folders()
        except Exception:  # noqa: BLE001
            pass
        try:
            sonarr_roots = await sc.list_root_folders()
        except Exception:  # noqa: BLE001
            pass

        schema = vol.Schema({
            vol.Required(CONF_RADARR_ROOT): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": r.get("path"), "value": r.get("path")} for r in radarr_roots],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_SONARR_ROOT): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": r.get("path"), "value": r.get("path")} for r in sonarr_roots],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })

        if user_input is not None:
            data = dict(self._tmp_data)
            data[CONF_RADARR_ROOT] = user_input[CONF_RADARR_ROOT]
            data[CONF_SONARR_ROOT] = user_input[CONF_SONARR_ROOT]
            self._tmp_data = data
            return await self.async_step_arr_select_profiles()

        return self.async_show_form(step_id="arr_select_roots", data_schema=schema, errors=errors)

    async def async_step_arr_select_profiles(self, user_input: Dict[str, Any] | None = None):
        assert self._tmp_data and self._tmp_data.get(CONF_BACKEND) == "arr"
        errors: Dict[str, str] = {}
        from .api_common import RadarrClient, SonarrClient
        session = async_get_clientsession(self.hass)
        rc = RadarrClient(self._tmp_data[CONF_RADARR_URL], self._tmp_data[CONF_RADARR_KEY], session)
        sc = SonarrClient(self._tmp_data[CONF_SONARR_URL], self._tmp_data[CONF_SONARR_KEY], session)
        radarr_qprofiles = []
        sonarr_qprofiles = []
        try:
            radarr_qprofiles = await rc.list_quality_profiles()
        except Exception:  # noqa: BLE001
            pass
        try:
            sonarr_qprofiles = await sc.list_quality_profiles()
        except Exception:  # noqa: BLE001
            pass

        schema = vol.Schema({
            vol.Required(CONF_RADARR_PROFILE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": p.get("name"), "value": str(p.get("id"))} for p in radarr_qprofiles],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_SONARR_PROFILE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": p.get("name"), "value": str(p.get("id"))} for p in sonarr_qprofiles],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })

        if user_input is not None:
            data = dict(self._tmp_data)
            data[CONF_RADARR_PROFILE] = int(user_input[CONF_RADARR_PROFILE])
            data[CONF_SONARR_PROFILE] = int(user_input[CONF_SONARR_PROFILE])
            self._tmp_data = data
            return await self.async_step_arr_tv_seasons()

        return self.async_show_form(step_id="arr_select_profiles", data_schema=schema, errors=errors)

    async def async_step_arr_tv_seasons(self, user_input: Dict[str, Any] | None = None):
        assert self._tmp_data and self._tmp_data.get(CONF_BACKEND) == "arr"
        errors: Dict[str, str] = {}
        schema = vol.Schema({
            vol.Required(CONF_DEFAULT_TV_SEASONS, default="season1"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["season1", "all"],
                    translation_key="default_tv_seasons",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        })
        if user_input is not None:
            data = dict(self._tmp_data)
            data[CONF_DEFAULT_TV_SEASONS] = user_input[CONF_DEFAULT_TV_SEASONS]
            title = "Hassarr (Sonarr/Radarr)"
            self._tmp_data = None
            return self.async_create_entry(title=title, data=data)
        return self.async_show_form(step_id="arr_tv_seasons", data_schema=schema, errors=errors)


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
                    # Selections are primarily managed via Select entities, but allow options to be set here too.
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
