"""Base entity class for Alexa Smart Home entities."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AlexaDataUpdateCoordinator
from .models import CapabilityState, SmartHomeDevice

_LOGGER = logging.getLogger(__name__)


class AlexaSmartHomeEntity(CoordinatorEntity[AlexaDataUpdateCoordinator]):
    """Base class for all Alexa Smart Home entities.

    Wraps a SmartHomeDevice and provides helpers for reading capability states
    from the coordinator's data and writing state changes via the API.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AlexaDataUpdateCoordinator,
        device: SmartHomeDevice,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = device.unique_id
        self._attr_name = None  # entity name comes from device name

    @property
    def device_info(self) -> DeviceInfo:
        """Return HA device registry info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.endpoint_id)},
            name=self._device.display_name,
            manufacturer=self._device.manufacturer,
            model=self._device.model,
            serial_number=self._device.serial_number,
        )

    @property
    def available(self) -> bool:
        """Return True if device is enabled and coordinator has data."""
        return (
            super().available
            and self._device.enabled
            and self.coordinator.data is not None
        )

    # ------------------------------------------------------------------
    # Helpers for reading state data
    # ------------------------------------------------------------------

    def _get_states(self) -> list[CapabilityState]:
        """Return the latest capability states for this device."""
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get(self._device.endpoint_id, [])

    def _get_state(
        self,
        feature_name: str,
        name: str | None = None,
        instance: str | None = None,
    ) -> CapabilityState | None:
        """Find a specific capability state by feature name and optional property name."""
        for state in self._get_states():
            if state.feature_name != feature_name:
                continue
            if name is not None and state.name != name:
                continue
            if instance is not None and state.instance != instance:
                continue
            return state
        return None

    def _supports_operation(self, operation: str) -> bool:
        """Return True if this device supports the given Alexa operation."""
        return operation in self._device.supported_operations

    # ------------------------------------------------------------------
    # Helpers for writing state changes
    # ------------------------------------------------------------------

    async def _set_device_state(
        self,
        feature_name: str,
        operation_name: str,
        payload: dict[str, Any] | None = None,
        optimistic_state: "CapabilityState | None" = None,
    ) -> None:
        """Execute a state mutation and update the local cache optimistically.

        After a successful API call the coordinator's cached data is updated
        in-place so that HA reflects the new state immediately without waiting
        for the next polling cycle.  ``async_write_ha_state()`` is called to
        push the change to the HA state machine straight away.

        Args:
            feature_name: The Alexa feature to mutate (e.g. ``"power"``).
            operation_name: The Alexa operation to invoke (e.g. ``"turnOn"``).
            payload: Optional payload dict for the GraphQL mutation.
            optimistic_state: If provided, write this CapabilityState into the
                coordinator data cache immediately after a successful API call.
        """
        await self.coordinator.api.set_device_state(
            self._device.endpoint_id,
            feature_name,
            operation_name,
            payload,
        )

        if optimistic_state is not None:
            # Update the device store so isCacheFresh() and fallback logic work
            self.coordinator.device_store.update_state_value(
                self._device.endpoint_id, optimistic_state
            )

            # Mirror the change into the coordinator's live data dict so that
            # HA property reads see the new value immediately.
            if self.coordinator.data is not None:
                device_states = self.coordinator.data.get(
                    self._device.endpoint_id, []
                )
                updated = False
                for cached in device_states:
                    if (
                        cached.feature_name == optimistic_state.feature_name
                        and (
                            optimistic_state.name is None
                            or cached.name == optimistic_state.name
                        )
                        and (
                            optimistic_state.instance is None
                            or cached.instance == optimistic_state.instance
                        )
                    ):
                        cached.value = optimistic_state.value
                        updated = True
                        break
                if not updated:
                    device_states.append(optimistic_state)
                    self.coordinator.data[self._device.endpoint_id] = device_states

            self.async_write_ha_state()
