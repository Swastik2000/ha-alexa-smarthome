"""Light platform for Alexa Smart Home.

Supports LIGHT devices and dimmable SWITCH devices with setBrightness support.
Capability mapping follows src/accessory/light-accessory.ts.
"""
from __future__ import annotations

import colorsys
import logging
import math
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FEATURE_BRIGHTNESS,
    FEATURE_COLOR,
    FEATURE_COLOR_TEMPERATURE,
    FEATURE_POWER,
    OPERATION_SET_BRIGHTNESS,
    OPERATION_SET_COLOR,
    OPERATION_SET_COLOR_TEMPERATURE,
    OPERATION_TURN_OFF,
    OPERATION_TURN_ON,
)
from .coordinator import AlexaDataUpdateCoordinator
from .entity import AlexaSmartHomeEntity
from .models import SmartHomeDevice

_LOGGER = logging.getLogger(__name__)

# Color temperature range supported by Alexa (in Kelvin)
_CT_MIN_K = 2000
_CT_MAX_K = 7142


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa light entities from a config entry."""
    coordinator: AlexaDataUpdateCoordinator = entry.runtime_data

    entities: list[AlexaLightEntity] = []
    for device in coordinator.devices:
        is_light = device.device_type == "LIGHT"
        is_dimmable_switch = (
            device.device_type == "SWITCH"
            and OPERATION_SET_BRIGHTNESS in device.supported_operations
        )
        if is_light or is_dimmable_switch:
            entities.append(AlexaLightEntity(coordinator, device))

    async_add_entities(entities)


class AlexaLightEntity(AlexaSmartHomeEntity, LightEntity):
    """Represents an Alexa light or dimmable switch as a HA LightEntity.

    Color modes are determined dynamically from the device's supported
    operations, mirroring the conditional service setup in light-accessory.ts.
    """

    def __init__(
        self,
        coordinator: AlexaDataUpdateCoordinator,
        device: SmartHomeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_name = None  # The device name is used as the entity name

        # Determine supported color modes based on device capabilities
        supported_modes: set[ColorMode] = set()
        if self._supports_operation(OPERATION_SET_COLOR):
            supported_modes.add(ColorMode.HS)
        if self._supports_operation(OPERATION_SET_COLOR_TEMPERATURE):
            supported_modes.add(ColorMode.COLOR_TEMP)
        if self._supports_operation(OPERATION_SET_BRIGHTNESS):
            supported_modes.add(ColorMode.BRIGHTNESS)
        if not supported_modes:
            supported_modes.add(ColorMode.ONOFF)

        self._attr_supported_color_modes = supported_modes

        # Set color temp range (Alexa range: ~2000 K – 7142 K)
        self._attr_min_color_temp_kelvin = _CT_MIN_K
        self._attr_max_color_temp_kelvin = _CT_MAX_K

    @property
    def color_mode(self) -> ColorMode:
        """Return the current color mode based on active state."""
        if ColorMode.HS in self._attr_supported_color_modes:
            color_state = self._get_state(FEATURE_COLOR)
            if color_state is not None:
                return ColorMode.HS
        if ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            ct_state = self._get_state(FEATURE_COLOR_TEMPERATURE)
            if ct_state is not None and ct_state.value is not None:
                return ColorMode.COLOR_TEMP
        if ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def is_on(self) -> bool | None:
        """Return True if the light is on."""
        state = self._get_state(FEATURE_POWER)
        if state is None:
            return None
        return state.value == "ON"

    @property
    def brightness(self) -> int | None:
        """Return current brightness scaled to 0-255."""
        state = self._get_state(FEATURE_BRIGHTNESS)
        if state is None or not isinstance(state.value, (int, float)):
            return None
        # Alexa uses 0-100; HA uses 0-255
        return round(state.value / 100 * 255)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return current color as (hue, saturation) in HA format.

        Alexa returns hue [0-360], saturation [0-1].
        HA expects hue [0-360], saturation [0-100].
        """
        state = self._get_state(FEATURE_COLOR)
        if state is None or not isinstance(state.value, dict):
            return None
        hue = state.value.get("hue")
        saturation = state.value.get("saturation")
        if not isinstance(hue, (int, float)) or not isinstance(saturation, (int, float)):
            return None
        return (float(hue), float(saturation) * 100)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return current color temperature in Kelvin."""
        state = self._get_state(FEATURE_COLOR_TEMPERATURE)
        if state is None or not isinstance(state.value, (int, float)):
            return None
        return round(state.value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally setting brightness/color/color temp."""
        if not self.is_on:
            await self._set_device_state(FEATURE_POWER, OPERATION_TURN_ON)

        if ATTR_BRIGHTNESS in kwargs and self._supports_operation(OPERATION_SET_BRIGHTNESS):
            # HA brightness is 0-255; Alexa expects 0-100
            alexa_brightness = round(kwargs[ATTR_BRIGHTNESS] / 255 * 100)
            await self._set_device_state(
                FEATURE_BRIGHTNESS,
                OPERATION_SET_BRIGHTNESS,
                {"brightness": str(alexa_brightness)},
            )

        if ATTR_HS_COLOR in kwargs and self._supports_operation(OPERATION_SET_COLOR):
            hue, saturation_pct = kwargs[ATTR_HS_COLOR]
            # Convert hue back to nearest named color (Alexa requires a color name
            # for setColor via the legacy REST API path, but the GraphQL path
            # accepts HSB values directly)
            saturation = saturation_pct / 100
            brightness = (self.brightness or 255) / 255
            await self._set_device_state(
                FEATURE_COLOR,
                OPERATION_SET_COLOR,
                {
                    "colorName": _hue_to_color_name(hue),
                },
            )

        if ATTR_COLOR_TEMP_KELVIN in kwargs and self._supports_operation(
            OPERATION_SET_COLOR_TEMPERATURE
        ):
            ct_kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            await self._set_device_state(
                FEATURE_COLOR_TEMPERATURE,
                OPERATION_SET_COLOR_TEMPERATURE,
                {"colorTemperatureInKelvin": ct_kelvin},
            )

        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._set_device_state(FEATURE_POWER, OPERATION_TURN_OFF)
        await self.coordinator.async_request_refresh()


