from __future__ import annotations

import json
import os
import ssl
import threading
from datetime import datetime
from functools import wraps
from typing import Any, Iterable, Optional, Tuple

import requests
import jwt
from jwt.algorithms import RSAAlgorithm
from flask import Flask, jsonify, render_template, session, redirect, url_for, request
import paho.mqtt.client as mqtt

# ====================================================================
# Flask app
# ====================================================================

app = Flask(__name__)
app.secret_key = os.environ.get("AEGIS_FLASK_SECRET", "dev-change-me")

# IMPORTANT: bump this whenever you update dashboard.js to force reload in browser
ASSET_VERSION = os.environ.get("AEGIS_ASSET_VERSION", "v8")

# ====================================================================
# Keycloak OIDC configuration
# ====================================================================

KEYCLOAK_BASE_URL = "http://localhost:8080"
KEYCLOAK_REALM = "AEGIS"
KEYCLOAK_CLIENT_ID = "aegis-dashboard"
KEYCLOAK_CLIENT_SECRET = os.environ.get(
    "KEYCLOAK_CLIENT_SECRET", "REPLACE_ME_WITH_REAL_CLIENT_SECRET"
)

DISCOVERY_URL = f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"

discovery = requests.get(DISCOVERY_URL, timeout=5).json()
AUTH_URL = discovery["authorization_endpoint"]
TOKEN_URL = discovery["token_endpoint"]
JWKS_URL = discovery["jwks_uri"]
ISSUER = discovery["issuer"]

_jwks_cache = None


def get_jwks():
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = requests.get(JWKS_URL, timeout=5).json()["keys"]
    return _jwks_cache


