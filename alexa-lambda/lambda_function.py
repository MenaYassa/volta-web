"""AWS Lambda Handler for Alexa Smart Home Skill (Volta Controller).

Directives handled:
  - Alexa.Discovery: Discover.Endpoints
  - Alexa.PowerController: TurnOn, TurnOff
  - Alexa: ReportState
"""

import json
import os
import time
import urllib.parse
import urllib.request

VOLTA_BASE_URL = os.environ.get("VOLTA_BASE_URL", "https://volta.your-domain.com").rstrip("/")
DEFAULT_TOKEN = os.environ.get("VOLTA_DEFAULT_TOKEN", "")


def lambda_handler(request, context):
    """Main entry point for Alexa Smart Home directives."""
    print("Received Alexa Directive:", json.dumps(request))

    directive = request.get("directive", {})
    header = directive.get("header", {})
    namespace = header.get("namespace")
    name = header.get("name")
    payload = directive.get("payload", {})

    token = get_auth_token(directive)

    if namespace == "Alexa.Discovery" and name == "Discover":
        return handle_discovery(request, token)

    if namespace == "Alexa.PowerController":
        return handle_power_control(request, token)

    if namespace == "Alexa" and name == "ReportState":
        return handle_report_state(request, token)

    print(f"Unhandled directive: {namespace}:{name}")
    return build_error_response(request, "INVALID_DIRECTIVE", f"Unhandled directive {namespace}:{name}")


def get_auth_token(directive):
    """Extract bearer token from directive scope/endpoint, fallback to DEFAULT_TOKEN."""
    endpoint = directive.get("endpoint", {})
    scope = endpoint.get("scope") or directive.get("payload", {}).get("scope", {})
    token = scope.get("token")
    return token or DEFAULT_TOKEN


def http_request(path, method="GET", data=None, token=""):
    url = f"{VOLTA_BASE_URL}{path}"
    headers = {"Content-Type": "application/json", "User-Agent": "AlexaVoltaSkill/1.0"}
    if token:
        headers["X-Token"] = token

    body_bytes = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def handle_discovery(request, token):
    try:
        res = http_request("/api/alexa/devices", method="GET", token=token)
        endpoints = res.get("endpoints", [])
    except Exception as e:
        print(f"Discovery error: {e}")
        endpoints = []

    header = request["directive"]["header"]
    return {
        "event": {
            "header": {
                "namespace": "Alexa.Discovery",
                "name": "Discover.Response",
                "payloadVersion": "3",
                "messageId": header["messageId"] + "-R",
            },
            "payload": {"endpoints": endpoints},
        }
    }


def handle_power_control(request, token):
    directive = request["directive"]
    header = directive["header"]
    endpoint = directive["endpoint"]
    endpoint_id = endpoint["endpointId"]
    action = header["name"]  # "TurnOn" or "TurnOff"
    turn_on = action == "TurnOn"

    # cookie contains mac and outlet
    cookie = endpoint.get("cookie", {})
    mac = cookie.get("mac")
    outlet_str = cookie.get("outlet", "1")

    if not mac and "_" in endpoint_id:
        mac, outlet_str = endpoint_id.split("_", 1)

    outlet = int(outlet_str)

    try:
        # Call Volta relay toggle endpoint
        res = http_request(
            "/api/onoff",
            method="POST",
            data={"mac": mac, "outlet": outlet, "on": turn_on},
            token=token,
        )
        print("Volta onoff response:", res)
    except Exception as e:
        print(f"Power control error: {e}")
        return build_error_response(request, "ENDPOINT_UNREACHABLE", str(e))

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    power_state_value = "ON" if turn_on else "OFF"

    return {
        "context": {
            "properties": [
                {
                    "namespace": "Alexa.PowerController",
                    "name": "powerState",
                    "value": power_state_value,
                    "timeOfSample": now_iso,
                    "uncertaintyInMilliseconds": 200,
                },
                {
                    "namespace": "Alexa.EndpointHealth",
                    "name": "connectivity",
                    "value": {"value": "OK"},
                    "timeOfSample": now_iso,
                    "uncertaintyInMilliseconds": 200,
                },
            ]
        },
        "event": {
            "header": {
                "namespace": "Alexa",
                "name": "Response",
                "payloadVersion": "3",
                "messageId": header["messageId"] + "-R",
                "correlationToken": header.get("correlationToken", ""),
            },
            "endpoint": {"endpointId": endpoint_id},
            "payload": {},
        },
    }


def handle_report_state(request, token):
    directive = request["directive"]
    header = directive["header"]
    endpoint = directive["endpoint"]
    endpoint_id = endpoint["endpointId"]

    try:
        q_path = f"/api/alexa/state?endpointId={urllib.parse.quote(endpoint_id)}"
        state_info = http_request(q_path, method="GET", token=token)
        power_state = state_info.get("powerState", "OFF")
        connected = state_info.get("connected", True)
        temp_c = float(state_info.get("temp_c", 25.0))
    except Exception as e:
        print(f"Report state error: {e}")
        return build_error_response(request, "ENDPOINT_UNREACHABLE", str(e))

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    properties = [
        {
            "namespace": "Alexa.PowerController",
            "name": "powerState",
            "value": power_state,
            "timeOfSample": now_iso,
            "uncertaintyInMilliseconds": 200,
        },
        {
            "namespace": "Alexa.TemperatureSensor",
            "name": "temperature",
            "value": {"value": temp_c, "scale": "CELSIUS"},
            "timeOfSample": now_iso,
            "uncertaintyInMilliseconds": 1000,
        },
        {
            "namespace": "Alexa.EndpointHealth",
            "name": "connectivity",
            "value": {"value": "OK" if connected else "UNREACHABLE"},
            "timeOfSample": now_iso,
            "uncertaintyInMilliseconds": 200,
        },
    ]

    return {
        "context": {
            "properties": properties
        },
        "event": {
            "header": {
                "namespace": "Alexa",
                "name": "StateReport",
                "payloadVersion": "3",
                "messageId": header["messageId"] + "-R",
                "correlationToken": header.get("correlationToken", ""),
            },
            "endpoint": {"endpointId": endpoint_id},
            "payload": {},
        },
    }


def build_error_response(request, error_type, message):
    header = request.get("directive", {}).get("header", {})
    return {
        "event": {
            "header": {
                "namespace": "Alexa",
                "name": "ErrorResponse",
                "payloadVersion": "3",
                "messageId": header.get("messageId", "err") + "-R",
            },
            "payload": {"type": error_type, "message": message},
        }
    }
