"""Config flow for the Alexa Smart Home integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AlexaApiClient, AlexaAuthError
from .auth import AlexaAuthManager
from .const import (
    AMAZON_DOMAINS,
    CONF_AMAZON_DOMAIN,
    CONF_CACHE_TTL,
    CONF_COOKIE,
    CONF_LANGUAGE,
    CONF_PROXY_PORT,
    DEFAULT_AMAZON_DOMAIN,
    DEFAULT_CACHE_TTL,
    DEFAULT_LANGUAGE,
    DEFAULT_PROXY_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# How long to wait for the user to complete Amazon login (seconds)
_AUTH_TIMEOUT = 300

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AMAZON_DOMAIN, default=DEFAULT_AMAZON_DOMAIN): SelectSelector(
            SelectSelectorConfig(
                options=AMAZON_DOMAINS,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(CONF_LANGUAGE, default=DEFAULT_LANGUAGE): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
        vol.Required(CONF_PROXY_PORT, default=DEFAULT_PROXY_PORT): NumberSelector(
            NumberSelectorConfig(min=1024, max=65535, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_CACHE_TTL, default=DEFAULT_CACHE_TTL): NumberSelector(
            NumberSelectorConfig(min=30, max=3600, mode=NumberSelectorMode.BOX)
        ),
    }
)


class AlexaSmartHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Alexa Smart Home config flow.

    Step 1 (user): Collect connection parameters.
    Step 2 (auth): Show proxy URL for browser login; wait for cookie capture.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._auth_manager: AlexaAuthManager | None = None
        self._proxy_url: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — collect configuration parameters."""
        errors: dict[str, str] = {}

        if user_input is not None:
            amazon_domain = user_input[CONF_AMAZON_DOMAIN]
            proxy_port = int(user_input[CONF_PROXY_PORT])

            # Check for duplicate config entry
            await self.async_set_unique_id(f"{amazon_domain}_{proxy_port}")
            self._abort_if_unique_id_configured()

            self._config = {
                CONF_AMAZON_DOMAIN: amazon_domain,
                CONF_LANGUAGE: user_input[CONF_LANGUAGE],
                CONF_PROXY_PORT: proxy_port,
                CONF_CACHE_TTL: int(user_input[CONF_CACHE_TTL]),
            }
            return await self.async_step_auth()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show proxy URL and wait for the user to complete Amazon login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # The user has confirmed; check if a cookie was already captured
            if self._auth_manager is not None:
                cookie = await self._auth_manager.get_session_cookie()
                if cookie:
                    await self._auth_manager.stop_proxy()
                    self._config[CONF_COOKIE] = cookie
                    return self.async_create_entry(
                        title=f"Alexa ({self._config[CONF_AMAZON_DOMAIN]})",
                        data=self._config,
                    )
            errors["base"] = "auth_not_complete"

        # Start the proxy (idempotent — safe to call multiple times)
        if self._auth_manager is None:
            self._auth_manager = AlexaAuthManager(
                config_dir=self.hass.config.config_dir,
                amazon_domain=self._config[CONF_AMAZON_DOMAIN],
                language=self._config[CONF_LANGUAGE],
                proxy_port=self._config[CONF_PROXY_PORT],
            )
            # Check if there is already a saved cookie from a previous session
            existing_cookie = await self._auth_manager.load_cookie()
            if existing_cookie:
                _LOGGER.debug("Found existing cookie — skipping proxy startup")
                self._config[CONF_COOKIE] = existing_cookie
                return self.async_create_entry(
                    title=f"Alexa ({self._config[CONF_AMAZON_DOMAIN]})",
                    data=self._config,
                )

        if self._proxy_url is None:
            self._proxy_url = await self._auth_manager.start_proxy()

        description_placeholders = {
            "proxy_url": self._proxy_url,
            "amazon_domain": self._config[CONF_AMAZON_DOMAIN],
        }

        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the cookie has expired."""
        self._config = dict(entry_data)
        return await self.async_step_auth()
