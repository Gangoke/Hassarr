from __future__ import annotations

from typing import Any, Optional
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    STORAGE_CLIENT,
    STORAGE_BACKEND,
    CONF_OVERSEERR_SERVER_ID,
    CONF_OVERSEERR_SERVER_ID_RADARR,
    CONF_OVERSEERR_SERVER_ID_SONARR,
    CONF_OVERSEERR_PROFILE_ID_MOVIE,
    CONF_OVERSEERR_PROFILE_ID_TV,
    CONF_OVERSEERR_USER_ID,
)
from .api_common import OverseerrClient

_LOGGER = logging.getLogger(__name__)


class BaseOvsrSelect(SelectEntity):
    key: str = "base"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: OverseerrClient,
        selected: dict[str, Any],
        registry: dict[str, "BaseOvsrSelect"],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.selected = selected
        self.registry = registry
        self._attr_should_poll = False
        self._label_to_value: dict[str, Optional[int]] = {}
        self._current_id: Optional[int] = None

    @property
    def available(self) -> bool:
        return True

    @property
    def options(self) -> list[str]:  # type: ignore[override]
        return list(self._label_to_value.keys())

    @property
    def current_option(self) -> str | None:  # type: ignore[override]
        if self._current_id is None:
            for label, value in self._label_to_value.items():
                if value is None:
                    return label
            return None
        for label, value in self._label_to_value.items():
            if value == self._current_id:
                return label
        return None

    async def async_select_option(self, option: str) -> None:  # type: ignore[override]
        if option not in self._label_to_value:
            raise ValueError("invalid_option")
        self._current_id = self._label_to_value[option]
        await self._handle_selection_changed()
        self.async_write_ha_state()

    async def _handle_selection_changed(self) -> None:
        return

    async def async_added_to_hass(self) -> None:
        await self._async_initial_refresh()

    async def _async_initial_refresh(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        raise NotImplementedError

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name="Hassarr (Overseerr)",
            manufacturer="Hassarr",
        )


class RadarrServerSelect(BaseOvsrSelect):
    key = "radarr_server"

    @property
    def name(self) -> str:
        return "Hassarr Radarr Server"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-radarr-server"

    @property
    def icon(self) -> str:
        return "mdi:server"

    async def _refresh(self) -> None:
        servers = []
        try:
            servers = await self.client.list_radarr()
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Failed to list Radarr servers: %s", e)
        self._label_to_value = {"- Not set -": None}
        for s in servers or []:
            label = f"{s.get('name','Radarr')} (#{s.get('id')})"
            self._label_to_value[label] = int(s.get("id"))
            if s.get("isDefault") and self._current_id is None:
                self._current_id = int(s.get("id"))

        cur = (
            self.selected.get("radarr_server_id")
            or self.entry.options.get(CONF_OVERSEERR_SERVER_ID_RADARR)
            or self.entry.data.get(CONF_OVERSEERR_SERVER_ID_RADARR)
            or self.entry.options.get(CONF_OVERSEERR_SERVER_ID)
            or self.entry.data.get(CONF_OVERSEERR_SERVER_ID)
        )
        if cur is not None:
            self._current_id = int(cur)
        # Fallback to first server if still not set
        if self._current_id is None:
            for lbl, val in self._label_to_value.items():
                if val is not None:
                    self._current_id = int(val)
                    break
        self.selected["radarr_server_id"] = self._current_id

    async def _handle_selection_changed(self) -> None:
        self.selected["radarr_server_id"] = self._current_id
        prof = self.registry.get(MovieProfileSelect.key)
        if prof:
            await prof._refresh()  # noqa: SLF001
            prof.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # After initial load, ensure the movie profile options are populated
        prof = self.registry.get(MovieProfileSelect.key)
        if prof:
            await prof._refresh()  # noqa: SLF001
            prof.async_write_ha_state()


