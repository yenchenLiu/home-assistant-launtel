from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, PLATFORMS
from .api import LauntelClient, LauntelService

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session: ClientSession = async_get_clientsession(hass)
    client = LauntelClient(session, entry.data["username"], entry.data["password"])

    service_id: int = entry.data["service_id"]
    avcid: str = entry.data["avcid"]
    user_id: str = entry.data["user_id"]

    async def _async_update() -> dict[str, Any]:
        try:
            services = await client.async_get_services()
            svc: Optional[LauntelService] = next((s for s in services if s.service_id == service_id), None)
            if not svc:
                raise UpdateFailed("Service not found for the configured service_id")

            change_in_progress = bool(svc.change_in_progress)
            options: list[str] = []
            label_to_psid: dict[str, int] = {}
            current_label: Optional[str] = None
            locid: Optional[str] = None
            plans_mapping: dict[int, dict[str, object]] = {}

            if change_in_progress:
                # Avoid fetching the plan page; it may be unavailable during changes
                current_label = svc.speed_label  # keep sensor meaningful
            else:
                options, label_to_psid, current_label, locid, plans_mapping = await client.async_get_plan_options(avcid)
                # current_label is derived from psid on the plan page by the API; no speed-based fallback needed

            data: dict[str, Any] = {
                "service": svc,
                "options": options,
                "label_to_psid": label_to_psid,
                "current_label": current_label,
                "locid": locid,
                "user_id": user_id,
                "service_id": service_id,
                "avcid": avcid,
                "plans_mapping": plans_mapping,
                "change_in_progress": change_in_progress,
                "service_speed_label": svc.speed_label,
            }
            return data
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name=f"Launtel {service_id}",
        update_method=_async_update,
        update_interval=timedelta(minutes=10),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
