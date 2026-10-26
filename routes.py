"""Responses for mockblox. Reloaded on every request, so it can be edited live.

handle() returns (status, headers, payload); payload may be bytes, str, dict or list.
"""
import base64
import importlib
import json
import os
import time
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

import assets

DATA = os.environ.get("WAXLEY_DATA", os.path.expanduser("~/.local/share/waxley"))
MOCK = os.path.join(DATA, "mock")
VERSION = "0.744.0.7441077"
VERSION_GUID = "version-7aaa6838d36c4448"
OAUTH = "https://apis.roblox.com/oauth"


def mock_json(name):
    with open(os.path.join(MOCK, name)) as f:
        return json.load(f)


USER = mock_json("user.json")


# ---------- OAuth: tokens signed with a local key, published at our jwks ----------

def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt_key():
    path = os.path.join(DATA, "certs", "jwt.key")
    if not os.path.exists(path):
        key = ec.generate_private_key(ec.SECP256R1())
        with open(path, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), None)


KEY = jwt_key()
KID = "waxley"


def jwk():
    n = KEY.public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "kid": KID, "use": "sig", "alg": "ES256",
            "x": b64url(n.x.to_bytes(32, "big")), "y": b64url(n.y.to_bytes(32, "big"))}


def jwt(claims):
    head = b64url(json.dumps({"alg": "ES256", "kid": KID, "typ": "JWT"}).encode())
    body = b64url(json.dumps(claims).encode())
    r, s = decode_dss_signature(KEY.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{head}.{body}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def claims(extra=None):
    now = int(time.time())
    c = {"iss": OAUTH + "/", "sub": str(USER["id"]), "aud": "studio", "iat": now, "nbf": now,
         "exp": now + 3600, "jti": str(uuid.uuid4()), "name": USER["displayName"],
         "nickname": USER["displayName"], "preferred_username": USER["name"],
         "created_at": 1500000000, "profile": f"https://www.roblox.com/users/{USER['id']}/profile"}
    c.update(extra or {})
    return c


def token_response(scope="openid profile"):
    return {"access_token": jwt(claims({"scope": scope})), "refresh_token": jwt(claims({"exp": int(time.time()) + 90 * 86400})),
            "id_token": jwt(claims()), "token_type": "Bearer", "expires_in": 3600, "scope": scope}


def oauth(method, p, body):
    if p == "/oauth/.well-known/openid-configuration":
        return {"issuer": OAUTH + "/",
                "authorization_endpoint": OAUTH + "/v1/authorize",
                "token_endpoint": OAUTH + "/v1/token",
                "introspection_endpoint": OAUTH + "/v1/token/introspect",
                "revocation_endpoint": OAUTH + "/v1/token/revoke",
                "resources_endpoint": OAUTH + "/v1/token/resources",
                "userinfo_endpoint": OAUTH + "/v1/userinfo",
                "jwks_uri": OAUTH + "/v1/certs",
                "registration_endpoint": "https://create.roblox.com/dashboard/credentials",
                "service_documentation": "https://create.roblox.com/docs/reference/cloud",
                "scopes_supported": ["openid", "profile", "email", "verification", "credentials",
                                     "age", "premium", "roles"],
                "response_types_supported": ["none", "code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["ES256"],
                "claims_supported": ["sub", "type", "name", "nickname", "preferred_username",
                                     "created_at", "profile", "email", "email_verified", "verified",
                                     "age_range", "premium", "roles", "internal_user"],
                "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"]}
    if p == "/oauth/v1/certs":
        return {"keys": [jwk()]}
    if p == "/oauth/v1/token":
        return token_response()
    if p == "/oauth/v1/userinfo":
        c = claims()
        info = {k: c[k] for k in ("sub", "name", "nickname", "preferred_username", "created_at", "profile")}
        extra = os.path.join(MOCK, "userinfo_extra.json")
        info.update(mock_json("userinfo_extra.json") if os.path.exists(extra) else
                    {"age_bracket": "Age13OrOver", "roles": [], "internal_user": False})
        return info
    if p == "/oauth/v1/token/introspect":
        return {"active": True, **claims(), "client_id": "studio", "token_type": "Bearer"}
    if p == "/oauth/v1/token/resources":
        return {"resource_infos": [{"owner": {"id": str(USER["id"]), "type": "User"},
                                    "resources": {"universe": {"ids": ["U"]}}}]}
    if p == "/oauth/v1/token/revoke":
        return {}
    return None


SINKS = {"ecsv2.roblox.com", "ephemeralcounters.api.roblox.com", "tracing.roblox.com",
         "client-telemetry.roblox.com", "sentry.prod.rbx.com", "upload.crashes.rbxinfra.com",
         "uploads.backtrace.rbx.com", "lms.roblox.com", "silver.roblox.com"}
SINK_PATHS = ("/experience-signals-ingest/", "/user-heartbeats-api/", "/product-experimentation-platform/",
              "/otel/", "/telemetry")


# ---------- users ----------

def user_obj():
    return {"id": USER["id"], "name": USER["name"], "displayName": USER["displayName"],
            "description": "", "created": "2017-01-01T00:00:00Z", "isBanned": False,
            "hasVerifiedBadge": False, "externalAppDisplayName": None}


def handle(method, host, path, headers, body):
    p = path.split("?")[0]

    # test hook: in-game HttpService posts big outputs here (http://localhost/waxley/dump/<name>[?reset=1])
    if host in ("localhost", "127.0.0.1") and p.startswith("/waxley/dump/") and method == "POST":
        name = os.path.basename(p)
        out = os.path.join(DATA, "run", "dumps")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, name), "wb" if "reset=1" in path else "ab") as f:
            f.write(body)
        return 200, {}, {"ok": True, "bytes": len(body)}

    if host in ("clientsettingscdn.roblox.com", "clientsettings.roblox.com"):
        if "/client-version/" in p:
            return 200, {}, {"version": VERSION, "clientVersionUpload": VERSION_GUID,
                             "bootstrapperVersion": "1, 6, 0, " + VERSION.split(".")[-1],
                             "nextClientVersionUpload": None, "nextClientVersion": None}
        if p.startswith("/v2/settings/application/PCStudioApp"):
            return 200, {}, {"applicationSettings": mock_json("StudioAppSettings.json")}
        if p.startswith("/v2/settings/application/"):
            return 200, {}, {"applicationSettings": {}}

    if host == "apis.roblox.com":
        if p.startswith("/guac-v2/v1/bundles/studio"):
            return 200, {}, mock_json("StudioGuacSettings.json")
        if p.startswith("/oauth/"):
            r = oauth(method, p, body)
            if r is not None:
                return 200, {}, r

    if host == "assetdelivery.roblox.com":
        if p == "/v1/assets/batch":
            return 200, {}, assets.batch(body)
        r = assets.handle(method, host, path)
        if r is not None:
            return r

    if p.lower() == "/my/settings/json":
        return 200, {}, {"UserId": USER["id"], "Name": USER["name"], "DisplayName": USER["displayName"],
                         "IsAdmin": False, "AccountAgeInDays": 3000, "UserAbove13": True,
                         "UseSuperSafeChat": False, "IsAnyBC": False, "IsOBC": False, "IsTBC": False,
                         "IsPremium": False, "IsEmailOnFile": True, "IsEmailVerified": True,
                         "ChangeUsernameEnabled": False, "IsBcRenewalMembership": False}

    if host == "users.roblox.com":
        if p in ("/v1/users/authenticated", f"/v1/users/{USER['id']}"):
            return 200, {}, user_obj()
        if p == "/v1/users":
            return 200, {}, {"data": [user_obj()]}

    # Telemetry / analytics: swallow it
    if host in SINKS or any(p.startswith(x) for x in SINK_PATHS):
        return 200, {}, {}

    # Everything else behaves like that service being down, which Studio already copes with
    # (an empty 200 makes some parsers crash, e.g. Play Solo's chat/blocking services)
    return 503, {}, ""