def decode_and_verify(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")

    key_data = None
    for key in get_jwks():
        if key.get("kid") == kid:
            key_data = key
            break
    if key_data is None:
        raise RuntimeError("No matching JWK for token")

    public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
    claims = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=KEYCLOAK_CLIENT_ID,
        issuer=ISSUER,
    )
    return claims


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def api_login_required(fn):
    """Return JSON 401 for fetch calls (avoid HTML redirect breaking res.json())."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


# ====================================================================
# MQTT settings (HiveMQ Cloud)
# ====================================================================

MQTT_BROKER = "221c1701e9df426cb5ed49185d5fccc0.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "aegis"
MQTT_PASS = "SuperStrongPass1"
MQTT_TOPIC = "aegis/telemetry/#"

# ====================================================================
# Rule-based risk assumptions (MORE SENSITIVE TO BRIGHTNESS)
# ====================================================================

TEMP_MIN = 18.0
TEMP_MAX = 24.0

# “normal-ish indoor” band for demo
BRIGHT_MIN = 15.0
BRIGHT_MAX = 85.0

TEMP_HARD_LOW = 10.0
TEMP_HARD_HIGH = 35.0

# key change: treat very low brightness as abnormal (covered sensor)
BRIGHT_HARD_LOW = 5.0
BRIGHT_HARD_HIGH = 100.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _penalty(value: float, lo: float, hi: float, hard_lo: float, hard_hi: float) -> float:
    v = float(value)
    if v <= hard_lo or v >= hard_hi:
        return 1.0
    if lo <= v <= hi:
        return 0.0
    if v < lo:
        return _clamp((lo - v) / (lo - hard_lo), 0.0, 1.0)
    return _clamp((v - hi) / (hard_hi - hi), 0.0, 1.0)


def compute_risk_rule(temp_c: float, brightness_pct: float) -> Tuple[int, str]:
    t_pen = _penalty(temp_c, TEMP_MIN, TEMP_MAX, TEMP_HARD_LOW, TEMP_HARD_HIGH)
    b_pen = _penalty(brightness_pct, BRIGHT_MIN, BRIGHT_MAX, BRIGHT_HARD_LOW, BRIGHT_HARD_HIGH)

    # brightness weighted heavier so “cover sensor” spikes the risk
    combined = (0.40 * t_pen) + (0.60 * b_pen)
    score = int(round(_clamp(combined, 0.0, 1.0) * 100))

    # more sensitive thresholds
    if score < 20:
        label = "normal"
    elif score < 50:
        label = "elevated"
    else:
        label = "high"

    return score, label


def infer_node_from_topic(topic: str) -> Optional[str]:
    t = topic.lower()
    if "node1" in t or "node-1" in t:
        return "node-1"
    if "node2" in t or "node-2" in t:
        return "node-2"
    return None


# ====================================================================
# Robust extraction helpers
# ====================================================================

TEMP_KEYS = ("temp", "temperature", "temp_c", "tempc", "t_c")
BRIGHT_KEYS = ("brightness", "brightness_pct", "light", "light_pct", "illum", "illuminance", "lux", "ldr")


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.strip().replace("%", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _walk_kv(obj: Any) -> Iterable[Tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk_kv(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_kv(item)


def extract_temp_and_brightness(payload: dict) -> Tuple[Optional[float], Optional[float]]:
    temp_val = None
    bright_val = None
    lux_val = None

    sensors = payload.get("sensors") if isinstance(payload.get("sensors"), dict) else {}

    direct_candidates = [
        ("temp_c", sensors.get("temp_c")),
        ("temp_c", payload.get("temp_c")),
        ("brightness_pct", sensors.get("brightness_pct")),
        ("brightness_pct", payload.get("brightness_pct")),
        ("lux", sensors.get("lux")),
        ("lux", payload.get("lux")),
    ]

    for k, v in direct_candidates:
        f = _to_float(v)
        if f is None:
            continue
        lk = k.lower()
        if lk.startswith("temp") and temp_val is None:
            temp_val = f
        if ("bright" in lk or "light" in lk) and bright_val is None:
            bright_val = f
        if "lux" in lk and lux_val is None:
            lux_val = f

    if temp_val is None or (bright_val is None and lux_val is None):
        for k, v in _walk_kv(payload):
            lk = str(k).lower()
            f = _to_float(v)
            if f is None:
                continue

            if temp_val is None and any(tk in lk for tk in TEMP_KEYS):
                if -50.0 <= f <= 100.0:
                    temp_val = f

            if bright_val is None and any(bk in lk for bk in BRIGHT_KEYS):
                if "lux" in lk or f > 100.0:
                    if lux_val is None:
                        lux_val = f
                else:
                    if 0.0 <= f <= 100.0:
                        bright_val = f

            if temp_val is not None and (bright_val is not None or lux_val is not None):
                break

    if bright_val is None and lux_val is not None:
        bright_val = _clamp((lux_val / 1000.0) * 100.0, 0.0, 100.0)

    return temp_val, bright_val


# ====================================================================
# Global state
# ====================================================================

state = {
    "backend_ok": True,
    "mqtt_connected": False,
    "node-1": None,
    "node-2": None,
    "ai_risk": {"score": None, "label": "idle", "last_update": None},
    "ai_summary": {"last_score": None, "last_label": "idle", "last_update": None},
}

# ====================================================================
# MQTT callbacks
# ====================================================================

def on_connect(client, userdata, flags, rc):
    print("MQTT connected with code:", rc)
    state["mqtt_connected"] = (rc == 0)
    if rc == 0:
        client.subscribe(MQTT_TOPIC)


def on_disconnect(client, userdata, rc):
    state["mqtt_connected"] = False


def on_message(client, userdata, msg):
    payload_raw = msg.payload.decode("utf-8", errors="ignore").strip()
    topic = msg.topic

    now = datetime.now()
    ts_epoch = int(now.timestamp())
    ts_iso = now.strftime("%Y-%m-%d %H:%M:%S")

    if topic.endswith("/status"):
        node = infer_node_from_topic(topic)
        if node:
            existing = state.get(node) or {"node": node}
            existing.update({"ts": ts_epoch, "ts_iso": ts_iso, "status": payload_raw or "unknown"})
            state[node] = existing
        return

    try:
        data = json.loads(payload_raw)
    except Exception:
        return

    node = data.get("node") or infer_node_from_topic(topic)

    if node == "node-1":
        temp_c, brightness_pct = extract_temp_and_brightness(data)

        sensors = data.get("sensors") if isinstance(data.get("sensors"), dict) else {}
        merged_sensors = dict(sensors)
        if temp_c is not None:
            merged_sensors["temp_c"] = temp_c
        if brightness_pct is not None:
            merged_sensors["brightness_pct"] = brightness_pct

        risk_score = None
        risk_label = "idle"

        if temp_c is not None and brightness_pct is not None:
            risk_score, risk_label = compute_risk_rule(temp_c, brightness_pct)
            state["ai_risk"] = {"score": risk_score, "label": risk_label, "last_update": ts_iso}
            state["ai_summary"] = {"last_score": risk_score, "last_label": risk_label, "last_update": ts_iso}

        # always mirror into node-1.ai too
        state["node-1"] = {
            "node": "node-1",
            "ts": ts_epoch,
            "ts_iso": ts_iso,
            "sensors": merged_sensors,
            "ai": {"score": risk_score, "label": risk_label},
            "status": data.get("status", (state.get("node-1") or {}).get("status", "online")),
        }

        print(f"[NODE1] temp={temp_c} bright={brightness_pct} -> risk={risk_score} label={risk_label}")

    elif node == "node-2":
        cam = data.get("camera") if isinstance(data.get("camera"), dict) else {}
        state["node-2"] = {
            "node": "node-2",
            "ts": ts_epoch,
            "ts_iso": ts_iso,
            "camera": cam,
            "status": data.get("status", (state.get("node-2") or {}).get("status", "online")),
        }


# ====================================================================
# MQTT thread
# ====================================================================

def mqtt_thread():
    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)

    client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    client.tls_insecure_set(False)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_forever()
    except Exception as e:
        print("[MQTT] thread crashed:", e)
        state["mqtt_connected"] = False


# ====================================================================
# Auth routes
# ====================================================================

@app.route("/login")
def login():
    next_url = request.args.get("next") or url_for("dashboard")
    session["oauth_next"] = next_url

    state_val = os.urandom(16).hex()
    session["oauth_state"] = state_val

    redirect_uri = url_for("oidc_callback", _external=True)

    params = {
        "client_id": KEYCLOAK_CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": redirect_uri,
        "state": state_val,
    }

    from urllib.parse import urlencode
    return redirect(f"{AUTH_URL}?{urlencode(params)}")


@app.route("/oidc/callback")
def oidc_callback():
    error = request.args.get("error")
    if error:
        return f"Login error: {error}", 400

    code = request.args.get("code")
    state_val = request.args.get("state")

    if not code or not state_val or state_val != session.get("oauth_state"):
        return "Invalid OIDC state", 400

    redirect_uri = url_for("oidc_callback", _external=True)

    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": KEYCLOAK_CLIENT_ID,
        "client_secret": KEYCLOAK_CLIENT_SECRET,
    }

    token_resp = requests.post(TOKEN_URL, data=token_data, timeout=5)
    if token_resp.status_code != 200:
        return f"Token endpoint error: {token_resp.text}", 400

    tokens = token_resp.json()
    id_token = tokens.get("id_token")
    access_token = tokens.get("access_token")

    claims = decode_and_verify(id_token)

    session["user"] = {
        "sub": claims.get("sub"),
        "username": claims.get("preferred_username"),
        "email": claims.get("email"),
        "roles": claims.get("realm_access", {}).get("roles", []),
    }
    session["id_token"] = id_token
    session["access_token"] = access_token

    next_url = session.pop("oauth_next", url_for("dashboard"))
    return redirect(next_url)


@app.route("/logout")
def logout():
    session.clear()
    redirect_uri = url_for("dashboard", _external=True)
    end_session = f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout"
    from urllib.parse import urlencode
    params = {"post_logout_redirect_uri": redirect_uri, "client_id": KEYCLOAK_CLIENT_ID}
    return redirect(f"{end_session}?{urlencode(params)}")


# ====================================================================
# Flask routes
# ====================================================================

@app.route("/")
@login_required
def dashboard():
    # cache-bust dashboard.js via query string
    return render_template("dashboard.html", asset_v=ASSET_VERSION)


@app.route("/api/state")
@api_login_required
def api_state():
    return jsonify(state)


# ====================================================================
# Main
# ====================================================================

if __name__ == "__main__":
    t = threading.Thread(target=mqtt_thread, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=True)
