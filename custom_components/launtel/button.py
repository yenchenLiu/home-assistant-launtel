from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = [
        LauntelRefreshButton(coordinator, entry),
    ]
    async_add_entities(entities)


class LauntelRefreshButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        svc = coordinator.data.get("service")
        title = svc.title if svc else entry.title
        self._attr_name = f"{title} refresh"
        self._attr_unique_id = f"{entry.data['service_id']}_refresh"

    @property
    def device_info(self) -> DeviceInfo:
        svc = self.coordinator.data.get("service")
        name = svc.title if svc else self._entry.title
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._entry.data["service_id"]))},
            name=name,
            manufacturer="Launtel",
            model="Internet Service",
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.async_request_refresh()