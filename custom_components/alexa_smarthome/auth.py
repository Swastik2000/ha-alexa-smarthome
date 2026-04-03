"""Cookie-based authentication manager for the Alexa Smart Home integration.

The Amazon Alexa API requires a valid session cookie. Users must obtain this
cookie manually from their browser after logging in to Amazon, then paste it
into the Home Assistant config flow.

How to get your Alexa cookie:
  1. Open https://www.amazon.com (or your regional domain) in a browser.
  2. Log in to your Amazon account.
  3. Open Developer Tools (F12) → Application tab → Cookies.
  4. Select the amazon.com domain and copy the full cookie string, or use
     the "Copy all as header value" option (the `Cookie: ...` header value).
  5. Paste that string into the Home Assistant integration setup form.

The cookie is persisted to disk so re-authentication is only needed when
the session expires (typically every 14 days).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .const import COOKIE_FILENAME

_LOGGER = logging.getLogger(__name__)


class AlexaAuthError(Exception):
    """Raised when authentication cannot be completed."""


class AlexaAuthManager:
    """Manages Amazon Alexa session cookie persistence.

    The cookie is stored as a JSON file in the HA config directory so it
    survives restarts. The integration re-uses the stored cookie until it
    receives a 401/403 response from the Alexa API, at which point
    ConfigEntryAuthFailed is raised and HA prompts the user to re-authenticate.
    """

    def __init__(
        self,
        config_dir: str,
        amazon_domain: str,
        language: str,
    ) -> None:
        self._config_dir = config_dir
        self._amazon_domain = amazon_domain
        self._language = language
        self._cookie_path = os.path.join(config_dir, COOKIE_FILENAME)
        self._cookie: str | None = None

    @property
    def cookie_path(self) -> str:
        """Return path to the persisted cookie file."""
        return self._cookie_path

    async def load_cookie(self) -> str | None:
        """Load cookie from disk if it exists and appears valid."""
        if not os.path.exists(self._cookie_path):
            _LOGGER.debug("No persisted cookie found at %s", self._cookie_path)
            return None
        try:
            with open(self._cookie_path) as fp:
                data = json.load(fp)
            cookie = data.get("cookie")
            if not cookie:
                _LOGGER.debug("Persisted cookie file has no 'cookie' field")
                return None
            self._cookie = cookie
            _LOGGER.debug("Loaded persisted Alexa session cookie")
            return cookie
        except (json.JSONDecodeError, OSError) as err:
            _LOGGER.warning("Could not read cookie file %s: %s", self._cookie_path, err)
            return None

    def save_cookie(self, cookie: str) -> None:
        """Persist the session cookie to disk."""
        data: dict[str, Any] = {"cookie": cookie, "amazon_domain": self._amazon_domain}
        try:
            with open(self._cookie_path, "w") as fp:
                json.dump(data, fp)
            _LOGGER.info("Alexa session cookie saved to %s", self._cookie_path)
        except OSError as err:
            _LOGGER.error("Failed to save cookie to %s: %s", self._cookie_path, err)

    def delete_cookie(self) -> None:
        """Remove the persisted cookie file (forces re-authentication)."""
        if os.path.exists(self._cookie_path):
            try:
                os.remove(self._cookie_path)
                _LOGGER.info("Deleted Alexa cookie file at %s", self._cookie_path)
            except OSError as err:
                _LOGGER.warning("Could not delete cookie file: %s", err)

    async def get_session_cookie(self) -> str | None:
        """Return the current session cookie, loading from disk if needed."""
        if self._cookie:
            return self._cookie
        return await self.load_cookie()
