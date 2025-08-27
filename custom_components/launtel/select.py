from __future__ import annotations

from typing import Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    client = data["client"]

    entity = LauntelPlanSelect(coordinator, client, entry)
    async_add_entities([entity])


class LauntelPlanSelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, client, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._client = client
        self._entry = entry
        svc = coordinator.data.get("service")
        title = svc.title if svc else entry.title
        self._attr_name = f"{title} plan"
        self._attr_unique_id = f"{entry.data['service_id']}_plan_select"

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

    @property
    def available(self) -> bool:
        # Disable select while a change is in progress
        return not bool(self.coordinator.data.get("change_in_progress", False))

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.data.get("options", [])) if self.available else []

    @property
    def current_option(self) -> Optional[str]:
        return self.coordinator.data.get("current_label")

    async def async_select_option(self, option: str) -> None:
        if not self.available:
            raise HomeAssistantError("Plan change in progress; selection is temporarily disabled")
        mapping = self.coordinator.data.get("label_to_psid", {})
        psid = mapping.get(option)
        if psid is None:
            raise HomeAssistantError("Invalid plan option selected")
        user_id: str = self.coordinator.data.get("user_id")
        service_id: int = self.coordinator.data.get("service_id")
        avcid: str = self.coordinator.data.get("avcid")
        locid: Optional[str] = self.coordinator.data.get("locid")
        if not locid:
            await self.coordinator.async_request_refresh()
            locid = self.coordinator.data.get("locid")
            if not locid:
                raise HomeAssistantError("Unable to determine locid for plan change")
        await self._client.async_change_plan(user_id, psid, service_id, avcid, locid)
        await self.coordinator.async_request_refresh()
