from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, PLATFORMS
from .api import LauntelClient, LauntelService

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session: ClientSession = async_get_clientsession(hass)
    client = LauntelClient(session, entry.data["username"], entry.data["password"])

    service_id: int = entry.data["service_id"]
    avcid: str = entry.data["avcid"]
    user_id: str = entry.data["user_id"]

    # Polling intervals
    NORMAL_INTERVAL = timedelta(hours=6)
    CHANGE_POLL_INTERVAL = timedelta(minutes=1)

    # Keep last known service to handle transient portal states
    previous_service: Optional[LauntelService] = None

    async def _async_update() -> dict[str, Any]:
        nonlocal previous_service

        # Defaults for safe state
        svc: Optional[LauntelService] = None
        change_in_progress = False
        options: list[str] = []
        label_to_psid: dict[str, int] = {}
        current_label: Optional[str] = None
        locid: Optional[str] = None
        plans_mapping: dict[int, dict[str, object]] = {}

        try:
            services = await client.async_get_services()
            svc = next((s for s in services if s.service_id == service_id), None)

            if not svc:
                # Temporarily missing: fall back and treat as changing
                if previous_service is not None:
                    _LOGGER.debug("Service %s missing this cycle; using previous and marking change-in-progress", service_id)
                    svc = previous_service
                    change_in_progress = True
                else:
                    _LOGGER.debug("Service %s missing and no previous; creating placeholder as change-in-progress", service_id)
                    svc = LauntelService(
                        title=entry.title or f"Launtel {service_id}",
                        service_id=service_id,
                        avcid=avcid,
                        user_id=user_id,
                        speed_label=None,
                        change_in_progress=True,
                    )
                    change_in_progress = True
            else:
                change_in_progress = bool(svc.change_in_progress)

            # When not changing, try to fetch plan options; otherwise skip
            if not change_in_progress:
                try:
                    options, label_to_psid, current_label, locid, plans_mapping = await client.async_get_plan_options(avcid)
                    # If modify page unusable, treat as changing
                    if (not options and not current_label) or (locid is None):
                        _LOGGER.debug(
                            "Modify page unusable (options=%s, current_label=%s, locid=%s); treating as change-in-progress",
                            bool(options), current_label, locid,
                        )
                        change_in_progress = True
                        current_label = current_label or svc.speed_label
                        options, label_to_psid, locid, plans_mapping = [], {}, None, {}
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Plan options fetch failed: %s; treating as change-in-progress", err)
                    change_in_progress = True
                    current_label = svc.speed_label
            else:
                current_label = svc.speed_label

        except Exception as err:  # Broad portal error — keep entities up with safe state
            _LOGGER.debug("Services fetch error: %s; keeping previous state and marking change-in-progress", err)
            if previous_service is not None:
                svc = previous_service
            else:
                svc = LauntelService(
                    title=entry.title or f"Launtel {service_id}",
                    service_id=service_id,
                    avcid=avcid,
                    user_id=user_id,
                    speed_label=None,
                    change_in_progress=True,
                )
            change_in_progress = True
            # Note: we don't raise UpdateFailed to avoid error spam during transitions

        # Package data for entities
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
            "service_speed_label": svc.speed_label if svc else None,
        }

        # Remember last known good service card
        if svc is not None:
            previous_service = svc

        # Adjust polling dynamically
        try:
            if change_in_progress and coordinator.update_interval != CHANGE_POLL_INTERVAL:
                coordinator.update_interval = CHANGE_POLL_INTERVAL
            elif not change_in_progress and coordinator.update_interval != NORMAL_INTERVAL:
                coordinator.update_interval = NORMAL_INTERVAL
        except NameError:
            pass

        return data

    coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name=f"Launtel {service_id}",
        update_method=_async_update,
        update_interval=NORMAL_INTERVAL,
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