class SonarrServerSelect(BaseOvsrSelect):
    key = "sonarr_server"

    @property
    def name(self) -> str:
        return "Hassarr Sonarr Server"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-sonarr-server"

    @property
    def icon(self) -> str:
        return "mdi:server"

    async def _refresh(self) -> None:
        servers = []
        try:
            servers = await self.client.list_sonarr()
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Failed to list Sonarr servers: %s", e)
        self._label_to_value = {"- Not set -": None}
        for s in servers or []:
            label = f"{s.get('name','Sonarr')} (#{s.get('id')})"
            self._label_to_value[label] = int(s.get("id"))
            if s.get("isDefault") and self._current_id is None:
                self._current_id = int(s.get("id"))

        cur = (
            self.selected.get("sonarr_server_id")
            or self.entry.options.get(CONF_OVERSEERR_SERVER_ID_SONARR)
            or self.entry.data.get(CONF_OVERSEERR_SERVER_ID_SONARR)
            or self.entry.options.get(CONF_OVERSEERR_SERVER_ID)
            or self.entry.data.get(CONF_OVERSEERR_SERVER_ID)
        )
        if cur is not None:
            self._current_id = int(cur)
        if self._current_id is None:
            for lbl, val in self._label_to_value.items():
                if val is not None:
                    self._current_id = int(val)
                    break
        self.selected["sonarr_server_id"] = self._current_id

    async def _handle_selection_changed(self) -> None:
        self.selected["sonarr_server_id"] = self._current_id
        prof = self.registry.get(TvProfileSelect.key)
        if prof:
            await prof._refresh()  # noqa: SLF001
            prof.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # After initial load, ensure the tv profile options are populated
        prof = self.registry.get(TvProfileSelect.key)
        if prof:
            await prof._refresh()  # noqa: SLF001
            prof.async_write_ha_state()


class MovieProfileSelect(BaseOvsrSelect):
    key = "movie_profile"

    @property
    def name(self) -> str:
        return "Hassarr Movie Profile"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-movie-profile"

    @property
    def icon(self) -> str:
        return "mdi:movie-open-cog"

    async def _refresh(self) -> None:
        radarr_id = self.selected.get("radarr_server_id")
        profiles: list[dict] = []
        labels: dict[str, Optional[int]] = {"- Not set -": None}
        if radarr_id:
            try:
                details = await self.client.get_radarr_details(int(radarr_id))
                profiles = details.get("profiles") or []
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("Failed to fetch Radarr details for %s: %s", radarr_id, e)
        for p in profiles:
            labels[str(p.get("name"))] = int(p.get("id"))
        self._label_to_value = labels

        cur = (
            self.selected.get("movie_profile_id")
            or self.entry.options.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
            or self.entry.data.get(CONF_OVERSEERR_PROFILE_ID_MOVIE)
        )
        if cur is not None:
            self._current_id = int(cur)
        self.selected["movie_profile_id"] = self._current_id

    async def _handle_selection_changed(self) -> None:
        self.selected["movie_profile_id"] = self._current_id


class TvProfileSelect(BaseOvsrSelect):
    key = "tv_profile"

    @property
    def name(self) -> str:
        return "Hassarr TV Profile"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-tv-profile"

    @property
    def icon(self) -> str:
        return "mdi:television-classic"

    async def _refresh(self) -> None:
        sonarr_id = self.selected.get("sonarr_server_id")
        profiles: list[dict] = []
        labels: dict[str, Optional[int]] = {"- Not set -": None}
        if sonarr_id:
            try:
                details = await self.client.get_sonarr_details(int(sonarr_id))
                profiles = details.get("profiles") or []
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("Failed to fetch Sonarr details for %s: %s", sonarr_id, e)
        for p in profiles:
            labels[str(p.get("name"))] = int(p.get("id"))
        self._label_to_value = labels

        cur = (
            self.selected.get("tv_profile_id")
            or self.entry.options.get(CONF_OVERSEERR_PROFILE_ID_TV)
            or self.entry.data.get(CONF_OVERSEERR_PROFILE_ID_TV)
        )
        if cur is not None:
            self._current_id = int(cur)
        self.selected["tv_profile_id"] = self._current_id

    async def _handle_selection_changed(self) -> None:
        self.selected["tv_profile_id"] = self._current_id


class OverseerrUserSelect(BaseOvsrSelect):
    key = "overseerr_user"

    @property
    def name(self) -> str:
        return "Hassarr Overseerr User"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-overseerr-user"

    @property
    def icon(self) -> str:
        return "mdi:account"

    def _user_label(self, u: dict) -> str:
        try:
            # Privacy: do not display email. Prefer username/displayName, else id.
            name = u.get("username") or u.get("displayName")
            if name:
                return str(name)
            uid = u.get("id")
            if isinstance(uid, int):
                return f"User #{uid}"
            return "User"
        except Exception:  # noqa: BLE001
            return f"User #{u.get('id')}"

    async def _refresh(self) -> None:
        users = []
        try:
            users = await self.client.list_users()
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Failed to list Overseerr users: %s", e)
        labels: dict[str, Optional[int]] = {"- Not set -": None}
        for u in users or []:
            uid = u.get("id")
            if uid is None:
                continue
            labels[self._user_label(u)] = int(uid)
        self._label_to_value = labels

        cur = (
            self.selected.get("user_id")
            or self.entry.options.get(CONF_OVERSEERR_USER_ID)
            or self.entry.data.get(CONF_OVERSEERR_USER_ID)
        )
        if cur is not None:
            self._current_id = int(cur)
        self.selected["user_id"] = self._current_id

    async def _handle_selection_changed(self) -> None:
        self.selected["user_id"] = self._current_id


