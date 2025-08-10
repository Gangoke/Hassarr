from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, STORAGE_BACKEND


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    backend = hass.data[DOMAIN][entry.entry_id][STORAGE_BACKEND]
    async_add_entities([BackendInfoSensor(entry, backend)])


class BackendInfoSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, backend: str) -> None:
        self._entry = entry
        self._backend = backend
        self._attr_unique_id = f"{entry.entry_id}-backend"
        self._attr_name = "Hassarr Backend"
        self._attr_icon = "mdi:information-outline"

    @property
    def native_value(self) -> str | None:
        return self._backend

    async def async_update(self) -> None:
        # static informational
        return

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Hassarr (Overseerr)" if self._backend == "overseerr" else "Hassarr (Sonarr/Radarr)",
            manufacturer="Hassarr",
        )
