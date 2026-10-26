"""wick's routes: what Sober and the Android app need, in front of Waxley's routes.py.

handle() returns (status, headers, payload) like routes.handle(), or falls through to it.
"""
import importlib
import json
import os
import re
import time

import routes as base

DATA = os.environ.get("WAXLEY_DATA", os.path.expanduser("~/.local/share/waxley"))
WD = os.path.join(DATA, "wick")
_base_mtime = [os.path.getmtime(base.__file__)]


def wick_json(name):
    with open(os.path.join(WD, "mock", name)) as f:
        return json.load(f)


def base_routes():
    m = os.path.getmtime(base.__file__)
    if m != _base_mtime[0]:
        _base_mtime[0] = m
        importlib.reload(base)
    return base


def user():
    return base_routes().USER


ENGLISH = {"id": 1, "locale": "en_us", "localeCode": "en-us", "name": "English(US)",
           "nativeName": "English", "language": {"id": 41, "name": "English", "nativeName": "English",
                                                 "languageCode": "en", "isRightToLeft": False}}


def json_body(body):
    try:
        return json.loads(body or b"{}")
    except ValueError:
        return {}


def local_flags():
    try:
        flags = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fflags.json")))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in flags.items() if not k.startswith("_")}


def android_flags():
    """The engine's server-side FastFlags: sober/fflags.json (FFlagX: true), sent as both FFlag and DFFlag,
    in the "True"/"False" string form Roblox's settings service uses."""
    try:
        flags = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fflags.json")))
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in flags.items():
        if k.startswith("_"):
            continue
        v = ("True" if v else "False") if isinstance(v, bool) else str(v)
        out[k] = v
        for a, b in (("FFlag", "DFFlag"), ("DFFlag", "FFlag")):
            if k.startswith(a) and not k.startswith("DFFlag" if a == "FFlag" else "~"):
                out.setdefault(b + k[len(a):], v)
    return out


SERVER_ADDR = "10.77.0.2"  # os0, the namespace's fake LAN address the Studio server listens on
JOB_ID = "00000000-0000-4000-8000-00000000wick"


def rbx_transport_port():
    """The Studio server's RbxTransport (QUIC) port: random per start, so read it from its log."""
    logs = os.path.join(WD, "server-appdata", "Roblox", "logs")
    try:
        newest = max((os.path.join(logs, f) for f in os.listdir(logs)), key=os.path.getmtime)
        m = re.findall(r"Started RbxTransport network server [^|]*\|(\d+)", open(newest, errors="replace").read())
        return int(m[-1]) if m else 0
    except (OSError, ValueError):
        return 0


def join_response(req):
    u = user()
    place = int(req.get("placeId") or os.environ.get("PLACE_ID", "1"))
    port = int(os.environ.get("SERVER_PORT", "53650"))
    session = json.dumps({"SessionId": JOB_ID, "GameId": JOB_ID, "PlaceId": place, "ClientIpAddress": "10.77.0.2",
                          "PlatformTypeId": 2, "SessionStarted": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "BrowserTrackerId": 1, "PartyId": None, "Age": 30.0, "Latitude": 0, "Longitude": 0,
                          "CountryId": 1, "LanguageId": 41, "UserScope": None})
    script = {
        "ClientPort": 0, "MachineAddress": SERVER_ADDR, "ServerPort": port,
        "ServerConnections": [{"Address": SERVER_ADDR, "Port": port}],
        "UdmuxEndpoints": [], "DirectServerReturn": True, "TokenGenAlgorithm": 0, "PepperId": 0,
        "TokenValue": "", "PingUrl": "", "PingInterval": 0,
        "UserName": u["name"], "DisplayName": u["displayName"], "HasVerifiedBadge": False,
        "SeleniumTestMode": False, "UserId": u["id"], "RobloxLocale": "en_us", "GameLocale": "en_us",
        "SuperSafeChat": False, "FlexibleChatEnabled": False, "CharacterAppearance": "",
        "ClientTicket": "", "GameId": JOB_ID, "PlaceId": place, "BaseUrl": "https://assetgame.roblox.com/",
        "ChatStyle": "ClassicAndBubble", "CreatorId": u["id"], "CreatorTypeEnum": "User",
        "MembershipType": "None", "AccountAge": 3000, "CookieStoreFirstTimePlayKey": "rbx_evt_ftp",
        "CookieStoreFiveMinutePlayKey": "rbx_evt_fmp", "CookieStoreEnabled": False,
        "IsUnknownOrUnder13": False, "GameChatType": "AllUsers", "SessionId": session,
        "AnalyticsSessionId": JOB_ID, "DataCenterId": 0, "UniverseId": place, "FollowUserId": 0,
        "characterAppearanceId": u["id"], "CountryCode": "US", "AlternateName": "", "RandomSeed1": "",
        "ClientPublicKeyData": "", "RccVersion": "", "ChannelName": "", "VerifiedAMP": 0,
        "PrivateServerOwnerID": 0, "PrivateServerID": "", "EventId": "", "EphemeralEarlyPubKey": "",
        "PartyId": "",
        # RbxTransport instead of RakNet: RakNet's connection password differs between the Android
        # engine's version and Studio's, the QUIC transport takes Studio's local test token
        "NetStackTokenValue": "bG9jYWxfdGVzdA==", "NetStackPort": rbx_transport_port(),
    }
    return {"jobId": JOB_ID, "status": 2, "statusData": None, "joinScriptUrl": None,
            "authenticationUrl": "https://auth.roblox.com/v1/authentication-ticket/redeem",
            "authenticationTicket": "wick", "message": None, "joinScript": script, "queuePosition": 0}


