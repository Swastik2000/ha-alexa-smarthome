"""Constants for the Alexa Smart Home integration."""

DOMAIN = "alexa_smarthome"

# Configuration keys
CONF_AMAZON_DOMAIN = "amazon_domain"
CONF_LANGUAGE = "language"
CONF_CACHE_TTL = "cache_ttl"
CONF_INCLUDE_DEVICES = "include_devices"
CONF_EXCLUDE_DEVICES = "exclude_devices"
CONF_DISABLED_OPERATIONS = "disabled_operations"
CONF_COOKIE = "cookie"

# Defaults
DEFAULT_AMAZON_DOMAIN = "amazon.com"
DEFAULT_LANGUAGE = "en-US"
DEFAULT_CACHE_TTL = 60  # seconds
DEFAULT_UPDATE_INTERVAL = 30  # seconds
DEFAULT_REQUEST_TIMEOUT = 65  # seconds
DEFAULT_MAX_CONCURRENT_REQUESTS = 2

# Cookie storage
COOKIE_FILENAME = ".alexa_smarthome_cookie"

# GraphQL endpoint
GRAPHQL_PATH = "/nexus/v1/graphql"

# Supported Amazon domains
AMAZON_DOMAINS = [
    "amazon.com",
    "amazon.ca",
    "amazon.de",
    "amazon.es",
    "amazon.fr",
    "amazon.it",
    "amazon.in",
    "amazon.nl",
    "amazon.co.jp",
    "amazon.co.uk",
    "amazon.com.au",
    "amazon.com.br",
    "amazon.com.mx",
]

# Device types from Alexa — mapped to HA platform names.
# Ported from src/domain/alexa/index.ts SupportedDeviceTypes
SUPPORTED_DEVICE_TYPES: dict[str, str] = {
    "LIGHT": "light",
    "SWITCH": "switch",
    "SMARTLOCK": "lock",
    "FAN": "fan",
    "SMARTPLUG": "switch",
    "THERMOSTAT": "climate",
    "ALEXA_VOICE_ENABLED": "sensor",
    "AIR_QUALITY_MONITOR": "sensor",
    "VACUUM_CLEANER": "switch",
    "GAME_CONSOLE": "switch",
    "AIR_FRESHENER": "switch",
}

# Platforms that this integration uses
PLATFORMS = ["light", "switch", "lock", "fan", "climate", "sensor"]

# Supported Alexa feature names — from src/domain/alexa/index.ts SupportedFeatures
FEATURE_BRIGHTNESS = "brightness"
FEATURE_COLOR = "color"
FEATURE_COLOR_TEMPERATURE = "colorTemperature"
FEATURE_LOCK = "lock"
FEATURE_POWER = "power"
FEATURE_RANGE = "range"
FEATURE_TEMPERATURE_SENSOR = "temperatureSensor"
FEATURE_THERMOSTAT = "thermostat"
FEATURE_TOGGLE = "toggle"

# Supported Alexa operations — from src/domain/alexa/index.ts SupportedActions
OPERATION_LOCK = "lock"
OPERATION_UNLOCK = "unlock"
OPERATION_TURN_ON = "turnOn"
OPERATION_TURN_OFF = "turnOff"
OPERATION_SET_BRIGHTNESS = "setBrightness"
OPERATION_SET_COLOR = "setColor"
OPERATION_SET_COLOR_TEMPERATURE = "setColorTemperature"
OPERATION_SET_TARGET_SETPOINT = "setTargetSetpoint"
OPERATION_ADJUST_TARGET_SETPOINT = "adjustTargetSetpoint"
OPERATION_SET_THERMOSTAT_MODE = "setThermostatMode"

SUPPORTED_OPERATIONS = {
    OPERATION_LOCK,
    OPERATION_UNLOCK,
    OPERATION_TURN_ON,
    OPERATION_TURN_OFF,
    OPERATION_SET_BRIGHTNESS,
    OPERATION_SET_COLOR,
    OPERATION_SET_COLOR_TEMPERATURE,
    OPERATION_SET_TARGET_SETPOINT,
    OPERATION_ADJUST_TARGET_SETPOINT,
    OPERATION_SET_THERMOSTAT_MODE,
}

# Alexa thermostat mode values
THERMOSTAT_MODE_HEAT = "HEAT"
THERMOSTAT_MODE_COOL = "COOL"
THERMOSTAT_MODE_AUTO = "AUTO"
THERMOSTAT_MODE_ECO = "ECO"
THERMOSTAT_MODE_OFF = "OFF"

# Alexa lock state values
LOCK_STATE_LOCKED = "LOCKED"
LOCK_STATE_UNLOCKED = "UNLOCKED"
LOCK_STATE_JAMMED = "JAMMED"

# Temperature scales
TEMP_SCALE_CELSIUS = "CELSIUS"
TEMP_SCALE_FAHRENHEIT = "FAHRENHEIT"
TEMP_SCALE_KELVIN = "KELVIN"

# Sensor types derived from Alexa range features
SENSOR_TYPE_TEMPERATURE = "temperature"
SENSOR_TYPE_HUMIDITY = "humidity"
SENSOR_TYPE_CO = "carbon_monoxide"
SENSOR_TYPE_AIR_QUALITY = "air_quality"

# Range feature friendly names that map to sensor types
RANGE_FEATURE_HUMIDITY_NAMES = {"Indoor humidity", "Humidity"}
RANGE_FEATURE_CO_NAMES = {"Carbon Monoxide", "CO"}
RANGE_FEATURE_AIR_QUALITY_NAMES = {"Air Quality", "AQI"}

# Skill IDs to exclude (Homebridge-related skills)
EXCLUDED_SKILL_IDS_DEV = "amzn1.ask.skill.a28c43e1-cba6-4aac-93ca-509e8c7ce39b"
EXCLUDED_SKILL_IDS_LIVE = "amzn1.ask.skill.2af008bb-2bb0-4bef-b131-e191f944a87e"
