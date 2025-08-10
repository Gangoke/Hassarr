from __future__ import annotations

from typing import Any, Dict
import json
import re
import voluptuous as vol
import logging
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers import selector
from yarl import URL

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
    CONF_OVERSEERR_USER_ID,
)

LOGGER = logging.getLogger(__name__)

URL_RE = re.compile(r"^https?://", re.I)


def _valid_url(url: str) -> bool:
    return bool(URL_RE.match(url.strip()))


def _safe_host_id(url: str) -> str:
    """Return a normalized host:port identifier without userinfo or path.

    Ensures we never persist credentials from URLs into the config registry.
    """
    try:
        u = URL(url)
        host = u.host or ""
        # Normalize port so http/https defaults are explicit
        if u.port is not None:
            port = u.port
        else:
            port = 443 if (u.scheme or "").lower() == "https" else 80
        return f"{host}:{port}"
    except Exception:  # noqa: BLE001
        # Fallback: very conservative extraction
        try:
            netloc = url.split("//", 1)[-1]
            netloc = netloc.split("/", 1)[0]
            # Strip potential userinfo@
            netloc = netloc.split("@", 1)[-1]
            host_only = netloc.split("?", 1)[0]
            return host_only
        except Exception:  # noqa: BLE001
            return "unknown"


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


def _ovsr_user_label(u: dict) -> str:
    """Pretty label for Overseerr user for dropdowns."""
    try:
        # Do not include email for privacy; prefer username/displayName, else fallback to id
        name = u.get("username") or u.get("displayName")
        if name:
            return str(name)
        uid = u.get("id")
        if isinstance(uid, int):
            return f"User #{uid}"
        return "User"
    except Exception:  # noqa: BLE001
        return f"User #{u.get('id')}"


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
                    host_id = _safe_host_id(base_url)
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
        users: list[dict] = []
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
        try:
            users = await client.list_users()
        except Exception:  # noqa: BLE001
            users = []

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
            vol.Optional(CONF_OVERSEERR_USER_ID): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": _ovsr_user_label(u), "value": str(u.get("id"))}
                        for u in users
                        if u.get("id") is not None
                    ],
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
            if user_input.get(CONF_OVERSEERR_USER_ID):
                try:
                    data[CONF_OVERSEERR_USER_ID] = int(user_input[CONF_OVERSEERR_USER_ID])
                except Exception:  # noqa: BLE001
                    pass
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
                    host_id = f"{_safe_host_id(radarr_url)}|{_safe_host_id(sonarr_url)}"
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

    async def async_step_init(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        errors: Dict[str, str] = {}

        current_presets = self.entry.options.get(CONF_PRESETS, [])
        current_default = self.entry.options.get(
            CONF_DEFAULT_TV_SEASONS,
            self.entry.data.get(CONF_DEFAULT_TV_SEASONS, "season1"),
        )

        ovsr_user_options: dict[str, str] = {}
        if self.entry.data.get(CONF_BACKEND) == "overseerr":
            try:
                from .api_common import OverseerrClient
                session = async_get_clientsession(self.hass)
                client = OverseerrClient(self.entry.data[CONF_BASE_URL], self.entry.data[CONF_API_KEY], session)
                if await client.ping():
                    users = await client.list_users()
                    ovsr_user_options = {str(u.get("id")): _ovsr_user_label(u) for u in (users or []) if u.get("id") is not None}
            except Exception:  # noqa: BLE001
                ovsr_user_options = {}

        if user_input is not None:
            text = user_input.get("presets_json", "[]")
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
                    uid = user_input.get(CONF_OVERSEERR_USER_ID)
                    if uid:
                        out[CONF_OVERSEERR_USER_ID] = int(uid)
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

        if self.entry.data.get(CONF_BACKEND) == "overseerr" and ovsr_user_options:
            default_uid = self.entry.options.get(CONF_OVERSEERR_USER_ID) or self.entry.data.get(CONF_OVERSEERR_USER_ID)
            default_uid_str = str(default_uid) if default_uid is not None else ""
            schema_dict[vol.Optional(CONF_OVERSEERR_USER_ID, default=default_uid_str)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"label": label, "value": uid} for uid, label in ovsr_user_options.items()],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        schema = vol.Schema(schema_dict)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