def handle(method, host, path, headers, body):
    p = path.split("?")[0]
    u = user()

    # Sober won't start without its remote manifest: a snapshot of the real one
    # ({"v1": {"<sober build tag>": {...}}}; a newer Sober build needs a fresh snapshot)
    if (host, p) in (("sober.vinegarhq.org", "/app"),
                     ("raw.githubusercontent.com", "/vinegarhq/sober/refs/heads/gh-pages/app")):
        # plus wick's FastFlags as Sober "remote app settings overrides", the one way Sober itself applies
        # flags to the engine at startup (its config.json fflags don't reach every flag)
        manifest = wick_json("sober_manifest.json")
        flags = local_flags()
        for build in manifest.get("v1", {}).values():
            build.setdefault("app_settings", {}).update(flags)
        return 200, {"Content-Type": "application/octet-stream"}, manifest

    # ---- app start-up ----
    if host == "users.roblox.com" and p == "/v1/users/authenticated/app-launch-info":
        return 200, {}, {"id": u["id"], "userId": u["id"], "name": u["name"], "displayName": u["displayName"],
                         "countryCode": "US", "ageBracket": 0, "isUnder13": False, "isPremium": False,
                         "membershipType": 0, "hasRobloxSubscription": False, "created": "2017-01-01T00:00:00Z"}
    if host in ("clientsettings.roblox.com", "clientsettingscdn.roblox.com"):
        if p.startswith("/v2/settings/application/GoogleAndroidApp"):
            return 200, {}, {"applicationSettings": android_flags()}
        if p == "/v2/user-channel":
            return 200, {}, {"channelName": "LIVE", "channelAssignmentType": "None", "token": None}
        if p == "/v1/mobile-client-version":
            return 200, {}, {"data": {"UpgradeAction": "NotRequired"}, "UpgradeAction": "NotRequired"}
    if host == "locale.roblox.com":
        if p == "/v1/locales/user-localization-locus-supported-locales":
            return 200, {}, {"signupAndLogin": ENGLISH, "generalExperience": ENGLISH, "ugc": ENGLISH}
        if p == "/v1/locales/supported-locales":
            return 200, {}, {"data": [ENGLISH]}
        if p in ("/v1/locales/user-locale", "/v1/locales"):
            return 200, {}, {"supportedLocale": ENGLISH, "nativeLanguageSupportedLocale": ENGLISH,
                             "signupAndLogin": ENGLISH, "generalExperience": ENGLISH, "ugc": ENGLISH}
    if host == "apis.roblox.com":
        if p == "/maintenance-status/v1/alerts/alert-info":
            return 200, {}, {"IsVisible": False, "Text": "", "LinkText": "", "LinkUrl": ""}
        if p.startswith("/guac-v2/v1/bundles/"):
            name = os.path.basename(p)
            f = os.path.join(WD, "mock", f"guac_{name}.json")
            return 200, {}, wick_json(f"guac_{name}.json") if os.path.exists(f) else {}
        if p.startswith(("/attribution/", "/browser-tracker-api/")):
            return 200, {}, {"browserTrackerId": 1} if "browser-tracker" in p else {}
        if p == "/platform-chat-api/v1/get-conversation-metadata":
            return 200, {}, {"global_unread_message_count": 0, "global_unread_conversation_count": 0}

    # ---- joining the hosted place: every join goes to wick's local server ----
    if host == "gamejoin.roblox.com" and p.startswith("/v1/join-"):
        return 200, {}, join_response(json_body(body))

    # ---- thumbnails: everything "Blocked" (no image) so the app shows its placeholders ----
    if host == "thumbnails.roblox.com":
        if p == "/v1/batch":
            reqs = json_body(body)
            reqs = reqs if isinstance(reqs, list) else []
            return 200, {}, {"data": [{"requestId": r.get("requestId"), "errorCode": 0, "errorMessage": "",
                                       "targetId": r.get("targetId", 0), "state": "Blocked", "imageUrl": None,
                                       "version": None} for r in reqs]}
        return 200, {}, {"data": []}

    return base_routes().handle(method, host, path, headers, body)
