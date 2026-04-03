"""Config flow for the Alexa Smart Home integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
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

    Step 1 (user): Collect region, language, and proxy port.
    Step 2 (auth): Start the local bookmarklet server; user logs in to Amazon
                   and clicks the bookmarklet — cookie is captured automatically.
    Step 3 (done): Cookie validated; entry created.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._auth_manager: AlexaAuthManager | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect connection parameters."""
        if user_input is not None:
            amazon_domain = user_input[CONF_AMAZON_DOMAIN]
            await self.async_set_unique_id(amazon_domain)
            self._abort_if_unique_id_configured()

            self._config = {
                CONF_AMAZON_DOMAIN: amazon_domain,
                CONF_LANGUAGE: user_input[CONF_LANGUAGE],
                CONF_PROXY_PORT: int(user_input[CONF_PROXY_PORT]),
                CONF_CACHE_TTL: int(user_input[CONF_CACHE_TTL]),
            }
            return await self.async_step_auth()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start the bookmarklet server and wait for the user to authenticate."""
        errors: dict[str, str] = {}

        # Initialise the auth manager once
        if self._auth_manager is None:
            self._auth_manager = AlexaAuthManager(
                config_dir=self.hass.config.config_dir,
                amazon_domain=self._config[CONF_AMAZON_DOMAIN],
                language=self._config[CONF_LANGUAGE],
                proxy_port=self._config[CONF_PROXY_PORT],
                ha_host=self.hass.config.external_url
                        or "homeassistant.local",
            )
            # Reuse a previously saved cookie if available
            existing = await self._auth_manager.load_cookie()
            if existing:
                self._config[CONF_COOKIE] = existing
                return self.async_create_entry(
                    title=f"Alexa ({self._config[CONF_AMAZON_DOMAIN]})",
                    data=self._config,
                )
            await self._auth_manager.start_server()

        if user_input is not None:
            # User clicked Submit — check whether the bookmarklet fired
            cookie = await self._auth_manager.get_session_cookie()
            if cookie:
                await self._auth_manager.stop_server()
                self._config[CONF_COOKIE] = cookie
                return self.async_create_entry(
                    title=f"Alexa ({self._config[CONF_AMAZON_DOMAIN]})",
                    data=self._config,
                )
            errors["base"] = "auth_not_complete"

        server_url = self._auth_manager.server_url
        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "server_url": server_url,
                "amazon_domain": self._config[CONF_AMAZON_DOMAIN],
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Re-authenticate when the cookie has expired."""
        self._config = dict(entry_data)
        self._auth_manager = None
        return await self.async_step_auth()
