#!/usr/bin/env python3
"""Standalone test for Alexa Smart Home API calls.

Run this directly on the HA server:
    python3 /config/test_alexa_api.py

It reads the saved registration data from /config/.alexa_smarthome_cookie
and tests: base URL discovery, CSRF refresh, device query, and a turnOn
mutation for the first switch device found (dry-run by default).

To actually toggle a device, pass --no-dry-run.
"""
import asyncio
import json
import os
import sys
import time
import argparse

try:
    import aiohttp
except ImportError:
    print("ERROR: aiohttp not available. Run inside HA Python environment.")
    sys.exit(1)

COOKIE_FILE = "/config/.alexa_smarthome_cookie"
API_UA = (
    "AppleWebKit PitanguiBridge/2.2.595606.0-"
    "[HARDWARE=iPhone14_7][SOFTWARE=17.4.1][DEVICE=iPhone]"
)
GRAPHQL_PATH = "/nexus/v1/graphql"

SET_ENDPOINT_FEATURES = """mutation updatePowerFeatureForEndpoints(
  $featureControlRequests: [FeatureControlRequest!]!
) {
  setEndpointFeatures(
    setEndpointFeaturesInput: {
      featureControlRequests: $featureControlRequests
    }
  ) {
    featureControlResponses { endpointId featureOperationName }
    errors { endpointId code }
  }
}"""

POWER_QUERY = """query getPowerState($endpointId: String!) {
  endpoint(id: $endpointId) {
    features {
      name
      properties {
        name
        ... on Power { powerStateValue }
      }
    }
  }
}"""


def load_cookie_file():
    if not os.path.exists(COOKIE_FILE):
        print(f"ERROR: Cookie file not found: {COOKIE_FILE}")
        sys.exit(1)
    with open(COOKIE_FILE) as f:
        data = json.load(f)
    print(f"[OK] Loaded cookie file")
    print(f"     localCookie length : {len(data.get('localCookie') or '')}")
    print(f"     csrf               : {'present' if data.get('csrf') else 'MISSING'}")
    print(f"     refreshToken       : {'present' if data.get('refreshToken') else 'MISSING'}")
    print(f"     amazonPage         : {data.get('amazonPage', 'NOT SET')}")
    print()
    return data


async def get_access_token(session, refresh_token):
    if not refresh_token:
        print("[SKIP] No refreshToken — skipping Bearer token fetch")
        return None
    url = "https://api.amazon.com/auth/token"
    data = (
        "app_name=Homebridge&app_version=2.2.595606.0&di.sdk.version=6.12.4"
        f"&source_token={refresh_token}&package_name=com.amazon.echo"
        "&di.hw.version=iPhone&platform=iOS&requested_token_type=access_token"
        "&source_token_type=refresh_token&di.os.name=iOS&di.os.version=16.6"
    )
    headers = {
        "User-Agent": API_UA,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "x-amzn-identity-auth-domain": "api.amazon.com",
    }
    async with session.post(url, data=data, headers=headers) as resp:
        body = await resp.json(content_type=None)
        tok = body.get("access_token")
        if tok:
            print(f"[OK] Bearer access token obtained (expires in {body.get('expires_in')}s)")
            return tok
        print(f"[FAIL] Bearer token fetch failed: {body}")
        return None


async def get_endpoints(session, base_url, local_cookie, csrf):
    url = f"{base_url}/api/endpoints"
    headers = {"User-Agent": API_UA, "Accept": "application/json", "Cookie": local_cookie}
    if csrf:
        headers["csrf"] = csrf
    async with session.get(url, headers=headers) as resp:
        body = await resp.json(content_type=None)
        website_url = body.get("websiteApiUrl")
        print(f"[INFO] /api/endpoints response:")
        print(f"       websiteApiUrl : {website_url}")
        print(f"       full body     : {json.dumps(body, indent=2)[:500]}")
        print()
        return website_url or base_url


async def refresh_csrf(session, base_url, local_cookie):
    url = f"{base_url}/api/language"
    headers = {"User-Agent": API_UA, "Accept": "application/json", "Cookie": local_cookie}
    async with session.get(url, headers=headers) as resp:
        csrf = resp.headers.get("csrf")
        print(f"[INFO] CSRF refresh: status={resp.status} csrf={'present: ' + csrf[:20] if csrf else 'NOT IN HEADER'}")
        return csrf


def build_headers(base_url, local_cookie, csrf, access_token=None):
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json; charset=utf-8",
        "User-Agent": API_UA,
        "Accept-Language": "en-US",
        "Referer": f"{base_url}/spa/index.html",
        "Origin": base_url,
        "Cookie": local_cookie,
    }
    if csrf:
        h["csrf"] = csrf
    if access_token:
        h["Authorization"] = f"Bearer {access_token}"
    return h


async def test_graphql_query(session, base_url, local_cookie, csrf, endpoint_id):
    headers = build_headers(base_url, local_cookie, csrf)
    payload = {"query": POWER_QUERY, "variables": {"endpointId": endpoint_id}}
    async with session.post(f"{base_url}{GRAPHQL_PATH}", json=payload, headers=headers) as resp:
        body = await resp.json(content_type=None)
        print(f"[GraphQL query] status={resp.status}")
        print(f"  response: {json.dumps(body, indent=2)[:400]}")
        return body


