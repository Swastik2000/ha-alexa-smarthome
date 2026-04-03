"""Authentication manager for the Alexa Smart Home integration.

Provides a local HTTP server that:
1. Serves a setup page with a drag-and-drop bookmarklet
2. User logs in to Amazon normally in their browser
3. User clicks the bookmarklet once — it extracts document.cookie and POSTs
   it back to the local server automatically
4. Server signals the config flow; setup completes without any manual copying

The cookie is persisted to disk and reused until a 401/403 triggers re-auth.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from aiohttp import web

from .const import COOKIE_FILENAME

_LOGGER = logging.getLogger(__name__)


class AlexaAuthError(Exception):
    """Raised when authentication cannot be completed."""


class AlexaAuthManager:
    """Manages Amazon session cookie acquisition via a bookmarklet proxy server."""

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
        self._cookie: str | None = None
        self._runner: web.AppRunner | None = None
        self._auth_event: asyncio.Event = asyncio.Event()

    @property
    def cookie_path(self) -> str:
        return self._cookie_path

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
        data: dict[str, Any] = {"cookie": cookie, "amazon_domain": self._amazon_domain}
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
    # Local HTTP server
    # ------------------------------------------------------------------

    async def start_server(self) -> str:
        """Start the local auth server. Returns the URL to open."""
        if self._runner is not None:
            return self.server_url

        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_post("/cookie", self._handle_cookie)
        app.router.add_get("/status", self._handle_status)
        app.router.add_get("/success", self._handle_success)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._proxy_port)
        await site.start()
        _LOGGER.info("Alexa auth server started at %s", self.server_url)
        return self.server_url

    async def stop_server(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def wait_for_cookie(self, timeout: float = 600.0) -> str:
        """Block until the bookmarklet submits the cookie."""
        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as err:
            raise AlexaAuthError("Authentication timed out") from err
        if not self._cookie:
            raise AlexaAuthError("No cookie received")
        return self._cookie

    # ------------------------------------------------------------------
    # Request handlers
    # ------------------------------------------------------------------

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the setup page with the bookmarklet."""
        server_url = self.server_url
        amazon_url = f"https://www.{self._amazon_domain}"

        # The bookmarklet JS — runs on amazon.com, POSTs document.cookie back
        bookmarklet_js = (
            "javascript:(function(){"
            f"fetch('http://{self._ha_host}:{self._proxy_port}/cookie',{{"
            "method:'POST',"
            "headers:{'Content-Type':'application/json'},"
            f"body:JSON.stringify({{cookie:document.cookie,domain:'{self._amazon_domain}'}})"
            "}}).then(r=>r.json()).then(d=>{"
            "if(d.status==='ok'){alert('✅ Alexa Smart Home: Login captured! Return to Home Assistant to finish setup.');}"
            "else{alert('❌ Error: '+JSON.stringify(d));}"
            "}).catch(e=>alert('❌ Failed to send cookie: '+e));"
            "})()"
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Alexa Smart Home — Login</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #1a1a2e; color: #eee; min-height: 100vh;
           display: flex; align-items: center; justify-content: center; padding: 20px; }}
    .card {{ background: #16213e; border-radius: 16px; padding: 40px;
             max-width: 560px; width: 100%; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }}
    h1 {{ font-size: 1.6rem; margin-bottom: 8px; color: #4fc3f7; }}
    .subtitle {{ color: #aaa; margin-bottom: 32px; font-size: 0.95rem; }}
    .step {{ display: flex; gap: 16px; margin-bottom: 28px; align-items: flex-start; }}
    .step-num {{ background: #4fc3f7; color: #000; width: 32px; height: 32px;
                 border-radius: 50%; display: flex; align-items: center;
                 justify-content: center; font-weight: bold; flex-shrink: 0; }}
    .step-body h3 {{ font-size: 1rem; margin-bottom: 6px; }}
    .step-body p {{ color: #bbb; font-size: 0.9rem; line-height: 1.5; }}
    .bookmarklet {{
      display: inline-block; margin-top: 10px;
      padding: 10px 20px; background: #ff9900; color: #000;
      border-radius: 8px; font-weight: bold; text-decoration: none;
      font-size: 0.95rem; cursor: grab; border: 3px dashed #ffcc44;
    }}
    .bookmarklet:active {{ cursor: grabbing; }}
    .hint {{ font-size: 0.8rem; color: #888; margin-top: 6px; }}
    .amazon-btn {{
      display: inline-block; margin-top: 10px;
      padding: 12px 24px; background: #ff9900; color: #000;
      border-radius: 8px; font-weight: bold; text-decoration: none;
      font-size: 1rem;
    }}
    .status-box {{ background: #0d1b2a; border-radius: 8px; padding: 16px;
                   margin-top: 24px; text-align: center; }}
    #status {{ color: #aaa; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Alexa Smart Home</h1>
    <p class="subtitle">Connect your Amazon account to Home Assistant</p>

    <div class="step">
      <div class="step-num">1</div>
      <div class="step-body">
        <h3>Add the bookmarklet to your browser</h3>
        <p>Drag the button below to your browser's bookmarks bar.</p>
        <a class="bookmarklet" href="{bookmarklet_js}">📦 Send to HA</a>
        <p class="hint">Drag this to your bookmarks bar — you'll click it after logging in.</p>
      </div>
    </div>

    <div class="step">
      <div class="step-num">2</div>
      <div class="step-body">
        <h3>Log in to Amazon</h3>
        <p>Open Amazon and sign in to the account linked to your Alexa devices.</p>
        <a class="amazon-btn" href="{amazon_url}" target="_blank">Open {self._amazon_domain} →</a>
      </div>
    </div>

    <div class="step">
      <div class="step-num">3</div>
      <div class="step-body">
        <h3>Click the bookmarklet</h3>
        <p>Once logged in on Amazon, click the <strong>📦 Send to HA</strong> bookmarklet you just added.
        A popup will confirm success, then return to Home Assistant — setup completes automatically.</p>
      </div>
    </div>

    <div class="status-box">
      <div id="status">⏳ Waiting for login…</div>
    </div>
  </div>

  <script>
    // Poll for completion every 3 seconds
    setInterval(async () => {{
      try {{
        const r = await fetch('/status');
        const d = await r.json();
        if (d.authenticated) {{
          document.getElementById('status').innerHTML =
            '✅ <strong>Login captured!</strong> Return to Home Assistant to finish setup.';
          document.getElementById('status').style.color = '#66bb6a';
        }}
      }} catch(e) {{}}
    }}, 3000);
  </script>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def _handle_cookie(self, request: web.Request) -> web.Response:
        """Receive the cookie POSTed by the bookmarklet."""
        # Allow cross-origin requests from Amazon
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
        if request.method == "OPTIONS":
            return web.Response(headers=headers)

        try:
            body = await request.json()
            cookie = (body.get("cookie") or "").strip()
            if not cookie:
                return web.Response(
                    status=400,
                    text=json.dumps({"status": "error", "message": "No cookie received"}),
                    content_type="application/json",
                    headers=headers,
                )
            self._cookie = cookie
            self.save_cookie(cookie)
            self._auth_event.set()
            _LOGGER.info("Alexa session cookie received via bookmarklet")
            return web.Response(
                text=json.dumps({"status": "ok"}),
                content_type="application/json",
                headers=headers,
            )
        except Exception as err:  # noqa: BLE001
            return web.Response(
                status=400,
                text=json.dumps({"status": "error", "message": str(err)}),
                content_type="application/json",
                headers=headers,
            )

    async def _handle_status(self, request: web.Request) -> web.Response:
        return web.Response(
            text=json.dumps({"authenticated": bool(self._cookie)}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_success(self, request: web.Request) -> web.Response:
        return web.Response(
            text="<h1>✅ Done! Return to Home Assistant.</h1>",
            content_type="text/html",
        )