class ArrBaseSelect(SelectEntity):
    key: str = "arr_base"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, selected: dict[str, Any]) -> None:
        self.hass = hass
        self.entry = entry
        self.selected = selected
        self._attr_should_poll = False
        self._label_to_value: dict[str, Any] = {}
        self._current: Any = None

    @property
    def options(self) -> list[str]:  # type: ignore[override]
        return list(self._label_to_value.keys())

    @property
    def current_option(self) -> str | None:  # type: ignore[override]
        for k, v in self._label_to_value.items():
            if v == self._current:
                return k
        return None

    async def async_select_option(self, option: str) -> None:  # type: ignore[override]
        if option not in self._label_to_value:
            raise ValueError("invalid_option")
        self._current = self._label_to_value[option]
        await self._handle_changed()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        raise NotImplementedError

    async def _handle_changed(self) -> None:
        return

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name="Hassarr (Sonarr/Radarr)",
            manufacturer="Hassarr",
        )


class ArrRadarrRootSelect(ArrBaseSelect):
    key = "arr_radarr_root"

    @property
    def name(self) -> str:
        return "Hassarr Radarr Root"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-arr-radarr-root"

    @property
    def icon(self) -> str:
        return "mdi:folder"

    async def _refresh(self) -> None:
        radarr = self.hass.data[DOMAIN][self.entry.entry_id]["radarr"]
        roots = []
        try:
            roots = await radarr.list_root_folders()
        except Exception:  # noqa: BLE001
            roots = []
        self._label_to_value = {r.get("path"): r.get("path") for r in roots}
        cur = self.selected.get("radarr_root")
        if cur:
            self._current = cur
        else:
            # try first root if available
            self._current = next(iter(self._label_to_value.values()), None)
        self.selected["radarr_root"] = self._current

    async def _handle_changed(self) -> None:
        self.selected["radarr_root"] = self._current


class ArrRadarrQualityProfileSelect(ArrBaseSelect):
    key = "arr_radarr_quality_profile"

    @property
    def name(self) -> str:
        return "Hassarr Radarr Quality Profile"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-arr-radarr-quality-profile"

    @property
    def icon(self) -> str:
        return "mdi:quality-high"

    async def _refresh(self) -> None:
        radarr = self.hass.data[DOMAIN][self.entry.entry_id]["radarr"]
        profs = []
        try:
            profs = await radarr.list_quality_profiles()
        except Exception:  # noqa: BLE001
            profs = []
        self._label_to_value = {p.get("name"): int(p.get("id")) for p in profs}
        cur = self.selected.get("radarr_quality_profile_id")
        if cur is not None:
            self._current = int(cur)
        else:
            self._current = next(iter(self._label_to_value.values()), None)
        self.selected["radarr_quality_profile_id"] = self._current

    async def _handle_changed(self) -> None:
        self.selected["radarr_quality_profile_id"] = self._current


class ArrSonarrRootSelect(ArrBaseSelect):
    key = "arr_sonarr_root"

    @property
    def name(self) -> str:
        return "Hassarr Sonarr Root"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-arr-sonarr-root"

    @property
    def icon(self) -> str:
        return "mdi:folder"

    async def _refresh(self) -> None:
        sonarr = self.hass.data[DOMAIN][self.entry.entry_id]["sonarr"]
        roots = []
        try:
            roots = await sonarr.list_root_folders()
        except Exception:  # noqa: BLE001
            roots = []
        self._label_to_value = {r.get("path"): r.get("path") for r in roots}
        cur = self.selected.get("sonarr_root")
        if cur:
            self._current = cur
        else:
            self._current = next(iter(self._label_to_value.values()), None)
        self.selected["sonarr_root"] = self._current

    async def _handle_changed(self) -> None:
        self.selected["sonarr_root"] = self._current


