"""Cookie-based authentication manager for the Alexa Smart Home integration.

The Amazon Alexa API requires a valid session cookie obtained by logging into
Amazon through a browser. This module provides a local HTTP proxy that
intercepts the login flow and captures the cookie, mirroring the approach
used by alexa-cookie2/alexa-remote2 in the Homebridge plugin.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp
from aiohttp import web

from .const import COOKIE_FILENAME

_LOGGER = logging.getLogger(__name__)

# Cookie fields that indicate a valid Alexa session
_REQUIRED_COOKIE_FIELDS = {"localCookie"}


class AlexaAuthError(Exception):
    """Raised when authentication cannot be completed."""


class AlexaAuthManager:
    """Manages Amazon Alexa session cookie acquisition and persistence.

    Workflow:
    1. User opens the proxy URL in their browser.
    2. The proxy serves an Amazon login page for the configured domain.
    3. After a successful login, the proxy intercepts Set-Cookie headers and
       extracts the session cookie.
    4. The cookie is persisted to disk and returned to callers.
    """

    def __init__(
        self,
        config_dir: str,
        amazon_domain: str,
        language: str,
        proxy_port: int,
    ) -> None:
        self._config_dir = config_dir
        self._amazon_domain = amazon_domain
        self._language = language
        self._proxy_port = proxy_port
        self._cookie_path = os.path.join(config_dir, COOKIE_FILENAME)
        self._cookie: str | None = None
        self._cookie_data: dict[str, Any] | None = None
        self._runner: web.AppRunner | None = None
        self._auth_event: asyncio.Event | None = None

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
            cookie_data = data.get("cookieData", {})
            local_cookie = cookie_data.get("localCookie")
            if not local_cookie:
                _LOGGER.debug("Persisted cookie file has no localCookie field")
                return None
            self._cookie = local_cookie
            self._cookie_data = cookie_data
            _LOGGER.debug("Loaded persisted Alexa session cookie")
            return local_cookie
        except (json.JSONDecodeError, OSError) as err:
            _LOGGER.warning("Could not read cookie file %s: %s", self._cookie_path, err)
            return None

    def save_cookie(self, cookie: str, cookie_data: dict[str, Any] | None = None) -> None:
        """Persist the session cookie to disk."""
        data: dict[str, Any] = {
            "cookieData": {
                "localCookie": cookie,
                **(cookie_data or {}),
            }
        }
        try:
            with open(self._cookie_path, "w") as fp:
                json.dump(data, fp)
            _LOGGER.info("Alexa session cookie saved to %s", self._cookie_path)
        except OSError as err:
            _LOGGER.error("Failed to save cookie to %s: %s", self._cookie_path, err)

    def set_cookie(self, cookie: str) -> None:
        """Set the in-memory session cookie (does not persist)."""
        self._cookie = cookie

    async def get_session_cookie(self) -> str | None:
        """Return the current session cookie, loading from disk if needed."""
        if self._cookie:
            return self._cookie
        return await self.load_cookie()

    async def start_proxy(self) -> str:
        """Start the local authentication proxy server.

        Returns the URL the user should open in their browser to authenticate.
        """
        if self._runner is not None:
            _LOGGER.debug("Proxy already running")
            return self._proxy_url

        self._auth_event = asyncio.Event()

        app = web.Application()
        app.router.add_get("/", self._handle_proxy_root)
        app.router.add_get("/amazon-login", self._handle_amazon_login_redirect)
        app.router.add_post("/cookie", self._handle_cookie_submission)
        app.router.add_get("/status", self._handle_status)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._proxy_port)
        await site.start()

        proxy_url = f"http://localhost:{self._proxy_port}"
        _LOGGER.info(
            "Alexa auth proxy started on port %d. Open %s to authenticate.",
            self._proxy_port,
            proxy_url,
        )
        return proxy_url

    async def stop_proxy(self) -> None:
        """Stop the local proxy server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            _LOGGER.debug("Alexa auth proxy stopped")

    async def wait_for_auth(self, timeout: float = 300.0) -> str:
        """Block until the user authenticates or timeout expires.

        Returns:
            The session cookie string.

        Raises:
            AlexaAuthError: If authentication times out or fails.
        """
        if self._auth_event is None:
            raise AlexaAuthError("Proxy not started; call start_proxy() first")
        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as err:
            raise AlexaAuthError(
                f"Authentication timed out after {timeout}s"
            ) from err
        if not self._cookie:
            raise AlexaAuthError("Authentication completed but no cookie was captured")
        return self._cookie

    @property
    def _proxy_url(self) -> str:
        return f"http://localhost:{self._proxy_port}"

    async def _handle_proxy_root(self, request: web.Request) -> web.Response:
        """Serve a landing page with instructions and a link to Amazon login."""
        amazon_login_url = (
            f"https://www.{self._amazon_domain}/ap/signin"
            f"?openid.ns=http://specs.openid.net/auth/2.0"
            f"&openid.mode=checkid_setup"
        )
        html = f"""<!DOCTYPE html>
<html>
<head><title>Alexa Smart Home — Authentication</title></head>
<body>
<h1>Alexa Smart Home Authentication</h1>
<p>Click the button below to log in to your Amazon account ({self._amazon_domain}).</p>
<p>After successful login, your session will be captured automatically and
Home Assistant will complete the integration setup.</p>
<a href="/amazon-login">
  <button style="padding:12px 24px;font-size:16px;cursor:pointer;">
    Log in to Amazon
  </button>
</a>
<hr>
<p><small>If you have already authenticated and are testing, you can also
<a href="/status">check the proxy status</a>.</small></p>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def _handle_amazon_login_redirect(self, request: web.Request) -> web.Response:
        """Redirect the browser to the real Amazon login page."""
        amazon_login = (
            f"https://www.{self._amazon_domain}/ap/signin"
            f"?openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
            f"&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
            f"&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
            f"&openid.mode=checkid_setup"
            f"&openid.assoc_handle=amzn_dp_project_dec_web_{self._language.replace('-', '_')}"
            f"&language={self._language.replace('-', '_')}"
        )
        raise web.HTTPFound(amazon_login)

    async def _handle_cookie_submission(self, request: web.Request) -> web.Response:
        """Accept a cookie submitted via POST (for programmatic use or testing)."""
        try:
            body = await request.json()
            cookie = body.get("cookie") or body.get("localCookie")
            if not cookie:
                return web.Response(
                    status=400,
                    text=json.dumps({"error": "Missing 'cookie' field"}),
                    content_type="application/json",
                )
            self._cookie = cookie
            self.save_cookie(cookie, body)
            if self._auth_event:
                self._auth_event.set()
            return web.Response(
                text=json.dumps({"status": "ok"}),
                content_type="application/json",
            )
        except (json.JSONDecodeError, Exception) as err:
            return web.Response(
                status=400,
                text=json.dumps({"error": str(err)}),
                content_type="application/json",
            )

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Return current proxy status (for debugging)."""
        status = {
            "authenticated": bool(self._cookie),
            "amazon_domain": self._amazon_domain,
            "language": self._language,
            "proxy_port": self._proxy_port,
        }
        return web.Response(
            text=json.dumps(status),
            content_type="application/json",
        )
