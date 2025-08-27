from __future__ import annotations

import logging
from typing import Any, Optional

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_SERVICE_ID
from .api import LauntelClient, LauntelService


class LauntelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._username: Optional[str] = None
        self._password: Optional[str] = None
        self._services: list[LauntelService] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            try:
                session: ClientSession = async_get_clientsession(self.hass)
                client = LauntelClient(session, self._username, self._password)
                await client.async_login()
                self._services = await client.async_get_services()
                if not self._services:
                    errors["base"] = "no_services"
                else:
                    return await self.async_step_select_service()
            except Exception as e:
                logging.critical(e)
                errors["base"] = "auth"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_select_service(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if not self._services:
            return self.async_abort(reason="unknown")

        choices_ids = [s.service_id for s in self._services]

        if user_input is not None:
            service_id: int = user_input[CONF_SERVICE_ID]
            svc = next((s for s in self._services if s.service_id == service_id), None)
            if not svc:
                errors["base"] = "no_services"
            else:
                assert self._username is not None and self._password is not None
                # Ensure unique entry per account+service
                unique_id = f"{self._username}:{svc.service_id}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=svc.title,
                    data={
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                        CONF_SERVICE_ID: svc.service_id,
                        "avcid": svc.avcid,
                        "user_id": svc.user_id,
                    },
                )

        data_schema = vol.Schema({vol.Required(CONF_SERVICE_ID): vol.In(choices_ids)})
        return self.async_show_form(step_id="select_service", data_schema=data_schema, errors=errors)