# ---------------------------------------------------------------------------
# Color name mapping
# Ported from src/mapper/light-mapper.ts — maps a hue value to the nearest
# Alexa-recognised color name using the same color table.
# ---------------------------------------------------------------------------

_COLOR_NAMES: dict[str, tuple[float, float, float]] = {
    "medium_sea_green": (0x57, 0xFF, 0xA0),
    "dark_turquoise": (0x01, 0xFB, 0xFF),
    "sky_blue": (0x93, 0xE0, 0xFF),
    "old_lace": (0xFF, 0xF7, 0xE8),
    "light_salmon": (0xFF, 0xA0, 0x7A),
    "orange_red": (0xFF, 0x44, 0x00),
    "lime_green": (0x40, 0xFF, 0x40),
    "deep_pink": (0xFF, 0x14, 0x91),
    "hot_pink": (0xFF, 0x68, 0xB6),
    "dodger_blue": (0x1E, 0x8F, 0xFF),
    "goldenrod": (0xFF, 0xC2, 0x27),
    "red": (0xFF, 0x00, 0x00),
    "blue": (0x41, 0x00, 0xFF),
    "fuchsia": (0xFF, 0x00, 0xFF),
    "green_yellow": (0xAF, 0xFF, 0x2D),
    "light_green": (0x99, 0xFF, 0x99),
    "light_sea_green": (0x2F, 0xFF, 0xF5),
    "cyan": (0x00, 0xFF, 0xFF),
    "royal_blue": (0x48, 0x76, 0xFF),
    "medium_turquoise": (0x57, 0xFF, 0xF9),
    "orchid": (0xFF, 0x84, 0xFD),
    "yellow_green": (0xBF, 0xFF, 0x46),
    "spring_green": (0x00, 0xFF, 0x7F),
    "dark_violet": (0xB3, 0x00, 0xFF),
    "purple": (0xAB, 0x24, 0xFF),
    "turquoise": (0x48, 0xFF, 0xED),
    "dark_cyan": (0x00, 0xFF, 0xFF),
    "pink": (0xFF, 0xBF, 0xCC),
    "light_steel_blue": (0xCA, 0xE2, 0xFF),
    "yellow": (0xFF, 0xFF, 0x00),
    "dark_orchid": (0xBF, 0x40, 0xFF),
    "blue_violet": (0x9B, 0x30, 0xFF),
    "web_green": (0x00, 0xFF, 0x3D),
    "gold": (0xFF, 0xD4, 0x00),
    "medium_orchid": (0xE0, 0x66, 0xFF),
    "slate_blue": (0x85, 0x6F, 0xFF),
    "dark_green": (0x00, 0xFF, 0x00),
    "coral": (0xFF, 0x7E, 0x4F),
    "salmon": (0xFF, 0xA0, 0x7A),
    "steel_blue": (0x60, 0xB7, 0xFF),
    "lawn_green": (0x79, 0xFF, 0x41),
    "olive_drab": (0xBF, 0xFF, 0x3F),
    "violet": (0xFF, 0x8B, 0xFF),
    "dark_magenta": (0xFF, 0x00, 0xFF),
    "maroon": (0xFF, 0x46, 0x8D),
    "medium_violet_red": (0xFF, 0x1A, 0xAB),
    "crimson": (0xFF, 0x25, 0x45),
    "tomato": (0xFF, 0x63, 0x47),
    "green": (0x00, 0xFF, 0x00),
    "chartreuse": (0x7F, 0xFF, 0x00),
    "chocolate": (0xFF, 0x80, 0x25),
    "magenta": (0xFF, 0x00, 0xFF),
    "medium_purple": (0xAC, 0x82, 0xFF),
    "indigo": (0x90, 0x00, 0xFF),
    "light_coral": (0xFF, 0x88, 0x88),
    "teal": (0x34, 0xFE, 0xFF),
    "pale_violet_red": (0xFF, 0x82, 0xAC),
    "dark_orange": (0xFF, 0x8A, 0x25),
    "deep_sky_blue": (0x38, 0xBD, 0xFF),
    "dark_goldenrod": (0xFF, 0xBB, 0x0E),
    "cornflower": (0x6B, 0x9E, 0xFF),
    "orange": (0xFF, 0xA6, 0x00),
    "aquamarine": (0x7F, 0xFF, 0xD2),
    "medium_spring_green": (0x1A, 0xFF, 0x9D),
    "midnight_blue": (0x39, 0x39, 0xFF),
    "sienna": (0xFF, 0x82, 0x48),
    "dark_slate_blue": (0x82, 0x6F, 0xFF),
    "dark_sea_green": (0xC1, 0xFF, 0xC1),
    "rebecca_purple": (0xAA, 0x55, 0xFF),
    "medium_blue": (0x00, 0x00, 0xFF),
    "medium_aquamarine": (0x7F, 0xFF, 0xD5),
    "aqua": (0x34, 0xFE, 0xFF),
    "lime": (0xC7, 0xFF, 0x1E),
    "medium_slate_blue": (0x83, 0x70, 0xFF),
    "navy_blue": (0x00, 0x00, 0xFF),
    "dark_blue": (0x00, 0x00, 0xFF),
    "lavender": (0x9F, 0x7F, 0xFF),
    "web_purple": (0xFF, 0x00, 0xFF),
    "web_maroon": (0xFF, 0x00, 0x00),
    "dark_red": (0xFF, 0x00, 0x00),
    "brown": (0xFF, 0x3D, 0x3E),
    "firebrick": (0xFF, 0x2F, 0x2F),
    "indian_red": (0xFF, 0x72, 0x72),
    "sea_green": (0x52, 0xFF, 0x9D),
    "forest_green": (0x3C, 0xFF, 0x3C),
}


def _hsl_to_rgb(h: float, s: float = 1.0, l: float = 1.0) -> tuple[int, int, int]:
    """Convert HSL (hue 0-360, saturation 0-1, lightness 0-1) to RGB 0-255."""
    r, g, b = colorsys.hls_to_rgb(h / 360, l, s)
    return (round(r * 255), round(g * 255), round(b * 255))


def _color_distance(c1: tuple[int, int, int], c2: tuple[float, float, float]) -> float:
    """Euclidean RGB distance."""
    return math.sqrt(
        (c1[0] - c2[0]) ** 2
        + (c1[1] - c2[1]) ** 2
        + (c1[2] - c2[2]) ** 2
    )


def _hue_to_color_name(hue: float) -> str:
    """Map a HomeKit hue value (0-360) to the nearest Alexa color name.

    Ported from mapHomeKitHueToAlexaValue() in light-mapper.ts.
    """
    target_rgb = _hsl_to_rgb(hue)
    best_name = "white"
    best_dist = float("inf")
    for name, rgb in _COLOR_NAMES.items():
        dist = _color_distance(target_rgb, rgb)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name