class ArrSonarrQualityProfileSelect(ArrBaseSelect):
    key = "arr_sonarr_quality_profile"

    @property
    def name(self) -> str:
        return "Hassarr Sonarr Quality Profile"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-arr-sonarr-quality-profile"

    @property
    def icon(self) -> str:
        return "mdi:quality-high"

    async def _refresh(self) -> None:
        sonarr = self.hass.data[DOMAIN][self.entry.entry_id]["sonarr"]
        profs = []
        try:
            profs = await sonarr.list_quality_profiles()
        except Exception:  # noqa: BLE001
            profs = []
        self._label_to_value = {p.get("name"): int(p.get("id")) for p in profs}
        cur = self.selected.get("sonarr_quality_profile_id")
        if cur is not None:
            self._current = int(cur)
        else:
            self._current = next(iter(self._label_to_value.values()), None)
        self.selected["sonarr_quality_profile_id"] = self._current

    async def _handle_changed(self) -> None:
        self.selected["sonarr_quality_profile_id"] = self._current


class DefaultTvSeasonsSelect(SelectEntity):
    key = "default_tv_seasons"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_should_poll = False
        # labels -> values
        self._label_to_value: dict[str, str] = {
            "Season 1": "season1",
            "All Seasons": "all",
        }
        self._current: str | None = None

    @property
    def name(self) -> str:
        return "Hassarr Default TV Seasons"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}-default-tv-seasons"

    @property
    def icon(self) -> str:
        return "mdi:timeline-clock"

    @property
    def options(self) -> list[str]:  # type: ignore[override]
        return list(self._label_to_value.keys())

    @property
    def current_option(self) -> str | None:  # type: ignore[override]
        if self._current is None:
            return None
        for label, value in self._label_to_value.items():
            if value == self._current:
                return label
        return None

    async def async_select_option(self, option: str) -> None:  # type: ignore[override]
        if option not in self._label_to_value:
            raise ValueError("invalid_option")
        self._current = self._label_to_value[option]
        # Persist in runtime store for service usage
        self.hass.data[DOMAIN][self.entry.entry_id]["default_tv_seasons_mode"] = self._current
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        # Initialize from runtime store or entry defaults
        store = self.hass.data[DOMAIN][self.entry.entry_id]
        val = store.get("default_tv_seasons_mode")
        if not val:
            # fallback to saved defaults in config
            from .const import CONF_DEFAULT_TV_SEASONS
            val = self.entry.options.get(CONF_DEFAULT_TV_SEASONS) or self.entry.data.get(CONF_DEFAULT_TV_SEASONS, "season1")
            store["default_tv_seasons_mode"] = val
        self._current = val

    @property
    def device_info(self) -> DeviceInfo:
        # Group under the entry's device, with backend-specific naming
        store = self.hass.data[DOMAIN][self.entry.entry_id]
        backend = store.get(STORAGE_BACKEND)
        name = "Hassarr (Overseerr)" if backend == "overseerr" else "Hassarr (Sonarr/Radarr)"
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=name,
            manufacturer="Hassarr",
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    store = hass.data[DOMAIN][entry.entry_id]
    backend = store.get(STORAGE_BACKEND)
    if backend == "arr":
        arr_sel = store.setdefault(
            "arr_selected",
            {
                "radarr_root": None,
                "radarr_quality_profile_id": None,
                "sonarr_root": None,
                "sonarr_quality_profile_id": None,
            },
        )
        entities = [
            ArrRadarrRootSelect(hass, entry, arr_sel),
            ArrRadarrQualityProfileSelect(hass, entry, arr_sel),
            ArrSonarrRootSelect(hass, entry, arr_sel),
            ArrSonarrQualityProfileSelect(hass, entry, arr_sel),
            DefaultTvSeasonsSelect(hass, entry),
        ]
        async_add_entities(entities, True)
        return

    # Default to Overseerr branch
    client: OverseerrClient = store[STORAGE_CLIENT]
    selected = store.setdefault(
        "ovsr_selected",
        {
            "radarr_server_id": None,
            "sonarr_server_id": None,
            "movie_profile_id": None,
            "tv_profile_id": None,
        },
    )
    registry: dict[str, BaseOvsrSelect] = {}
    store.setdefault("select_entities", registry)
    radarr_server = RadarrServerSelect(hass, entry, client, selected, registry)
    sonarr_server = SonarrServerSelect(hass, entry, client, selected, registry)
    movie_profile = MovieProfileSelect(hass, entry, client, selected, registry)
    tv_profile = TvProfileSelect(hass, entry, client, selected, registry)
    user_select = OverseerrUserSelect(hass, entry, client, selected, registry)
    registry[radarr_server.key] = radarr_server
    registry[sonarr_server.key] = sonarr_server
    registry[movie_profile.key] = movie_profile
    registry[tv_profile.key] = tv_profile
    registry[user_select.key] = user_select
    async_add_entities([radarr_server, sonarr_server, movie_profile, tv_profile, user_select, DefaultTvSeasonsSelect(hass, entry)], True)
