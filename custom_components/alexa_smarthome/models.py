"""Data models for the Alexa Smart Home integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapabilityState:
    """Represents the state of a single device capability/feature.

    Ported from src/domain/alexa/index.ts CapabilityState interface.
    """

    feature_name: str
    """The Alexa feature name (e.g., 'power', 'brightness', 'color')."""

    value: str | int | float | bool | dict[str, Any] | None
    """The current value of the feature."""

    instance: str | None = None
    """For range features, the instance identifier."""

    name: str | None = None
    """The property name within the feature (e.g., 'thermostatMode', 'lockState')."""

    range_name: str | None = None
    """Human-friendly name for range features (e.g., 'Indoor humidity')."""


@dataclass
class RangeFeatureCapability:
    """Describes a range feature's capabilities/configuration.

    Ported from src/domain/alexa/save-device-capabilities.ts RangeFeature.
    """

    instance: str
    """The unique instance identifier for this range feature."""

    friendly_name: str
    """Human-readable name (e.g., 'Indoor humidity', 'Carbon Monoxide')."""


@dataclass
class SmartHomeDevice:
    """Represents an Alexa smart home device.

    Ported from src/domain/alexa/get-devices.ts SmartHomeDevice interface.
    """

    endpoint_id: str
    """The full Alexa endpoint ID (e.g., 'amzn1.alexa.endpoint.xxx')."""

    id: str
    """Shortened device ID (endpoint_id with 'amzn1.alexa.endpoint.' prefix stripped)."""

    display_name: str
    """Human-readable device name."""

    supported_operations: list[str]
    """List of supported Alexa operation names."""

    enabled: bool
    """Whether the device is enabled in Alexa."""

    device_type: str
    """Alexa device category (e.g., 'LIGHT', 'SWITCH', 'THERMOSTAT')."""

    serial_number: str = "Unknown"
    """Device serial number."""

    model: str = "Unknown"
    """Device model name."""

    manufacturer: str = "Amazon"
    """Device manufacturer."""

    range_features: list[RangeFeatureCapability] = field(default_factory=list)
    """Range feature capabilities discovered during device enumeration."""

    @property
    def unique_id(self) -> str:
        """Return unique identifier suitable for HA entity unique_id."""
        return self.endpoint_id
