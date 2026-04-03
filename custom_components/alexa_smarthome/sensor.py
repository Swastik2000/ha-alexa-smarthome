"""Sensor platform for Alexa Smart Home.

Creates separate sensor entities for each sensor capability of a device:
temperature, humidity, carbon monoxide level, and air quality index.

Covers AIR_QUALITY_MONITOR and ALEXA_VOICE_ENABLED device types, and also
exposes temperature/humidity from THERMOSTAT devices.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FEATURE_RANGE,
    FEATURE_TEMPERATURE_SENSOR,
    RANGE_FEATURE_AIR_QUALITY_NAMES,
    RANGE_FEATURE_CO_NAMES,
    RANGE_FEATURE_HUMIDITY_NAMES,
    SENSOR_TYPE_AIR_QUALITY,
    SENSOR_TYPE_CO,
    SENSOR_TYPE_HUMIDITY,
    SENSOR_TYPE_TEMPERATURE,
    TEMP_SCALE_FAHRENHEIT,
)
from .coordinator import AlexaDataUpdateCoordinator
from .entity import AlexaSmartHomeEntity
from .models import CapabilityState, SmartHomeDevice

_LOGGER = logging.getLogger(__name__)

# Device types that may expose sensor readings
_SENSOR_DEVICE_TYPES = {"AIR_QUALITY_MONITOR", "ALEXA_VOICE_ENABLED", "THERMOSTAT"}


def _celsius_from_alexa(value_obj: Any) -> float | None:
    """Convert Alexa temperature object to Celsius."""
    if not isinstance(value_obj, dict):
        return None
    raw = value_obj.get("value")
    scale = (value_obj.get("scale") or "").upper()
    if not isinstance(raw, (int, float)):
        return None
    if scale == TEMP_SCALE_FAHRENHEIT:
        return (raw - 32) * 5 / 9
    return float(raw)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa sensor entities from a config entry."""
    coordinator: AlexaDataUpdateCoordinator = entry.runtime_data

    entities: list[AlexaSensorEntity] = []
    for device in coordinator.devices:
        if device.device_type not in _SENSOR_DEVICE_TYPES:
            continue

        # Always create a temperature sensor for devices with temperatureSensor
        states = coordinator.data.get(device.endpoint_id, []) if coordinator.data else []
        has_temp = any(s.feature_name == FEATURE_TEMPERATURE_SENSOR for s in states)
        if has_temp:
            entities.append(
                AlexaSensorEntity(coordinator, device, SENSOR_TYPE_TEMPERATURE)
            )

        # Create range-based sensors based on the device's range features
        for rf in device.range_features:
            fn = rf.friendly_name
            if fn in RANGE_FEATURE_HUMIDITY_NAMES:
                entities.append(
                    AlexaSensorEntity(
                        coordinator, device, SENSOR_TYPE_HUMIDITY,
                        range_instance=rf.instance, range_name=fn,
                    )
                )
            elif fn in RANGE_FEATURE_CO_NAMES:
                entities.append(
                    AlexaSensorEntity(
                        coordinator, device, SENSOR_TYPE_CO,
                        range_instance=rf.instance, range_name=fn,
                    )
                )
            elif fn in RANGE_FEATURE_AIR_QUALITY_NAMES:
                entities.append(
                    AlexaSensorEntity(
                        coordinator, device, SENSOR_TYPE_AIR_QUALITY,
                        range_instance=rf.instance, range_name=fn,
                    )
                )

    async_add_entities(entities)


class AlexaSensorEntity(AlexaSmartHomeEntity, SensorEntity):
    """Represents a single sensor reading from an Alexa device.

    Multiple AlexaSensorEntity instances can be created per physical device —
    one for each distinct sensor capability (temperature, humidity, CO, etc.).
    """

    def __init__(
        self,
        coordinator: AlexaDataUpdateCoordinator,
        device: SmartHomeDevice,
        sensor_type: str,
        range_instance: str | None = None,
        range_name: str | None = None,
    ) -> None:
        super().__init__(coordinator, device)
        self._sensor_type = sensor_type
        self._range_instance = range_instance
        self._range_name = range_name

        # Make unique_id incorporate the sensor type to avoid collisions when
        # a single device produces multiple sensor entities.
        self._attr_unique_id = f"{device.unique_id}_{sensor_type}"
        if range_instance:
            self._attr_unique_id = f"{self._attr_unique_id}_{range_instance}"

        self._configure_for_type(sensor_type)

    def _configure_for_type(self, sensor_type: str) -> None:
        """Set HA sensor attributes based on the sensor type."""
        if sensor_type == SENSOR_TYPE_TEMPERATURE:
            self._attr_name = f"{self._device.display_name} Temperature"
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif sensor_type == SENSOR_TYPE_HUMIDITY:
            self._attr_name = f"{self._device.display_name} Humidity"
            self._attr_device_class = SensorDeviceClass.HUMIDITY
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif sensor_type == SENSOR_TYPE_CO:
            self._attr_name = f"{self._device.display_name} Carbon Monoxide"
            self._attr_device_class = SensorDeviceClass.CO
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION
        elif sensor_type == SENSOR_TYPE_AIR_QUALITY:
            self._attr_name = f"{self._device.display_name} Air Quality"
            self._attr_device_class = SensorDeviceClass.AQI
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = None
        else:
            self._attr_name = f"{self._device.display_name} Sensor"
            self._attr_device_class = None
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = None

    @property
    def native_value(self) -> float | str | None:
        """Return the sensor reading."""
        if self._sensor_type == SENSOR_TYPE_TEMPERATURE:
            state = self._get_state(FEATURE_TEMPERATURE_SENSOR)
            if state is None:
                return None
            return _celsius_from_alexa(state.value)

        # Range-based sensors (humidity, CO, air quality)
        if self._range_instance:
            state = self._get_state(
                FEATURE_RANGE, instance=self._range_instance
            )
            if state is None or not isinstance(state.value, (int, float)):
                return None
            return float(state.value)

        return None
