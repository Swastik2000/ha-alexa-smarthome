"""Authentication proxy for the Alexa Smart Home integration.

Replicates the alexa-cookie2 approach used by the Homebridge plugin:

1. Starts a local HTTP server on a configurable port (default 9000)
2. The server acts as a transparent proxy — it fetches Amazon pages
   server-side over HTTPS, rewrites URLs to go through the proxy,
   and serves the content to the browser over plain HTTP
3. All Set-Cookie headers from Amazon responses are captured
4. When a successful login is detected the cookie is persisted and
   the config flow is signalled to complete

The user never interacts with Amazon directly — they open
http://<ha-host>:9000 and see a real Amazon login page.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import aiohttp
from aiohttp import web

from .const import COOKIE_FILENAME

_LOGGER = logging.getLogger(__name__)

# Cookies that indicate a completed Amazon login
_AUTH_COOKIES = {"at-main", "sess-at-main", "x-main", "ubid-main"}

# Headers we must NOT forward from the proxied response to the browser
_HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding", "te",
    "trailer", "upgrade", "proxy-authorization", "proxy-authenticate",
    "content-encoding",  # we decode on the server side
}


class AlexaAuthError(Exception):
    """Raised when authentication cannot be completed."""


class AlexaAuthManager:
    """Transparent HTTP proxy that captures the Amazon session cookie."""

    def __init__(
        self,
        config_dir: str,
        amazon_domain: str,
        language: str,
        proxy_port: int = 9000,
        ha_host: str = "homeassistant.local",
    ) -> None:
        self._config_dir = config_dir
        self._amazon_domain = amazon_domain
        self._language = language
        self._proxy_port = proxy_port
        self._ha_host = ha_host
        self._cookie_path = os.path.join(config_dir, COOKIE_FILENAME)

        self._captured_cookies: dict[str, str] = {}
        self._cookie: str | None = None
        self._auth_event: asyncio.Event = asyncio.Event()
        self._runner: web.AppRunner | None = None
        self._upstream_session: aiohttp.ClientSession | None = None

    @property
    def server_url(self) -> str:
        return f"http://{self._ha_host}:{self._proxy_port}"

    # ------------------------------------------------------------------
    # Cookie persistence
    # ------------------------------------------------------------------

    async def load_cookie(self) -> str | None:
        if not os.path.exists(self._cookie_path):
            return None
        try:
            with open(self._cookie_path) as fp:
                data = json.load(fp)
            cookie = data.get("cookie")
            if cookie:
                self._cookie = cookie
                return cookie
        except (json.JSONDecodeError, OSError) as err:
            _LOGGER.warning("Could not read cookie file: %s", err)
        return None

    def save_cookie(self, cookie: str) -> None:
        data: dict[str, Any] = {
            "cookie": cookie,
            "amazon_domain": self._amazon_domain,
        }
        try:
            with open(self._cookie_path, "w") as fp:
                json.dump(data, fp)
            _LOGGER.info("Alexa session cookie saved")
        except OSError as err:
            _LOGGER.error("Failed to save cookie: %s", err)

    def delete_cookie(self) -> None:
        if os.path.exists(self._cookie_path):
            try:
                os.remove(self._cookie_path)
            except OSError:
                pass

    async def get_session_cookie(self) -> str | None:
        if self._cookie:
            return self._cookie
        return await self.load_cookie()

    # ------------------------------------------------------------------
    # Proxy server
    # ------------------------------------------------------------------

    async def start_server(self) -> str:
        """Start the proxy server. Returns the URL for the user to open."""
        if self._runner is not None:
            return self.server_url

        # Upstream session used by the proxy to talk to Amazon
        self._upstream_session = aiohttp.ClientSession(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
        )

        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", self._handle_proxy)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._proxy_port)
        await site.start()
        _LOGGER.info("Alexa auth proxy started at %s", self.server_url)
        return self.server_url

    async def stop_server(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self._upstream_session:
            await self._upstream_session.close()
            self._upstream_session = None

    async def wait_for_cookie(self, timeout: float = 600.0) -> str:
        """Wait until the user completes login."""
        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as err:
            raise AlexaAuthError("Authentication timed out") from err
        if not self._cookie:
            raise AlexaAuthError("No cookie captured")
        return self._cookie

    # ------------------------------------------------------------------
    # Proxy request handler
    # ------------------------------------------------------------------

    async def _handle_proxy(self, request: web.Request) -> web.StreamResponse:
        """Forward the request to Amazon, rewrite response, capture cookies."""
        amazon_base = f"https://www.{self._amazon_domain}"
        path = request.path or "/"
        if not path.startswith("/"):
            path = "/" + path
        query = request.query_string
        upstream_url = f"{amazon_base}{path}"
        if query:
            upstream_url = f"{upstream_url}?{query}"

        # Forward cookies we've already captured back to Amazon
        upstream_cookie = "; ".join(
            f"{k}={v}" for k, v in self._captured_cookies.items()
        )

        # Build upstream headers
        headers: dict[str, str] = {}
        for name, value in request.headers.items():
            if name.lower() in ("host", "cookie", "content-length"):
                continue
            headers[name] = value
        headers["Host"] = f"www.{self._amazon_domain}"
        if upstream_cookie:
            headers["Cookie"] = upstream_cookie

        # Read request body for POST
        body = await request.read() if request.method == "POST" else None

        try:
            assert self._upstream_session is not None
            async with self._upstream_session.request(
                method=request.method,
                url=upstream_url,
                headers=headers,
                data=body,
                allow_redirects=False,
                ssl=True,
            ) as upstream:
                # Capture cookies from this response
                self._capture_cookies(upstream)

                # Build response headers, rewriting Location if needed
                resp_headers: dict[str, str] = {}
                for name, value in upstream.headers.items():
                    if name.lower() in _HOP_BY_HOP:
                        continue
                    if name.lower() == "location":
                        value = self._rewrite_url(value)
                    if name.lower() == "set-cookie":
                        continue  # We handle cookies ourselves
                    resp_headers[name] = value

                status = upstream.status

                # Read and rewrite body for HTML responses
                content_type = upstream.headers.get("Content-Type", "")
                raw_body = await upstream.read()

                if "text/html" in content_type:
                    try:
                        text = raw_body.decode("utf-8", errors="replace")
                        text = self._rewrite_html(text)
                        raw_body = text.encode("utf-8")
                        resp_headers["Content-Type"] = "text/html; charset=utf-8"
                    except Exception:  # noqa: BLE001
                        pass

                resp_headers["Content-Length"] = str(len(raw_body))

                response = web.Response(
                    status=status,
                    headers=resp_headers,
                    body=raw_body,
                )
                return response

        except aiohttp.ClientError as err:
            _LOGGER.error("Proxy upstream error: %s", err)
            return web.Response(status=502, text=f"Proxy error: {err}")

    def _capture_cookies(self, response: aiohttp.ClientResponse) -> None:
        """Extract Set-Cookie headers and accumulate into our cookie jar."""
        for cookie_header in response.headers.getall("Set-Cookie", []):
            # Parse name=value; attrs...
            parts = cookie_header.split(";")
            name_value = parts[0].strip()
            if "=" in name_value:
                name, _, value = name_value.partition("=")
                self._captured_cookies[name.strip()] = value.strip()

        # Check if we now have enough cookies to authenticate
        if _AUTH_COOKIES.issubset(set(self._captured_cookies.keys())):
            cookie_str = "; ".join(
                f"{k}={v}" for k, v in self._captured_cookies.items()
            )
            self._cookie = cookie_str
            self.save_cookie(cookie_str)
            if not self._auth_event.is_set():
                _LOGGER.info("Alexa: login cookie captured successfully")
                self._auth_event.set()

    def _rewrite_url(self, url: str) -> str:
        """Rewrite an Amazon URL to go through our proxy."""
        amazon_base = f"https://www.{self._amazon_domain}"
        proxy_base = self.server_url
        if url.startswith(amazon_base):
            return url.replace(amazon_base, proxy_base, 1)
        if url.startswith(f"https://www.{self._amazon_domain}"):
            return url.replace(
                f"https://www.{self._amazon_domain}", proxy_base, 1
            )
        return url

    def _rewrite_html(self, html: str) -> str:
        """Rewrite Amazon URLs in HTML to point to our proxy."""
        amazon_https = f"https://www.{self._amazon_domain}"
        amazon_http = f"http://www.{self._amazon_domain}"
        proxy = self.server_url
        html = html.replace(amazon_https, proxy)
        html = html.replace(amazon_http, proxy)
        # Also rewrite protocol-relative URLs
        html = html.replace(
            f"//www.{self._amazon_domain}", proxy.replace("http://", "//")
        )
        return html
