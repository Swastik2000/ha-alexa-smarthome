"""Alexa Smart Home API client using aiohttp for async GraphQL requests."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import (
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    DEFAULT_REQUEST_TIMEOUT,
    EXCLUDED_SKILL_IDS_DEV,
    EXCLUDED_SKILL_IDS_LIVE,
    GRAPHQL_PATH,
)
from .models import CapabilityState, RangeFeatureCapability, SmartHomeDevice

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GraphQL query strings — copied verbatim from the TypeScript source files
# in src/wrapper/graphql/
# ---------------------------------------------------------------------------

ENDPOINTS_QUERY = """query Endpoints {
  endpoints {
    items {
      id
      friendlyName
      displayCategories {
        primary {
          value
        }
      }
      serialNumber {
        value {
          text
        }
      }
      enablement
      model {
        value {
          text
        }
      }
      manufacturer {
        value {
          text
        }
      }
      features {
        name
        instance
        operations {
          name
        }
        properties {
          name
          ... on RangeValue {
            rangeValue {
              value
            }
          }
          ... on TemperatureSensor {
            value {
              value
              scale
            }
          }
          ... on ToggleState {
            toggleStateValue
          }
          ... on Power {
            powerStateValue
          }
          ... on Brightness {
            brightnessStateValue
          }
          ... on Color {
            colorStateValue {
              hue
              saturation
              brightness
            }
          }
          ... on ColorTemperature {
            colorTemperatureInKelvinStateValue
          }
          ... on Lock {
            lockState
          }
          ... on Setpoint {
            value {
              value
              scale
            }
          }
          ... on ThermostatMode {
            thermostatModeValue
          }
        }
        configuration {
          ... on RangeConfiguration {
            friendlyName {
              value {
                text
              }
            }
          }
        }
      }
      endpointReports {
        reporter {
          id
          namespace
          skillStage
        }
      }
    }
  }
}"""

LIGHT_QUERY = """query getPowerBrightnessColorColorTempStates(
  $endpointId: String!
) {
  endpoint(id: $endpointId) {
    features {
      name
      properties {
        name
        ... on Power {
          powerStateValue
        }
        ... on Brightness {
          brightnessStateValue
        }
        ... on Color {
          colorStateValue {
            hue
            saturation
            brightness
          }
        }
        ... on ColorTemperature {
          colorTemperatureInKelvinStateValue
        }
      }
    }
  }
}
"""

POWER_QUERY = """query getPowerState(
  $endpointId: String!
) {
  endpoint(id: $endpointId) {
    features {
      name
      properties {
        name
        ... on Power {
          powerStateValue
        }
      }
    }
  }
}"""

LOCK_QUERY = """query getLockState(
  $endpointId: String!
) {
  endpoint(id: $endpointId) {
    features {
      name
      __typename
      properties {
        name
        ... on Lock {
          lockState
        }
      }
    }
    __typename
  }
}"""

THERMOSTAT_QUERY = """query getThermostatStates(
  $endpointId: String!
) {
  endpoint(id: $endpointId) {
    features {
      name
      properties {
        name
        ... on RangeValue {
          rangeValue {
            value
          }
        }
        ... on Setpoint {
          value {
            value
            scale
          }
        }
        ... on TemperatureSensor {
          value {
            value
            scale
          }
        }
        ... on ThermostatMode {
          thermostatModeValue
        }
      }
      configuration {
        ... on RangeConfiguration {
          friendlyName {
            value {
              text
            }
          }
        }
      }
    }
  }
}"""

TEMP_SENSOR_QUERY = """query getTemperatureStates(
  $endpointId: String!
) {
  endpoint(id: $endpointId) {
    features {
      name
      properties {
        name
        ... on TemperatureSensor {
          value {
            value
            scale
          }
        }
      }
    }
  }
}"""

RANGE_QUERY = """query getRangeStates(
  $endpointId: String!
) {
  endpoint(id: $endpointId) {
    features {
      name
      instance
      properties {
        name
        ... on RangeValue {
          rangeValue {
            value
          }
        }
      }
      configuration {
        ... on RangeConfiguration {
          friendlyName {
            value {
              text
            }
          }
        }
      }
    }
  }
}"""

AIR_QUALITY_QUERY = """query getAirQualityStates(
  $endpointId: String!
) {
  endpoint(id: $endpointId) {
    features {
      name
      properties {
        name
        ... on RangeValue {
          rangeValue {
            value
          }
        }
        ... on TemperatureSensor {
          value {
            value
            scale
          }
        }
        ... on ToggleState {
          toggleStateValue
        }
      }
      configuration {
        ... on RangeConfiguration {
          friendlyName {
            value {
              text
            }
          }
        }
      }
    }
  }
}"""

SET_ENDPOINT_FEATURES = """mutation updatePowerFeatureForEndpoints(
  $featureControlRequests: [FeatureControlRequest!]!
) {
  setEndpointFeatures(
    setEndpointFeaturesInput: {
      featureControlRequests: $featureControlRequests
    }
  ) {
    featureControlResponses {
      endpointId
      featureOperationName
    }
    errors {
      endpointId
      code
    }
  }
}"""


# ---------------------------------------------------------------------------
# Custom exception types
# ---------------------------------------------------------------------------


class AlexaApiError(Exception):
    """Base exception for all Alexa API errors."""


class AlexaAuthError(AlexaApiError):
    """Raised when authentication fails or the session cookie is invalid."""


class AlexaDeviceOfflineError(AlexaApiError):
    """Raised when a device is unreachable or offline."""


# ---------------------------------------------------------------------------
# State extraction helper — ported from src/domain/alexa/get-device-state.ts
# ---------------------------------------------------------------------------


def _extract_states(features: list[dict[str, Any]]) -> list[CapabilityState]:
    """Parse GraphQL feature data into a flat list of CapabilityState objects.

    Mirrors the extractStates() function from get-device-state.ts.
    """
    states: list[CapabilityState] = []

    # Flatten features that have multiple properties into individual entries,
    # matching the TypeScript implementation.
    flat: list[dict[str, Any]] = []
    for feature in features:
        props = feature.get("properties") or []
        if len(props) <= 1:
            flat.append(feature)
        else:
            for prop in props:
                flat.append({**feature, "properties": [prop]})

    for f in flat:
        name = f.get("name", "")
        props = f.get("properties") or []
        prop = props[0] if props else {}
        prop_name = prop.get("name")

        if name == "brightness":
            brightness = prop.get("brightnessStateValue")
            if isinstance(brightness, (int, float)):
                states.append(CapabilityState(
                    feature_name=name,
                    value=brightness,
                    name=prop_name,
                ))

        elif name == "color":
            color_val = prop.get("colorStateValue")
            if isinstance(color_val, dict):
                states.append(CapabilityState(
                    feature_name=name,
                    value=color_val,
                    name=prop_name,
                ))

        elif name == "colorTemperature":
            ct_val = prop.get("colorTemperatureInKelvinStateValue")
            if isinstance(ct_val, (int, float)):
                states.append(CapabilityState(
                    feature_name=name,
                    value=ct_val,
                    name=prop_name,
                ))

        elif name == "lock":
            lock_state = prop.get("lockState")
            if isinstance(lock_state, str):
                states.append(CapabilityState(
                    feature_name=name,
                    value=lock_state,
                    name=prop_name,
                ))

        elif name == "power":
            power_val = prop.get("powerStateValue")
            if isinstance(power_val, str):
                states.append(CapabilityState(
                    feature_name=name,
                    value=power_val,
                    name=prop_name,
                ))

        elif name == "toggle":
            toggle_val = prop.get("toggleStateValue")
            if isinstance(toggle_val, str):
                states.append(CapabilityState(
                    feature_name=name,
                    value=toggle_val,
                    name=prop_name,
                ))

        elif name == "temperatureSensor":
            temp_obj = prop.get("value")
            if isinstance(temp_obj, dict) and isinstance(temp_obj.get("value"), (int, float)):
                states.append(CapabilityState(
                    feature_name=name,
                    value=temp_obj,
                    name=prop_name,
                ))

        elif name == "range":
            instance = f.get("instance")
            range_val_obj = prop.get("rangeValue")
            config = f.get("configuration") or {}
            friendly_name_obj = config.get("friendlyName", {})
            friendly_name = (
                friendly_name_obj.get("value", {}).get("text")
                if isinstance(friendly_name_obj, dict)
                else None
            )
            if (
                isinstance(instance, str)
                and isinstance(range_val_obj, dict)
                and isinstance(range_val_obj.get("value"), (int, float))
            ):
                states.append(CapabilityState(
                    feature_name=name,
                    value=range_val_obj["value"],
                    instance=instance,
                    name=prop_name,
                    range_name=friendly_name,
                ))

        elif name == "thermostat":
            if prop_name == "thermostatMode":
                mode_val = prop.get("thermostatModeValue")
                if isinstance(mode_val, str):
                    states.append(CapabilityState(
                        feature_name=name,
                        value=mode_val,
                        name=prop_name,
                    ))
            elif prop_name in ("targetSetpoint", "upperSetpoint", "lowerSetpoint"):
                setpoint_obj = prop.get("value")
                if isinstance(setpoint_obj, dict) and isinstance(setpoint_obj.get("value"), (int, float)):
                    states.append(CapabilityState(
                        feature_name=name,
                        value=setpoint_obj,
                        name=prop_name,
                    ))

    return states


def _extract_range_features(
    features: list[dict[str, Any]],
) -> list[RangeFeatureCapability]:
    """Extract range feature capability metadata from endpoint features."""
    result: list[RangeFeatureCapability] = []
    for f in features:
        if f.get("name") != "range":
            continue
        instance = f.get("instance")
        config = f.get("configuration") or {}
        friendly_name_obj = config.get("friendlyName", {})
        friendly_name = (
            friendly_name_obj.get("value", {}).get("text")
            if isinstance(friendly_name_obj, dict)
            else None
        )
        if isinstance(instance, str) and isinstance(friendly_name, str):
            result.append(RangeFeatureCapability(
                instance=instance,
                friendly_name=friendly_name,
            ))
    return result


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


_API_USER_AGENT = (
    "AppleWebKit PitanguiBridge/2.2.595606.0-"
    "[HARDWARE=iPhone14_7][SOFTWARE=17.4.1][DEVICE=iPhone]"
)


class AlexaApiClient:
    """Async client for Alexa Smart Home GraphQL API.

    Uses aiohttp with a semaphore limiting max 2 concurrent requests and a
    65-second request timeout, mirroring the TypeScript implementation in
    src/wrapper/alexa-api-wrapper.ts.

    Requires a full registration_data dict (from AlexaAuthManager) containing
    localCookie and csrf — mirrors how alexa-remote2 uses localCookie + csrf header.
    """

    def __init__(
        self,
        amazon_domain: str,
        session: aiohttp.ClientSession,
    ) -> None:
        self._amazon_domain = amazon_domain
        self._base_url = f"https://alexa.{amazon_domain}"
        self._session = session
        self._semaphore = asyncio.Semaphore(DEFAULT_MAX_CONCURRENT_REQUESTS)
        self._timeout = aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT)
        self._local_cookie: str | None = None
        self._csrf: str | None = None
        self._refresh_token: str | None = None
        self._access_token: str | None = None
        self._access_token_expiry: float = 0.0

    def set_registration_data(self, data: dict) -> None:
        """Update auth credentials from full registration data dict."""
        self._local_cookie = data.get("localCookie") or data.get("loginCookie")
        self._csrf = data.get("csrf")
        self._refresh_token = data.get("refreshToken")

    @property
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json; charset=utf-8",
            "User-Agent": _API_USER_AGENT,
            "Accept-Language": "en-US",
            "Referer": f"https://alexa.{self._amazon_domain}/spa/index.html",
            "Origin": f"https://alexa.{self._amazon_domain}",
        }
        if self._local_cookie:
            headers["Cookie"] = self._local_cookie
        if self._csrf:
            headers["csrf"] = self._csrf
        return headers

    async def get_access_token(self) -> str | None:
        """Obtain a Bearer access token from the stored refreshToken.

        Mirrors alexa-remote2's getAuthApiBearerToken() and alexa-cookie2's
        refreshAlexaCookie() — both POST to api.amazon.com/auth/token with
        the refreshToken to get an access_token used for device control.

        The token is cached until its expiry.
        """
        import time

        if self._access_token and time.time() < self._access_token_expiry:
            return self._access_token

        if not self._refresh_token:
            _LOGGER.debug("No refreshToken stored; cannot obtain Bearer access token")
            return None

        url = "https://api.amazon.com/auth/token"
        data = (
            "app_name=Homebridge"
            "&app_version=2.2.595606.0"
            "&di.sdk.version=6.12.4"
            f"&source_token={self._refresh_token}"
            "&package_name=com.amazon.echo"
            "&di.hw.version=iPhone"
            "&platform=iOS"
            "&requested_token_type=access_token"
            "&source_token_type=refresh_token"
            "&di.os.name=iOS"
            "&di.os.version=16.6"
            "&current_version=6.12.4"
        )
        headers = {
            "User-Agent": _API_USER_AGENT,
            "Accept-Language": "en-US",
            "Accept-Charset": "utf-8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "x-amzn-identity-auth-domain": "api.amazon.com",
        }
        try:
            async with self._session.post(
                url, data=data, headers=headers, timeout=self._timeout
            ) as resp:
                body = await resp.json(content_type=None)
                access_token = body.get("access_token")
                expires_in = body.get("expires_in", 3600)
                if access_token:
                    self._access_token = access_token
                    self._access_token_expiry = time.time() + expires_in - 60
                    _LOGGER.debug("Bearer access token obtained (expires in %ss)", expires_in)
                    return access_token
                _LOGGER.warning("Failed to obtain Bearer access token: %s", body)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Error obtaining Bearer access token: %s", err)
        return None

    async def refresh_csrf(self) -> None:
        """Fetch a fresh CSRF token from the Alexa /api/language endpoint.

        The CSRF token is required for state-mutating GraphQL operations.
        alexa-remote2 refreshes this automatically; we replicate that here.
        """
        url = f"{self._base_url}/api/language"
        headers = {
            "User-Agent": _API_USER_AGENT,
            "Accept": "application/json",
        }
        if self._local_cookie:
            headers["Cookie"] = self._local_cookie
        try:
            async with self._session.get(
                url, headers=headers, timeout=self._timeout
            ) as resp:
                csrf = resp.headers.get("csrf")
                if csrf:
                    self._csrf = csrf
                    _LOGGER.debug("CSRF token refreshed (status %s)", resp.status)
                else:
                    _LOGGER.warning(
                        "CSRF refresh: no csrf header in response (status %s)", resp.status
                    )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to refresh CSRF token: %s", err)

    async def _execute_graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query/mutation and return the parsed response."""
        async with self._semaphore:
            payload: dict[str, Any] = {"query": query}
            if variables:
                payload["variables"] = variables

            url = f"{self._base_url}{GRAPHQL_PATH}"
            try:
                async with self._session.post(
                    url,
                    json=payload,
                    headers=self._headers,
                    timeout=self._timeout,
                ) as response:
                    if response.status == 401:
                        raise AlexaAuthError(
                            "Authentication failed (401). Re-authentication required."
                        )
                    if response.status == 403:
                        raise AlexaAuthError(
                            "Access denied (403). Check your session cookie."
                        )
                    response.raise_for_status()
                    data: dict[str, Any] = await response.json(content_type=None)
                    return data
            except aiohttp.ClientResponseError as err:
                raise AlexaApiError(
                    f"HTTP error {err.status} calling Alexa API: {err.message}"
                ) from err
            except asyncio.TimeoutError as err:
                raise AlexaApiError(
                    "Timeout waiting for Alexa API response (>65s)"
                ) from err
            except aiohttp.ClientError as err:
                raise AlexaApiError(
                    f"Network error calling Alexa API: {err}"
                ) from err

    async def get_devices(self) -> list[SmartHomeDevice]:
        """Fetch all smart home devices via EndpointsQuery.

        Filters out Homebridge-related skill devices, matching the
        excludeHomebridgeAlexaPluginDevices logic in AlexaApiWrapper.getDevices().
        """
        response = await self._execute_graphql(ENDPOINTS_QUERY)

        items = (
            (response.get("data") or {})
            .get("endpoints", {})
            or {}
        ).get("items") or []

        if not isinstance(items, list):
            raise AlexaApiError(
                "Unexpected response from Alexa API: endpoints.items is not a list"
            )

        devices: list[SmartHomeDevice] = []
        for endpoint in items:
            # Must have a primary display category
            display_categories = endpoint.get("displayCategories") or {}
            primary = (display_categories.get("primary") or {})
            device_type = primary.get("value")
            if not device_type:
                continue

            # Filter out Homebridge Alexa skill devices
            endpoint_reports = endpoint.get("endpointReports") or []
            if _is_homebridge_skill_device(endpoint_reports):
                _LOGGER.debug(
                    "Skipping Homebridge skill device: %s",
                    endpoint.get("friendlyName"),
                )
                continue

            endpoint_id = endpoint.get("id", "")
            device_id = endpoint_id.replace("amzn1.alexa.endpoint.", "")
            features = endpoint.get("features") or []

            supported_operations: list[str] = []
            for feature in features:
                for op in (feature.get("operations") or []):
                    op_name = op.get("name")
                    if op_name:
                        supported_operations.append(op_name)

            range_features = _extract_range_features(features)

            serial_obj = endpoint.get("serialNumber") or {}
            serial = (serial_obj.get("value") or {}).get("text") or "Unknown"

            model_obj = endpoint.get("model") or {}
            model = (model_obj.get("value") or {}).get("text") or "Unknown"

            mfr_obj = endpoint.get("manufacturer") or {}
            manufacturer = (mfr_obj.get("value") or {}).get("text") or "Amazon"

            devices.append(SmartHomeDevice(
                endpoint_id=endpoint_id,
                id=device_id,
                display_name=endpoint.get("friendlyName", "Unknown Device"),
                supported_operations=supported_operations,
                enabled=endpoint.get("enablement") == "ENABLED",
                device_type=device_type,
                serial_number=serial,
                model=model,
                manufacturer=manufacturer,
                range_features=range_features,
            ))

        return devices

    async def get_device_states(
        self,
        device: SmartHomeDevice,
        query_type: str = "power",
    ) -> list[CapabilityState]:
        """Fetch current states for a device using the appropriate query.

        Args:
            device: The device to query.
            query_type: One of 'light', 'lock', 'thermostat', 'temp_sensor',
                        'range', 'air_quality', or 'power' (default).
        """
        query_map = {
            "light": LIGHT_QUERY,
            "lock": LOCK_QUERY,
            "thermostat": THERMOSTAT_QUERY,
            "temp_sensor": TEMP_SENSOR_QUERY,
            "range": RANGE_QUERY,
            "air_quality": AIR_QUALITY_QUERY,
            "power": POWER_QUERY,
        }
        query = query_map.get(query_type, POWER_QUERY)

        response = await self._execute_graphql(
            query,
            variables={"endpointId": device.endpoint_id},
        )
        features = (
            (response.get("data") or {})
            .get("endpoint", {})
            or {}
        ).get("features") or []

        return _extract_states(features)

    async def _set_device_state_rest(
        self,
        entity_id: str,
        operation_name: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Control a device via the legacy PUT /api/phoenix/state REST endpoint.

        This is the same API used by the Alexa app and alexa-remote2's
        executeSmarthomeDeviceAction(). It is more reliable than the GraphQL
        setEndpointFeatures mutation for third-party skill devices.

        Args:
            entity_id: Short device UUID (without 'amzn1.alexa.endpoint.' prefix).
            operation_name: Alexa action name, e.g. 'turnOn', 'turnOff'.
            payload: Optional extra parameters merged into the action parameters.
        """
        parameters: dict[str, Any] = {"action": operation_name}
        if payload:
            parameters.update(payload)

        body = {
            "controlRequests": [
                {
                    "entityId": entity_id,
                    "entityType": "APPLIANCE",
                    "parameters": parameters,
                }
            ]
        }

        # Build headers; include Bearer token if available — some third-party
        # skill devices (e.g. Tuya) require device-level auth for control.
        rest_headers = dict(self._headers)
        access_token = await self.get_access_token()
        if access_token:
            rest_headers["Authorization"] = f"Bearer {access_token}"

        url = f"{self._base_url}/api/phoenix/state"
        try:
            async with self._session.put(
                url,
                json=body,
                headers=rest_headers,
                timeout=self._timeout,
            ) as response:
                if response.status == 401:
                    raise AlexaAuthError(
                        "Authentication failed (401). Re-authentication required."
                    )
                if response.status == 403:
                    raise AlexaAuthError(
                        "Access denied (403). Check your session cookie."
                    )
                response.raise_for_status()
                data: dict[str, Any] = await response.json(content_type=None)
        except aiohttp.ClientResponseError as err:
            raise AlexaApiError(
                f"HTTP error {err.status} calling /api/phoenix/state: {err.message}"
            ) from err
        except asyncio.TimeoutError as err:
            raise AlexaApiError("Timeout waiting for /api/phoenix/state response") from err
        except aiohttp.ClientError as err:
            raise AlexaApiError(f"Network error calling /api/phoenix/state: {err}") from err

        errors = data.get("errors") or []
        if errors:
            _LOGGER.error("/api/phoenix/state errors for %s: %s", entity_id, errors)
            raise AlexaApiError(f"/api/phoenix/state failed: {errors}")

        _LOGGER.debug("/api/phoenix/state success for %s: %s", entity_id, data.get("controlResponses"))

    async def set_device_state(
        self,
        endpoint_id: str,
        feature_name: str,
        operation_name: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Control a device via GraphQL mutation, falling back to REST if the
        device is reported offline by the GraphQL API.

        Some third-party skill devices (especially in non-US regions) return
        DEVICE_OFFLINE from setEndpointFeatures even though they respond fine
        via the legacy /api/phoenix/state REST endpoint used by the Alexa app.
        """
        _LOGGER.debug(
            "set_device_state: endpointId=%s featureName=%s operationName=%s payload=%s",
            endpoint_id, feature_name, operation_name, payload,
        )

        request: dict[str, Any] = {
            "endpointId": endpoint_id,
            "featureOperationName": operation_name,
            "featureName": feature_name,
        }
        if payload:
            request["payload"] = payload

        response = await self._execute_graphql(
            SET_ENDPOINT_FEATURES,
            variables={"featureControlRequests": [request]},
        )

        # Surface top-level GraphQL errors (auth / schema issues)
        top_errors = response.get("errors")
        if top_errors:
            _LOGGER.error(
                "GraphQL top-level errors for %s: %s", endpoint_id, top_errors
            )
            raise AlexaApiError(f"GraphQL errors: {top_errors}")

        result = (response.get("data") or {}).get("setEndpointFeatures") or {}
        gql_errors = result.get("errors") or []

        if gql_errors:
            error_codes = {e.get("code") for e in gql_errors}
            if "DEVICE_OFFLINE" in error_codes:
                # GraphQL reports device offline — fall back to the REST API
                # which is used by the Alexa app and works for third-party skills.
                _LOGGER.debug(
                    "setEndpointFeatures returned DEVICE_OFFLINE for %s; "
                    "falling back to /api/phoenix/state REST API",
                    endpoint_id,
                )
                short_id = endpoint_id.replace("amzn1.alexa.endpoint.", "")
                await self._set_device_state_rest(short_id, operation_name, payload)
                return

            _LOGGER.error(
                "setEndpointFeatures returned errors for %s: %s", endpoint_id, gql_errors
            )
            raise AlexaApiError(f"setEndpointFeatures failed: {gql_errors}")

        responses = result.get("featureControlResponses") or []
        _LOGGER.debug("setEndpointFeatures success: %s", responses)


def _is_homebridge_skill_device(endpoint_reports: list[dict[str, Any]]) -> bool:
    """Return True if the device was added by the Homebridge Alexa skill.

    Mirrors the excludeHomebridgeAlexaPluginDevices filter in
    AlexaApiWrapper.getDevices().
    """
    for report in endpoint_reports:
        reporter = report.get("reporter") or {}
        skill_stage = (reporter.get("skillStage") or "").lower()
        skill_id = reporter.get("id", "")
        if (
            skill_stage == "development"
            and skill_id == EXCLUDED_SKILL_IDS_DEV
        ) or (
            skill_stage == "live"
            and skill_id == EXCLUDED_SKILL_IDS_LIVE
        ):
            return True
    return False
