"""Config flow for the Alexa Smart Home integration."""
from __future__ import annotations

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
    DEFAULT_AMAZON_DOMAIN,
    DEFAULT_CACHE_TTL,
    DEFAULT_LANGUAGE,
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
        vol.Required(CONF_CACHE_TTL, default=DEFAULT_CACHE_TTL): NumberSelector(
            NumberSelectorConfig(min=30, max=3600, mode=NumberSelectorMode.BOX)
        ),
    }
)


def _build_cookie_schema(default: str = "") -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_COOKIE, default=default): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
            ),
        }
    )


class AlexaSmartHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Alexa Smart Home config flow.

    Step 1 (user): Collect Amazon domain and language.
    Step 2 (cookie): User pastes the session cookie extracted from their browser.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — collect configuration parameters."""
        if user_input is not None:
            amazon_domain = user_input[CONF_AMAZON_DOMAIN]

            # Prevent duplicate entries for the same Amazon domain
            await self.async_set_unique_id(amazon_domain)
            self._abort_if_unique_id_configured()

            self._config = {
                CONF_AMAZON_DOMAIN: amazon_domain,
                CONF_LANGUAGE: user_input[CONF_LANGUAGE],
                CONF_CACHE_TTL: int(user_input[CONF_CACHE_TTL]),
            }
            return await self.async_step_cookie()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
        )

    async def async_step_cookie(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to paste their Amazon session cookie."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie = (user_input.get(CONF_COOKIE) or "").strip()
            if not cookie:
                errors[CONF_COOKIE] = "cookie_required"
            else:
                # Validate the cookie by attempting a device list query
                error = await _test_cookie(
                    self.hass,
                    self._config[CONF_AMAZON_DOMAIN],
                    cookie,
                )
                if error:
                    errors["base"] = error
                else:
                    # Save cookie to disk and create the config entry
                    auth = AlexaAuthManager(
                        config_dir=self.hass.config.config_dir,
                        amazon_domain=self._config[CONF_AMAZON_DOMAIN],
                        language=self._config[CONF_LANGUAGE],
                    )
                    auth.save_cookie(cookie)
                    self._config[CONF_COOKIE] = cookie
                    return self.async_create_entry(
                        title=f"Alexa ({self._config[CONF_AMAZON_DOMAIN]})",
                        data=self._config,
                    )

        amazon_domain = self._config.get(CONF_AMAZON_DOMAIN, DEFAULT_AMAZON_DOMAIN)
        return self.async_show_form(
            step_id="cookie",
            data_schema=_build_cookie_schema(),
            errors=errors,
            description_placeholders={"amazon_domain": amazon_domain},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the cookie has expired."""
        self._config = dict(entry_data)
        return await self.async_step_cookie()


async def _test_cookie(
    hass: HomeAssistant,
    amazon_domain: str,
    cookie: str,
) -> str | None:
    """Test whether a cookie can reach the Alexa API.

    Returns an error key string on failure, or None on success.
    """
    try:
        session = aiohttp.ClientSession()
        try:
            client = AlexaApiClient(amazon_domain=amazon_domain, session=session)
            client.set_cookie(cookie)
            await client.get_devices()
        finally:
            await session.close()
    except AlexaAuthError:
        return "invalid_auth"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error testing Alexa cookie")
        return "cannot_connect"
    return None
