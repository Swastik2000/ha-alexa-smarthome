"""Climate platform for Alexa Smart Home.

Ported from src/accessory/thermostat-accessory.ts.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FEATURE_TEMPERATURE_SENSOR,
    FEATURE_THERMOSTAT,
    OPERATION_SET_TARGET_SETPOINT,
    OPERATION_SET_THERMOSTAT_MODE,
    OPERATION_TURN_OFF,
    OPERATION_TURN_ON,
    TEMP_SCALE_CELSIUS,
    TEMP_SCALE_FAHRENHEIT,
    THERMOSTAT_MODE_AUTO,
    THERMOSTAT_MODE_COOL,
    THERMOSTAT_MODE_ECO,
    THERMOSTAT_MODE_HEAT,
    THERMOSTAT_MODE_OFF,
)
from .coordinator import AlexaDataUpdateCoordinator
from .entity import AlexaSmartHomeEntity
from .models import CapabilityState, SmartHomeDevice

_LOGGER = logging.getLogger(__name__)

# Alexa thermostat mode → HA HVACMode
_ALEXA_TO_HA_MODE: dict[str, HVACMode] = {
    THERMOSTAT_MODE_HEAT: HVACMode.HEAT,
    THERMOSTAT_MODE_COOL: HVACMode.COOL,
    THERMOSTAT_MODE_AUTO: HVACMode.AUTO,
    THERMOSTAT_MODE_ECO: HVACMode.AUTO,  # Map ECO to AUTO (closest HA mode)
    THERMOSTAT_MODE_OFF: HVACMode.OFF,
}

# HA HVACMode → Alexa thermostat mode
_HA_TO_ALEXA_MODE: dict[HVACMode, str] = {
    HVACMode.HEAT: THERMOSTAT_MODE_HEAT,
    HVACMode.COOL: THERMOSTAT_MODE_COOL,
    HVACMode.AUTO: THERMOSTAT_MODE_AUTO,
    HVACMode.OFF: THERMOSTAT_MODE_OFF,
}


def _celsius_from_alexa(value_obj: Any) -> float | None:
    """Convert an Alexa temperature object {'value': x, 'scale': 'CELSIUS'|'FAHRENHEIT'}
    to degrees Celsius.

    Mirrors mapAlexaTempToHomeKit() from temperature-mapper.ts.
    """
    if not isinstance(value_obj, dict):
        return None
    raw = value_obj.get("value")
    scale = (value_obj.get("scale") or "").upper()
    if not isinstance(raw, (int, float)):
        return None
    if scale == TEMP_SCALE_FAHRENHEIT:
        return (raw - 32) * 5 / 9
    return float(raw)


def _alexa_temp_from_celsius(celsius: float, scale: str) -> float:
    """Convert Celsius to the target Alexa scale.

    Mirrors mapHomeKitTempToAlexa() from temperature-mapper.ts.
    """
    if scale.upper() == TEMP_SCALE_FAHRENHEIT:
        return celsius * 9 / 5 + 32
    return celsius


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa climate entities from a config entry."""
    coordinator: AlexaDataUpdateCoordinator = entry.runtime_data

    entities = [
        AlexaClimateEntity(coordinator, device)
        for device in coordinator.devices
        if device.device_type == "THERMOSTAT"
    ]
    async_add_entities(entities)