async def test_graphql_mutation(session, base_url, local_cookie, csrf, endpoint_id, dry_run):
    action = "turnOn"
    print(f"\n[GraphQL mutation] setEndpointFeatures turnOn for {endpoint_id}")
    if dry_run:
        print("  [DRY RUN] skipping actual call (pass --no-dry-run to execute)")
        return
    headers = build_headers(base_url, local_cookie, csrf)
    variables = {
        "featureControlRequests": [{
            "endpointId": endpoint_id,
            "featureOperationName": action,
            "featureName": "power",
        }]
    }
    async with session.post(
        f"{base_url}{GRAPHQL_PATH}",
        json={"query": SET_ENDPOINT_FEATURES, "variables": variables},
        headers=headers,
    ) as resp:
        body = await resp.json(content_type=None)
        print(f"  status={resp.status}")
        print(f"  response: {json.dumps(body, indent=2)}")


async def test_rest_api(session, base_url, local_cookie, csrf, short_id, access_token, dry_run):
    print(f"\n[REST /api/phoenix/state] turnOn for {short_id}")
    if dry_run:
        print("  [DRY RUN] skipping actual call (pass --no-dry-run to execute)")
        return
    headers = build_headers(base_url, local_cookie, csrf, access_token)
    body = {
        "controlRequests": [{
            "entityId": short_id,
            "entityType": "APPLIANCE",
            "parameters": {"action": "turnOn"},
        }]
    }
    async with session.put(f"{base_url}/api/phoenix/state", json=body, headers=headers) as resp:
        result = await resp.json(content_type=None)
        print(f"  status={resp.status}")
        print(f"  response: {json.dumps(result, indent=2)}")


async def main(dry_run, target_device):
    reg = load_cookie_file()
    local_cookie = reg.get("localCookie") or reg.get("loginCookie", "")
    csrf = reg.get("csrf")
    refresh_token = reg.get("refreshToken")
    amazon_domain = reg.get("amazonPage", "amazon.in")
    base_url = f"https://alexa.{amazon_domain}"

    print(f"=== Starting tests (base_url={base_url}) ===\n")

    async with aiohttp.ClientSession() as session:
        # 1. Get Bearer token
        access_token = await get_access_token(session, refresh_token)
        print()

        # 2. Fetch real endpoints
        real_base = await get_endpoints(session, base_url, local_cookie, csrf)
        if real_base != base_url:
            print(f"[!] Using updated base URL: {real_base}")
            base_url = real_base

        # 3. Refresh CSRF
        fresh_csrf = await refresh_csrf(session, base_url, local_cookie)
        if fresh_csrf:
            csrf = fresh_csrf
        print()

        # 4. Get device list via GraphQL
        print("=== Fetching device list ===")
        endpoints_query = """query Endpoints {
  endpoints { items { id friendlyName
    features { name operations { name }
      properties { name ... on Power { powerStateValue } }
    }
  } }
}"""
        headers = build_headers(base_url, local_cookie, csrf)
        async with session.post(
            f"{base_url}{GRAPHQL_PATH}",
            json={"query": endpoints_query},
            headers=headers,
        ) as resp:
            data = await resp.json(content_type=None)
            items = ((data.get("data") or {}).get("endpoints") or {}).get("items") or []
            print(f"  HTTP status : {resp.status}")
            print(f"  Devices     : {len(items)}")
            if data.get("errors"):
                print(f"  GQL errors  : {data['errors']}")

        # Find target device
        target = None
        for item in items:
            name = item.get("friendlyName", "")
            if target_device.lower() in name.lower():
                target = item
                break
        if not target and items:
            # Fall back to first switch
            for item in items:
                ops = [o.get("name") for f in item.get("features", []) for o in f.get("operations", [])]
                if "turnOn" in ops:
                    target = item
                    break

        if not target:
            print("No suitable device found for control test")
            return

        ep_id = target["id"]
        short_id = ep_id.replace("amzn1.alexa.endpoint.", "")
        print(f"\n=== Testing with device: {target['friendlyName']} ===")
        print(f"  endpointId : {ep_id}")
        print(f"  shortId    : {short_id}")

        # 5. Query state
        print(f"\n[Power state query]")
        await test_graphql_query(session, base_url, local_cookie, csrf, ep_id)

        # 6. Mutation
        await test_graphql_mutation(session, base_url, local_cookie, csrf, ep_id, dry_run)

        # 7. REST API
        await test_rest_api(session, base_url, local_cookie, csrf, short_id, access_token, dry_run)

        print("\n=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Alexa Smart Home API")
    parser.add_argument("--no-dry-run", action="store_true", help="Actually send control commands")
    parser.add_argument("--device", default="G Panel Light One", help="Device name to test")
    args = parser.parse_args()
    asyncio.run(main(dry_run=not args.no_dry_run, target_device=args.device))
