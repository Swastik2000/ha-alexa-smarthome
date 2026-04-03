"""DataUpdateCoordinator for the Alexa Smart Home integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AlexaApiClient, AlexaApiError, AlexaAuthError
from .const import (
    CONF_CACHE_TTL,
    CONF_EXCLUDE_DEVICES,
    CONF_INCLUDE_DEVICES,
    DEFAULT_CACHE_TTL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    SUPPORTED_DEVICE_TYPES,
)
from .device_store import DeviceStore
from .models import CapabilityState, SmartHomeDevice

_LOGGER = logging.getLogger(__name__)

# Map device_type → query type string used by AlexaApiClient.get_device_states()
_DEVICE_QUERY_MAP: dict[str, str] = {
    "LIGHT": "light",
    "SWITCH": "power",
    "SMARTPLUG": "power",
    "FAN": "power",
    "SMARTLOCK": "lock",
    "THERMOSTAT": "thermostat",
    "AIR_QUALITY_MONITOR": "air_quality",
    "ALEXA_VOICE_ENABLED": "temp_sensor",
    "VACUUM_CLEANER": "power",
    "GAME_CONSOLE": "power",
    "AIR_FRESHENER": "power",
}


class AlexaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, list[CapabilityState]]]):
    """Coordinator that periodically polls Alexa for device states.

    On first setup, it discovers all devices and populates the device list.
    Subsequent updates fetch current states for all known devices.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: AlexaApiClient,
    ) -> None:
        self.api = api
        self.entry = entry
        self.devices: list[SmartHomeDevice] = []
        self.device_store = DeviceStore(
            cache_ttl=entry.data.get(CONF_CACHE_TTL, DEFAULT_CACHE_TTL)
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )

    async def async_setup(self) -> None:
        """Discover devices and populate the devices list.

        Should be called once during config entry setup before the first
        coordinator refresh.
        """
        # Refresh CSRF token so mutations work (queries can work without it,
        # but state-changing operations require a fresh token).
        await self.api.refresh_csrf()

        _LOGGER.debug("Discovering Alexa smart home devices")
        try:
            all_devices = await self.api.get_devices()
        except AlexaAuthError as err:
            _LOGGER.error("Authentication error during device discovery: %s", err)
            raise
        except AlexaApiError as err:
            _LOGGER.error("API error during device discovery: %s", err)
            raise

        include_list: list[str] = [
            d.strip()
            for d in (self.entry.data.get(CONF_INCLUDE_DEVICES) or [])
        ]
        exclude_list: list[str] = [
            d.strip()
            for d in (self.entry.data.get(CONF_EXCLUDE_DEVICES) or [])
        ]

        filtered: list[SmartHomeDevice] = []
        for device in all_devices:
            # Only include device types we know how to map to HA platforms
            if device.device_type not in SUPPORTED_DEVICE_TYPES:
                _LOGGER.debug(
                    "Skipping unsupported device type %s: %s",
                    device.device_type,
                    device.display_name,
                )
                continue

            name = device.display_name.strip()
            if include_list:
                if name not in include_list:
                    continue
            elif exclude_list:
                if name in exclude_list:
                    continue

            filtered.append(device)

        self.devices = filtered
        _LOGGER.info(
            "Discovered %d Alexa smart home device(s)",
            len(self.devices),
        )
        for device in self.devices:
            _LOGGER.debug(
                "  - %s (%s) ops=%s",
                device.display_name,
                device.device_type,
                device.supported_operations,
            )

    def get_device(self, endpoint_id: str) -> SmartHomeDevice | None:
        """Return the device with the given endpoint_id, or None."""
        for device in self.devices:
            if device.endpoint_id == endpoint_id:
                return device
        return None

    async def _async_update_data(self) -> dict[str, list[CapabilityState]]:
        """Fetch current states for all known devices.

        Returns a dict mapping endpoint_id -> list[CapabilityState].
        """
        if not self.devices:
            return {}

        results: dict[str, list[CapabilityState]] = {}

        for device in self.devices:
            if not device.enabled:
                _LOGGER.debug("Skipping disabled device: %s", device.display_name)
                continue

            query_type = _DEVICE_QUERY_MAP.get(device.device_type, "power")
            try:
                states = await self.api.get_device_states(device, query_type=query_type)
                results[device.endpoint_id] = states
                self.device_store.update_states(device.endpoint_id, states)
            except AlexaAuthError as err:
                _LOGGER.error(
                    "Authentication error fetching state for %s: %s",
                    device.display_name,
                    err,
                )
                # Propagate auth errors immediately — caller needs to re-authenticate
                raise UpdateFailed(f"Authentication error: {err}") from err
            except AlexaApiError as err:
                _LOGGER.warning(
                    "Failed to fetch state for %s: %s",
                    device.display_name,
                    err,
                )
                # Fall back to stale cache so the device doesn't go unavailable
                # on a transient network error
                cached = self.device_store.get_states(device.endpoint_id)
                if cached:
                    results[device.endpoint_id] = cached

        return results
