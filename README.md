# Alexa Smart Home — Home Assistant Integration

A Home Assistant custom integration that brings your Amazon Alexa smart home devices directly into Home Assistant — no Homebridge, no extra hub required.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

## Supported Device Types

| Alexa Device | Home Assistant Entity |
|---|---|
| Light (on/off, brightness, color, color temp) | `light` |
| Switch / Smart Plug / Outlet | `switch` |
| Fan | `fan` |
| Smart Lock | `lock` |
| Thermostat | `climate` |
| Temperature / Humidity / CO / Air Quality sensor | `sensor` |

## Installation via HACS

1. Install [HACS](https://hacs.xyz) if you haven't already.
2. In Home Assistant go to **HACS → Integrations → ⋮ → Custom repositories**.
3. Add `https://github.com/Swastik2000/ha-alexa-smarthome` with category **Integration**.
4. Click **Download** on the Alexa Smart Home card.
5. Restart Home Assistant.

## Setup

1. Go to **Settings → Integrations → Add Integration → Alexa Smart Home**.
2. Select your Amazon domain and language.
3. **Get your Amazon session cookie:**
   - Open your Amazon domain (e.g. `amazon.com`) in a browser and log in.
   - Open DevTools (`F12`) → **Application** → **Cookies** → select your Amazon domain.
   - Copy all cookie key=value pairs (or use the Network tab → any Amazon request → copy the `Cookie` request header value).
4. Paste the cookie string into the setup form.
5. The integration validates the cookie and discovers your devices automatically.

The cookie is stored locally and only ever sent to Amazon's Alexa API. It typically lasts 14 days — Home Assistant will prompt you to re-authenticate when it expires.

## Configuration Options

| Option | Default | Description |
|---|---|---|
| Amazon domain | `amazon.com` | Your regional Amazon domain |
| Language | `en-US` | Alexa account language/locale |
| State cache TTL | `60` seconds | How long to cache device states |

## How It Works

This integration calls the same private Alexa GraphQL API (`/nexus/v1/graphql`) used by the [homebridge-alexa-smarthome](https://github.com/joeyhage/homebridge-alexa-smarthome) Homebridge plugin, ported to native Python for Home Assistant.

- Device discovery via `EndpointsQuery` GraphQL
- State polling every 30 seconds with a configurable local cache
- Max 2 concurrent API requests (matches Alexa rate limits)
- 65-second request timeout

## License

MIT
