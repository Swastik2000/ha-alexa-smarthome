"""Switch platform for Alexa Smart Home.

Covers SWITCH (non-dimmable), SMARTPLUG, VACUUM_CLEANER, GAME_CONSOLE, and
AIR_FRESHENER device types.  Dimmable SWITCH devices are handled by light.py.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FEATURE_POWER,
    OPERATION_SET_BRIGHTNESS,
    OPERATION_TURN_OFF,
    OPERATION_TURN_ON,
)
from .coordinator import AlexaDataUpdateCoordinator
from .entity import AlexaSmartHomeEntity
from .models import SmartHomeDevice

_LOGGER = logging.getLogger(__name__)

# Device types routed to this platform
_SWITCH_DEVICE_TYPES = {
    "SWITCH",
    "SMARTPLUG",
    "VACUUM_CLEANER",
    "GAME_CONSOLE",
    "AIR_FRESHENER",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa switch entities from a config entry."""
    coordinator: AlexaDataUpdateCoordinator = entry.runtime_data

    entities: list[AlexaSwitchEntity] = []
    for device in coordinator.devices:
        if device.device_type not in _SWITCH_DEVICE_TYPES:
            continue
        # Dimmable switches (with setBrightness) become light entities instead
        if (
            device.device_type == "SWITCH"
            and OPERATION_SET_BRIGHTNESS in device.supported_operations
        ):
            continue
        entities.append(AlexaSwitchEntity(coordinator, device))

    async_add_entities(entities)


class AlexaSwitchEntity(AlexaSmartHomeEntity, SwitchEntity):
    """Represents an Alexa switch/plug/outlet as a HA SwitchEntity."""

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        state = self._get_state(FEATURE_POWER)
        if state is None:
            return None
        return state.value == "ON"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._set_device_state(FEATURE_POWER, OPERATION_TURN_ON)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._set_device_state(FEATURE_POWER, OPERATION_TURN_OFF)
        await self.coordinator.async_request_refresh()
