"""Device state cache with TTL for the Alexa Smart Home integration.

Ported from src/store/device-store.ts in the Homebridge plugin.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .models import CapabilityState

_LOGGER = logging.getLogger(__name__)


class DeviceStore:
    """In-memory cache of device capability states with TTL.

    Mirrors the DeviceStore class from device-store.ts.  States are keyed by
    device ID.  Each device has its own ``last_updated`` timestamp so that
    isCacheFresh() can be checked per-device, matching the TypeScript
    implementation exactly.
    """

    def __init__(self, cache_ttl: int = 60) -> None:
        """Initialise the store.

        Args:
            cache_ttl: Number of seconds before cached states are considered
                       stale and should be re-fetched.
        """
        self._cache_ttl = cache_ttl
        # Maps device_id -> list[CapabilityState]
        self._states: dict[str, list[CapabilityState]] = {}
        # Per-device last-updated timestamps (time.monotonic())
        self._device_timestamps: dict[str, float] = {}
        # Global last_updated kept for backward-compatibility
        self._last_updated: float = 0.0

    @property
    def cache_ttl(self) -> int:
        """Return the configured cache TTL in seconds."""
        return self._cache_ttl

    @property
    def last_updated(self) -> float:
        """Return the monotonic timestamp of the last cache update (any device)."""
        return self._last_updated

    def is_cache_fresh(self) -> bool:
        """Return True if the global cache was updated within the TTL window.

        Deprecated in favour of isCacheFresh(device_id) for per-device checks.
        Kept for backward compatibility.
        """
        return (time.monotonic() - self._last_updated) < self._cache_ttl

    def isCacheFresh(self, device_id: str) -> bool:  # noqa: N802 — matches TS name
        """Return True if the per-device cache was updated within the TTL window.

        Mirrors isCacheFresh() from device-store.ts.  A device that has never
        been fetched is considered stale (returns False).
        """
        ts = self._device_timestamps.get(device_id)
        if ts is None:
            return False
        return (time.monotonic() - ts) < self._cache_ttl

    def update_states(
        self, device_id: str, states: list[CapabilityState]
    ) -> None:
        """Replace the cached states for a single device and update the timestamp."""
        now = time.monotonic()
        self._states[device_id] = states
        self._device_timestamps[device_id] = now
        self._last_updated = now

    def get_states(self, device_id: str) -> list[CapabilityState]:
        """Return the cached states for a device, or an empty list."""
        return list(self._states.get(device_id, []))

    def get_state_value(
        self,
        device_id: str,
        feature_name: str,
        name: str | None = None,
        instance: str | None = None,
    ) -> CapabilityState | None:
        """Fetch a specific capability state from the cache.

        Matches by feature_name and optionally by property name and instance,
        mirroring getCacheValue() in device-store.ts.
        """
        for state in self._states.get(device_id, []):
            if state.feature_name != feature_name:
                continue
            if name is not None and state.name != name:
                continue
            if instance is not None and state.instance != instance:
                continue
            return state
        return None

    def update_state_value(
        self,
        device_id: str,
        new_state: CapabilityState,
    ) -> None:
        """Update the value of an existing cached state in-place.

        If a matching state is not found, the new state is appended.
        Mirrors updateCacheValue() in device-store.ts.
        """
        existing = self._states.get(device_id, [])
        for cached in existing:
            if (
                cached.feature_name == new_state.feature_name
                and (new_state.name is None or cached.name == new_state.name)
                and (new_state.instance is None or cached.instance == new_state.instance)
            ):
                cached.value = new_state.value
                return
        # State not found — append it so subsequent reads work correctly
        self._states.setdefault(device_id, []).append(new_state)
