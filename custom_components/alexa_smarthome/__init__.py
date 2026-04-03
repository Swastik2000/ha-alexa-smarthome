"""The Alexa Smart Home integration.

This integration connects Home Assistant to Amazon Alexa smart home devices
via the Alexa GraphQL API, using cookie-based authentication.

Based on the homebridge-alexa-smarthome Homebridge plugin.
"""
from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import AlexaApiClient, AlexaAuthError, AlexaApiError
from .const import CONF_AMAZON_DOMAIN, CONF_COOKIE, DOMAIN, PLATFORMS
from .coordinator import AlexaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alexa Smart Home from a config entry.

    Creates the aiohttp session, configures the API client with the stored
    cookie, runs device discovery, and starts the data coordinator.
    """
    amazon_domain = entry.data[CONF_AMAZON_DOMAIN]
    cookie = entry.data.get(CONF_COOKIE)

    if not cookie:
        _LOGGER.error(
            "No Alexa session cookie found. Re-authentication required."
        )
        raise ConfigEntryAuthFailed("Missing Alexa session cookie")

    # Create a single long-lived aiohttp session for the lifetime of the entry
    session = aiohttp.ClientSession()
    api = AlexaApiClient(amazon_domain=amazon_domain, session=session)
    api.set_cookie(cookie)

    coordinator = AlexaDataUpdateCoordinator(hass, entry, api)

    try:
        await coordinator.async_setup()
    except AlexaAuthError as err:
        await session.close()
        raise ConfigEntryAuthFailed(
            f"Alexa authentication failed: {err}"
        ) from err
    except AlexaApiError as err:
        await session.close()
        raise ConfigEntryNotReady(
            f"Could not connect to Alexa API: {err}"
        ) from err

    # Run the first data refresh
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator on the entry for platform access
    entry.runtime_data = coordinator

    # Forward setup to all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register an unload hook to clean up the aiohttp session
    entry.async_on_unload(session.close)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up all associated resources."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
