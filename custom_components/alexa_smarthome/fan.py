"""Fan platform for Alexa Smart Home."""
from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FEATURE_BRIGHTNESS,
    FEATURE_POWER,
    OPERATION_SET_BRIGHTNESS,
    OPERATION_TURN_OFF,
    OPERATION_TURN_ON,
)
from .coordinator import AlexaDataUpdateCoordinator
from .entity import AlexaSmartHomeEntity
from .models import SmartHomeDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa fan entities from a config entry."""
    coordinator: AlexaDataUpdateCoordinator = entry.runtime_data

    entities = [
        AlexaFanEntity(coordinator, device)
        for device in coordinator.devices
        if device.device_type == "FAN"
    ]
    async_add_entities(entities)


class AlexaFanEntity(AlexaSmartHomeEntity, FanEntity):
    """Represents an Alexa FAN device as a HA FanEntity.

    Supports on/off and, if the device supports setBrightness (Alexa uses the
    brightness feature for fan speed on some devices), percentage-based speed
    control.
    """

    def __init__(
        self,
        coordinator: AlexaDataUpdateCoordinator,
        device: SmartHomeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self._supports_operation(OPERATION_SET_BRIGHTNESS):
            features |= FanEntityFeature.SET_SPEED
        self._attr_supported_features = features

    @property
    def is_on(self) -> bool | None:
        """Return True if the fan is on."""
        state = self._get_state(FEATURE_POWER)
        if state is None:
            return None
        return state.value == "ON"

    @property
    def percentage(self) -> int | None:
        """Return the current speed as a percentage (0-100).

        Alexa encodes fan speed via the brightness feature on devices that
        support variable speed.
        """
        if not self._supports_operation(OPERATION_SET_BRIGHTNESS):
            return None
        state = self._get_state(FEATURE_BRIGHTNESS)
        if state is None or not isinstance(state.value, (int, float)):
            return None
        return round(float(state.value))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally setting speed."""
        await self._set_device_state(FEATURE_POWER, OPERATION_TURN_ON)
        if percentage is not None and self._supports_operation(OPERATION_SET_BRIGHTNESS):
            await self._set_device_state(
                FEATURE_BRIGHTNESS,
                OPERATION_SET_BRIGHTNESS,
                {"brightness": str(percentage)},
            )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self._set_device_state(FEATURE_POWER, OPERATION_TURN_OFF)
        await self.coordinator.async_request_refresh()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed percentage."""
        if percentage == 0:
            await self.async_turn_off()
            return
        if not self.is_on:
            await self._set_device_state(FEATURE_POWER, OPERATION_TURN_ON)
        await self._set_device_state(
            FEATURE_BRIGHTNESS,
            OPERATION_SET_BRIGHTNESS,
            {"brightness": str(percentage)},
        )
        await self.coordinator.async_request_refresh()
