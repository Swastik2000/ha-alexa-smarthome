"""Lock platform for Alexa Smart Home.

Ported from src/accessory/lock-accessory.ts.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FEATURE_LOCK,
    LOCK_STATE_JAMMED,
    LOCK_STATE_LOCKED,
    LOCK_STATE_UNLOCKED,
    OPERATION_LOCK,
    OPERATION_UNLOCK,
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
    """Set up Alexa lock entities from a config entry."""
    coordinator: AlexaDataUpdateCoordinator = entry.runtime_data

    entities = [
        AlexaLockEntity(coordinator, device)
        for device in coordinator.devices
        if device.device_type == "SMARTLOCK"
    ]
    async_add_entities(entities)


class AlexaLockEntity(AlexaSmartHomeEntity, LockEntity):
    """Represents an Alexa SMARTLOCK as a HA LockEntity.

    Mirrors lock-accessory.ts handleCurrentStateGet / handleTargetStateSet.
    """

    @property
    def _lock_state_value(self) -> str | None:
        """Return the raw Alexa lock state string."""
        state = self._get_state(FEATURE_LOCK, name="lockState")
        if state is None or not isinstance(state.value, str):
            return None
        return state.value

    @property
    def is_locked(self) -> bool | None:
        """Return True if the lock is locked."""
        val = self._lock_state_value
        if val is None:
            return None
        return val == LOCK_STATE_LOCKED

    @property
    def is_locking(self) -> bool:
        """Return True if the lock is in the process of locking (not tracked)."""
        return False

    @property
    def is_unlocking(self) -> bool:
        """Return True if the lock is in the process of unlocking (not tracked)."""
        return False

    @property
    def is_jammed(self) -> bool | None:
        """Return True if the lock is jammed."""
        val = self._lock_state_value
        if val is None:
            return None
        return val == LOCK_STATE_JAMMED

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the device."""
        await self._set_device_state(FEATURE_LOCK, OPERATION_LOCK)
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the device."""
        await self._set_device_state(FEATURE_LOCK, OPERATION_UNLOCK)
        await self.coordinator.async_request_refresh()
