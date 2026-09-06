from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.const import CURRENCY_DOLLAR, UnitOfInformation, UnitOfTime

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    usage_coordinator = data["usage_coordinator"]

    entities = [
        LauntelCurrentPlanSensor(coordinator, entry),
        LauntelBalanceSensor(coordinator, entry),
        LauntelEstimatedDaysRemainingSensor(coordinator, entry),
        LauntelUsageTodaySensor(usage_coordinator, entry),
        LauntelUsageTodayDownloadSensor(usage_coordinator, entry),
        LauntelUsageTodayUploadSensor(usage_coordinator, entry),
        LauntelUsageMonthSensor(usage_coordinator, entry),
    ]
    async_add_entities(entities)


class LauntelCurrentPlanSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        svc = coordinator.data.get("service")
        title = svc.title if svc else entry.title
        self._attr_name = f"{title} plan"
        self._attr_unique_id = f"{entry.data['service_id']}_current_plan"

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
    def native_value(self) -> str | None:
        if self.coordinator.data.get("change_in_progress"):
            return "Change in progress"
        return self.coordinator.data.get("current_label")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        current_label = data.get("current_label")
        label_to_psid = data.get("label_to_psid", {})
        plans_mapping = data.get("plans_mapping", {})
        current_psid = label_to_psid.get(current_label) if current_label else None
        current_meta = plans_mapping.get(current_psid) if current_psid is not None else None

        # Convert mapping keys to strings for HA attribute compatibility
        plans_serializable: dict[str, Any] = {
            str(k): v for k, v in plans_mapping.items()
        }
        attrs: dict[str, Any] = {
            "change_in_progress": data.get("change_in_progress", False),
            "service_speed_label": data.get("service_speed_label"),
            "current_psid": current_psid,
            "current_price_per_day": current_meta.get("price_per_day") if current_meta else None,
            "current_unlimited": current_meta.get("unlimited") if current_meta else None,
            "current_speed": current_meta.get("speed") if current_meta else None,
            "options": list(data.get("options", [])),
            "plans": plans_serializable,
        }
        return attrs


class LauntelBalanceSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:currency-usd"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_DOLLAR
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        svc = coordinator.data.get("service")
        title = svc.title if svc else entry.title
        self._attr_name = f"{title} balance"
        self._attr_unique_id = f"{entry.data['service_id']}_account_balance"

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
    def native_value(self) -> float | None:
        return self.coordinator.data.get("account_balance")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        balance = self.coordinator.data.get("account_balance")
        attrs: dict[str, Any] = {
            "last_updated": self.coordinator.last_update_success,
        }

        if balance is not None:
            attrs.update({
                "balance_status": "credit" if balance >= 0 else "debt",
                "formatted_balance": f"${abs(balance):.2f}",
            })

        return attrs


class LauntelEstimatedDaysRemainingSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        svc = coordinator.data.get("service")
        title = svc.title if svc else entry.title
        self._attr_name = f"{title} estimated days remaining"
        self._attr_unique_id = f"{entry.data['service_id']}_estimated_days_remaining"

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
    def native_value(self) -> int | None:
        return self.coordinator.data.get("estimated_days_remaining")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        days_remaining = self.coordinator.data.get("estimated_days_remaining")
        attrs: dict[str, Any] = {
            "last_updated": self.coordinator.last_update_success,
        }

        if days_remaining is not None:
            attrs.update({
                "status": "low" if days_remaining < 7 else "normal",
                "weeks_remaining": round(days_remaining / 7, 1),
            })

        return attrs


class _LauntelUsageBase(CoordinatorEntity, SensorEntity):
    """Shared config for usage sensors (backed by the usage coordinator)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._entry.data["service_id"]))},
            name=self._entry.title,
            manufacturer="Launtel",
            model="Internet Service",
        )


class LauntelUsageTodaySensor(_LauntelUsageBase):
    """Total data used today (download + upload + free-night, in GB)."""

    _attr_icon = "mdi:swap-vertical"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = f"{entry.title} usage today"
        self._attr_unique_id = f"{entry.data['service_id']}_usage_today"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        return data.get("today_gb")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "download_gb": data.get("download_gb"),
            "upload_gb": data.get("upload_gb"),
            "night_free_gb": data.get("night_free_gb"),
        }


class LauntelUsageTodayDownloadSensor(_LauntelUsageBase):
    """Data downloaded today (GB)."""

    _attr_icon = "mdi:download-network"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = f"{entry.title} download today"
        self._attr_unique_id = f"{entry.data['service_id']}_usage_today_download"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        return data.get("download_gb")


class LauntelUsageTodayUploadSensor(_LauntelUsageBase):
    """Data uploaded today (GB)."""

    _attr_icon = "mdi:upload-network"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = f"{entry.title} upload today"
        self._attr_unique_id = f"{entry.data['service_id']}_usage_today_upload"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        return data.get("upload_gb")


class LauntelUsageMonthSensor(_LauntelUsageBase):
    """Month-to-date total data usage (GB), as reported by the portal."""

    _attr_icon = "mdi:calendar-month"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = f"{entry.title} usage this month"
        self._attr_unique_id = f"{entry.data['service_id']}_usage_month"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        return data.get("month_to_date_gb")
