"""Amazon Alexa authentication proxy.

Exact Python port of alexa-cookie2's proxy.js approach:

- Masquerades as the Amazon Echo iOS app (PitanguiBridge user-agent)
- Uses the Alexa mobile OpenID/PKCE device auth flow, NOT the web login
- This is the same flow the Alexa app uses, so AWS WAF does not block it
- Injects required device cookies (frc, map-md) into every proxied request
- URL scheme: https://www.amazon.in/ <-> http://<ha-host>:9000/www.amazon.in/
- Detects success when Amazon redirects to /ap/maplanding or /spa/index.html
- Extracts loginCookie + authorization_code and signals the config flow

User just opens http://<ha-host>:9000 and logs in normally.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import urllib.parse
from typing import Any

import aiohttp
from aiohttp import web

from .const import COOKIE_FILENAME

_LOGGER = logging.getLogger(__name__)

# Exact user-agent from alexa-cookie2 (iOS iPhone — required for amzn_dp_project_dee_ios auth flow)
_USER_AGENT = "AppleWebKit PitanguiBridge/2.2.485407.0-[HARDWARE=iPhone10_4][SOFTWARE=15.5][DEVICE=iPhone]"

# map-md cookie value — exact replica from alexa-cookie2
_MAP_MD_PAYLOAD = {
    "device_user_dictionary": [],
    "device_registration_data": {"software_version": "1"},
    "app_identifier": {
        "app_version": "2.2.485407",
        "bundle_id": "com.amazon.echo",
    },
}

# Suffix appended to FORMERDATA_STORE_VERSION 4 device IDs
_DEVICE_ID_SUFFIX = "23413249564c5635564d32573831"

# Headers that must not be forwarded upstream or downstream
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "transfer-encoding", "te",
    "trailer", "upgrade", "proxy-authorization", "proxy-authenticate",
})


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    verifier = _base64url(secrets.token_bytes(32))
    challenge = _base64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# ---------------------------------------------------------------------------
# Device identity generation (mirrors alexa-cookie2)
# ---------------------------------------------------------------------------

def _make_device_id() -> str:
    buf = secrets.token_bytes(16)
    hex_upper = buf.hex().upper()          # 32 hex chars
    hex_of_hex = hex_upper.encode().hex()  # 64 chars (hex of ascii hex)
    return hex_of_hex + _DEVICE_ID_SUFFIX


def _make_frc() -> str:
    return base64.b64encode(secrets.token_bytes(313)).decode()


def _make_map_md() -> str:
    return base64.b64encode(
        json.dumps(_MAP_MD_PAYLOAD, separators=(",", ":")).encode()
    ).decode()


# ---------------------------------------------------------------------------
# Amazon domain → OpenID handle suffix (mirrors alexa-cookie2)
# ---------------------------------------------------------------------------

def _amazon_page_handle(amazon_domain: str) -> str:
    tld = amazon_domain.rsplit(".", 1)[-1]
    if tld == "jp":
        return f"_{tld}"
    return ""


# ---------------------------------------------------------------------------
# Auth manager
# ---------------------------------------------------------------------------


class AlexaAuthError(Exception):
    """Raised when authentication cannot be completed."""


class AlexaAuthManager:
    """Proxy-based Amazon auth that replicates alexa-cookie2's proxy.js."""

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

        # Device identity (generated once, reused on restart)
        self._frc = _make_frc()
        self._map_md = _make_map_md()
        self._device_id = _make_device_id()
        self._code_verifier, self._code_challenge = _pkce_pair()

        # Running cookie jar for the proxy session
        self._proxy_cookies: dict[str, str] = {
            "frc": self._frc,
            "map-md": self._map_md,
        }

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
    # Proxy server lifecycle
    # ------------------------------------------------------------------

    async def start_server(self) -> str:
        if self._runner is not None:
            return self.server_url

        self._upstream_session = aiohttp.ClientSession(
            headers={"User-Agent": _USER_AGENT},
            connector=aiohttp.TCPConnector(ssl=True),
        )

        app = web.Application()
        app.router.add_route("*", "/cookie-success", self._handle_success)
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
        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as err:
            raise AlexaAuthError("Authentication timed out") from err
        if not self._cookie:
            raise AlexaAuthError("No cookie captured")
        return self._cookie

    # ------------------------------------------------------------------
    # URL rewriting (mirrors replaceHosts / replaceHostsBack in proxy.js)
    # ------------------------------------------------------------------

    def _to_proxy_url(self, url: str) -> str:
        """Rewrite an Amazon URL to go through our proxy."""
        proxy = self.server_url
        d = self._amazon_domain
        for prefix in (
            f"https://www.{d}/",
            f"http://www.{d}/",
            f"https://alexa.{d}/",
            f"http://alexa.{d}/",
        ):
            if url.startswith(prefix):
                subdomain = "www" if "www." in prefix else "alexa"
                rest = url[len(prefix):]
                return f"{proxy}/{subdomain}.{d}/{rest}"
        # protocol-relative
        for prefix in (f"//www.{d}/", f"//alexa.{d}/"):
            if url.startswith(prefix):
                subdomain = "www" if "www." in prefix else "alexa"
                rest = url[len(prefix):]
                return f"//{self._ha_host}:{self._proxy_port}/{subdomain}.{d}/{rest}"
        return url

    def _from_proxy_url(self, url: str) -> str:
        """Rewrite a proxy-local URL back to the real Amazon URL (for Referer/Origin)."""
        proxy = self.server_url
        d = self._amazon_domain
        for subdomain in ("www", "alexa"):
            prefix = f"{proxy}/{subdomain}.{d}/"
            if url.startswith(prefix):
                return f"https://{subdomain}.{d}/{url[len(prefix):]}"
        return url

    def _rewrite_body(self, body: str) -> str:
        d = self._amazon_domain
        proxy = self.server_url
        # Full https URLs
        body = re.sub(
            rf'https?://www\.{re.escape(d)}:?[0-9]*/'.replace("/", r"/"),
            f"{proxy}/www.{d}/",
            body,
        )
        body = re.sub(
            rf'https?://alexa\.{re.escape(d)}:?[0-9]*/'.replace("/", r"/"),
            f"{proxy}/alexa.{d}/",
            body,
        )
        # HTML entity encoded slashes
        body = body.replace("&#x2F;", "/")
        # form action relative paths
        body = re.sub(
            r'action="(/[^"]*)"',
            lambda m: f'action="{proxy}{m.group(1)}"',
            body,
        )
        body = re.sub(
            r"action='(/[^']*)'",
            lambda m: f"action='{proxy}{m.group(1)}'",
            body,
        )
        return body

    # ------------------------------------------------------------------
    # Cookie jar helpers (mirrors addCookies in proxy.js)
    # ------------------------------------------------------------------

    def _merge_set_cookie(self, headers: "aiohttp.CIMultiDictProxy[str]") -> None:
        for raw in headers.getall("Set-Cookie", []):
            # parse name=value from the first segment
            m = re.match(r"^([^=]+)=([^;]*)", raw)
            if m:
                name, value = m.group(1).strip(), m.group(2).strip()
                if name == "ap-fid" and value == '""':
                    continue
                self._proxy_cookies[name] = value

    def _cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self._proxy_cookies.items())

    # ------------------------------------------------------------------
    # Initial signin URL (mirrors router '/' in proxy.js)
    # ------------------------------------------------------------------

    def _signin_url(self) -> str:
        handle = _amazon_page_handle(self._amazon_domain)
        lang = self._language.replace("-", "_")
        params = {
            "openid.return_to": f"https://www.{self._amazon_domain}/ap/maplanding",
            "openid.assoc_handle": f"amzn_dp_project_dee_ios{handle}",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "pageId": f"amzn_dp_project_dee_ios{handle}",
            "accountStatusPolicy": "P1",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.mode": "checkid_setup",
            "openid.ns.oa2": f"http://www.{self._amazon_domain}/ap/ext/oauth/2",
            "openid.oa2.client_id": f"device:{self._device_id}",
            "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
            "openid.oa2.response_type": "code",
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.pape.max_auth_age": "0",
            "openid.oa2.scope": "device_auth_access",
            "openid.oa2.code_challenge_method": "S256",
            "openid.oa2.code_challenge": self._code_challenge,
            "language": lang,
        }
        return (
            f"https://www.{self._amazon_domain}/ap/signin?"
            + urllib.parse.urlencode(params)
        )

    # ------------------------------------------------------------------
    # Proxy request handler
    # ------------------------------------------------------------------

    async def _handle_proxy(self, request: web.Request) -> web.Response:
        assert self._upstream_session is not None
        d = self._amazon_domain
        proxy = self.server_url
        path = request.path  # e.g. /www.amazon.in/ap/signin

        # Determine upstream host and real path
        if path == "/":
            # Initial request → redirect to Alexa mobile signin URL
            signin = self._signin_url()
            proxy_signin = self._to_proxy_url(signin + "/")
            # Actually redirect browser to proxied signin
            proxy_signin = (
                f"{proxy}/www.{d}/ap/signin?"
                + urllib.parse.urlencode(urllib.parse.parse_qs(
                    signin.split("?", 1)[1], keep_blank_values=True
                ), doseq=True)
            )
            raise web.HTTPFound(proxy_signin)

        # Parse subdomain and real path from /www.amazon.in/... or /alexa.amazon.in/...
        upstream_base = None
        real_path = path
        if path.startswith(f"/www.{d}/"):
            upstream_base = f"https://www.{d}"
            real_path = path[len(f"/www.{d}"):]
        elif path.startswith(f"/alexa.{d}/"):
            upstream_base = f"https://alexa.{d}"
            real_path = path[len(f"/alexa.{d}"):]
        elif path.startswith(f"/www.{d}"):
            upstream_base = f"https://www.{d}"
            real_path = "/"
        else:
            # fallback
            upstream_base = f"https://www.{d}"
            real_path = path

        query = request.query_string
        upstream_url = upstream_base + real_path
        if query:
            upstream_url += f"?{query}"

        # Build headers for upstream request
        headers: dict[str, str] = {}
        for name, value in request.headers.items():
            nl = name.lower()
            if nl in _HOP_BY_HOP or nl in ("host", "cookie", "content-length"):
                continue
            if nl == "referer":
                value = self._from_proxy_url(value)
            if nl == "origin":
                value = f"https://www.{d}"
            headers[name] = value
        headers["Host"] = upstream_base.replace("https://", "")
        headers["Cookie"] = self._cookie_header()
        headers["Accept-Language"] = self._language
        headers["authority"] = f"www.{d}"

        body = await request.read() if request.method in ("POST", "PUT", "PATCH") else None

        # Skip proxying static assets (mirrors proxy.js skip list)
        skip_exts = (".ico", ".js", ".ttf", ".svg", ".png", ".appcache")
        if any(real_path.endswith(e) for e in skip_exts):
            return web.Response(status=204)

        try:
            async with self._upstream_session.request(
                method=request.method,
                url=upstream_url,
                headers=headers,
                data=body,
                allow_redirects=False,
                ssl=True,
            ) as upstream:
                # Capture Set-Cookie from upstream
                self._merge_set_cookie(upstream.headers)

                location = upstream.headers.get("Location", "")

                # ---- SUCCESS DETECTION (mirrors onProxyRes in proxy.js) ----
                if "/ap/maplanding" in location or "/spa/index.html" in location:
                    cookie_str = self._cookie_header()
                    self._cookie = cookie_str
                    self.save_cookie(cookie_str)
                    if not self._auth_event.is_set():
                        _LOGGER.info("Alexa: login cookie captured successfully")
                        self._auth_event.set()
                    raise web.HTTPFound(f"{proxy}/cookie-success")

                # Build response headers
                resp_headers: dict[str, str] = {}
                for name, value in upstream.headers.items():
                    nl = name.lower()
                    if nl in _HOP_BY_HOP or nl == "set-cookie":
                        continue
                    if nl == "location":
                        # Rewrite redirect target through our proxy
                        if value.startswith("/"):
                            value = f"{proxy}/{upstream_base.replace('https://', '')}{value}"
                        else:
                            value = self._to_proxy_url(value)
                    resp_headers[name] = value

                # Rewrite body for HTML responses
                content_type = upstream.headers.get("Content-Type", "")
                raw = await upstream.read()

                if "text/html" in content_type or "text/javascript" in content_type:
                    try:
                        text = raw.decode("utf-8", errors="replace")
                        text = self._rewrite_body(text)
                        raw = text.encode("utf-8")
                        if "text/html" in content_type:
                            resp_headers["Content-Type"] = "text/html; charset=utf-8"
                    except Exception:  # noqa: BLE001
                        pass

                resp_headers["Content-Length"] = str(len(raw))

                return web.Response(
                    status=upstream.status,
                    headers=resp_headers,
                    body=raw,
                )

        except web.HTTPException:
            raise
        except aiohttp.ClientError as err:
            _LOGGER.error("Proxy upstream error for %s: %s", upstream_url, err)
            return web.Response(status=502, text=f"Proxy error: {err}")

    async def _handle_success(self, request: web.Request) -> web.Response:
        html = """<!DOCTYPE html>
<html><head><title>Alexa — Login Successful</title>
<style>body{font-family:sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;margin:0;background:#1a1a2e;color:#eee;}
.card{text-align:center;padding:40px;background:#16213e;border-radius:16px;}
h1{color:#66bb6a;font-size:2rem;margin-bottom:12px;}
p{color:#aaa;}</style></head>
<body><div class="card">
<h1>✅ Login Successful!</h1>
<p>Your Amazon session has been captured.<br>
Return to Home Assistant and click <strong>Submit</strong> to finish setup.</p>
</div></body></html>"""
        return web.Response(text=html, content_type="text/html")