class AlexaClimateEntity(AlexaSmartHomeEntity, ClimateEntity):
    """Represents an Alexa THERMOSTAT as a HA ClimateEntity.

    Mirrors thermostat-accessory.ts: supports current temp, target temp,
    heating/cooling setpoints (auto mode), thermostat mode, and humidity.
    """

    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.AUTO,
    ]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: AlexaDataUpdateCoordinator,
        device: SmartHomeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        self._attr_supported_features = features

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current HVAC mode."""
        state = self._get_state(FEATURE_THERMOSTAT, name="thermostatMode")
        if state is None or not isinstance(state.value, str):
            return None
        return _ALEXA_TO_HA_MODE.get(state.value.upper())

    @property
    def current_temperature(self) -> float | None:
        """Return current ambient temperature in Celsius."""
        state = self._get_state(FEATURE_TEMPERATURE_SENSOR)
        if state is None:
            return None
        return _celsius_from_alexa(state.value)

    @property
    def target_temperature(self) -> float | None:
        """Return target setpoint temperature in Celsius.

        In AUTO mode, returns the midpoint of heat/cool setpoints, matching
        the calculateTargetTemp() logic in thermostat-accessory.ts.
        """
        mode = self.hvac_mode
        if mode == HVACMode.AUTO:
            return self._auto_midpoint()

        state = self._get_state(FEATURE_THERMOSTAT, name="targetSetpoint")
        if state is None:
            return None
        return _celsius_from_alexa(state.value)

    @property
    def target_temperature_high(self) -> float | None:
        """Return cooling setpoint (upper) in Celsius."""
        state = self._get_state(FEATURE_THERMOSTAT, name="upperSetpoint")
        if state is None:
            return None
        return _celsius_from_alexa(state.value)

    @property
    def target_temperature_low(self) -> float | None:
        """Return heating setpoint (lower) in Celsius."""
        state = self._get_state(FEATURE_THERMOSTAT, name="lowerSetpoint")
        if state is None:
            return None
        return _celsius_from_alexa(state.value)

    def _auto_midpoint(self) -> float | None:
        """Calculate midpoint of heat + cool setpoints for AUTO mode display."""
        high = self.target_temperature_high
        low = self.target_temperature_low
        if high is None or low is None:
            return None
        return (high + low) / 2

    def _get_temp_scale(self) -> str:
        """Return the Alexa temperature scale from the current temp sensor state."""
        state = self._get_state(FEATURE_TEMPERATURE_SENSOR)
        if state is None or not isinstance(state.value, dict):
            return TEMP_SCALE_CELSIUS
        return (state.value.get("scale") or TEMP_SCALE_CELSIUS).upper()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the thermostat HVAC mode.

        Turning OFF via HVACMode.OFF maps to the power turnOff operation,
        mirroring handleTargetStateSet() in thermostat-accessory.ts.
        """
        if hvac_mode == HVACMode.OFF:
            if OPERATION_TURN_OFF in self._device.supported_operations:
                # Use the power feature to turn the thermostat off
                await self._set_device_state(FEATURE_POWER, OPERATION_TURN_OFF)
            else:
                await self._set_device_state(
                    FEATURE_THERMOSTAT,
                    OPERATION_SET_THERMOSTAT_MODE,
                    {"thermostatMode": THERMOSTAT_MODE_OFF},
                )
        else:
            alexa_mode = _HA_TO_ALEXA_MODE.get(hvac_mode, THERMOSTAT_MODE_AUTO)
            await self._set_device_state(
                FEATURE_THERMOSTAT,
                OPERATION_SET_THERMOSTAT_MODE,
                {"thermostatMode": alexa_mode},
            )
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature or heat/cool setpoints.

        Mirrors handleTargetTempSet / handleCoolTempSet / handleHeatTempSet in
        thermostat-accessory.ts.
        """
        scale = self._get_temp_scale()
        mode = self.hvac_mode

        if ATTR_TEMPERATURE in kwargs and mode != HVACMode.AUTO:
            celsius = kwargs[ATTR_TEMPERATURE]
            alexa_temp = _alexa_temp_from_celsius(celsius, scale)
            await self._set_device_state(
                FEATURE_THERMOSTAT,
                OPERATION_SET_TARGET_SETPOINT,
                {
                    "targetSetpoint": {
                        "value": str(alexa_temp),
                        "scale": scale,
                    }
                },
            )
        elif ATTR_TARGET_TEMP_HIGH in kwargs or ATTR_TARGET_TEMP_LOW in kwargs:
            high_c = kwargs.get(ATTR_TARGET_TEMP_HIGH, self.target_temperature_high)
            low_c = kwargs.get(ATTR_TARGET_TEMP_LOW, self.target_temperature_low)
            if high_c is not None and low_c is not None:
                high_alexa = _alexa_temp_from_celsius(high_c, scale)
                low_alexa = _alexa_temp_from_celsius(low_c, scale)
                await self._set_device_state(
                    FEATURE_THERMOSTAT,
                    OPERATION_SET_TARGET_SETPOINT,
                    {
                        "upperSetpoint": {"value": str(high_alexa), "scale": scale},
                        "lowerSetpoint": {"value": str(low_alexa), "scale": scale},
                    },
                )

        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn the thermostat on."""
        if OPERATION_TURN_ON in self._device.supported_operations:
            await self._set_device_state(FEATURE_POWER, OPERATION_TURN_ON)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn the thermostat off."""
        await self.async_set_hvac_mode(HVACMode.OFF)
