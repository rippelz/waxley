"""Local asset store for Waxley: rbxassetid://<id> -> bytes on this machine.

Lookup order for an asset id:
  1. ~/.local/share/waxley/assets/<id>[.ext]   (drop files here, name = asset id; tools/bo3_local.py
     fills it from a bo3map export: .mesh, .rbxmx models, .decal.rbxmx, images)
  2. the rbxasset upload ledger (your own uploads -> the original local file)
"""
import hashlib
import json
import os
import subprocess
import threading
import urllib.parse

DATA = os.environ.get("WAXLEY_DATA", os.path.expanduser("~/.local/share/waxley"))
STORE = os.path.join(DATA, "assets")
LEDGER = os.path.expanduser("~/.local/share/rbxasset/ledger.json")
LOCAL = "https://assetdelivery.roblox.com/offline/"

# Roblox AssetType ids
IMAGE, AUDIO, MESH, MODEL, DECAL = 1, 3, 4, 10, 13


def sniff(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:2] == b"BM" or data[4:12] == b"ftypavif":
        return IMAGE
    if data[:4] in (b"OggS", b"RIFF", b"fLaC", b"ID3\x03", b"ID3\x04") or data[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return AUDIO
    if data[:8] == b"version ":
        return MESH
    if data[:7] in (b"<roblox", b"<robloxm"[:7]):
        return MODEL
    return MODEL


_index = {"mtime": None, "map": {}}


def store_index():
    """asset id -> file name in STORE (rebuilt when the folder changes)"""
    try:
        m = os.stat(STORE).st_mtime_ns
    except OSError:
        return {}
    if m != _index["mtime"]:
        _index["map"] = {f.split(".")[0]: f for f in os.listdir(STORE) if f.split(".")[0].isdigit()}
        _index["mtime"] = m
    return _index["map"]


_ledger = {"mtime": 0, "map": {}}


def ledger():
    try:
        m = os.path.getmtime(LEDGER)
    except OSError:
        return {}
    if m != _ledger["mtime"]:
        with open(LEDGER) as f:
            _ledger["map"] = {str(v["assetId"]): v["path"] for v in json.load(f).values()
                              if v.get("assetId") and v.get("path")}
        _ledger["mtime"] = m
    return _ledger["map"]


def find(asset_id):
    asset_id = str(asset_id)
    if not asset_id.isdigit():
        return None
    hit = store_index().get(asset_id)
    if hit:
        return os.path.join(STORE, hit)
    path = ledger().get(asset_id)
    if path and os.path.exists(path):
        return path
    return None


def read(asset_id):
    path = find(asset_id)
    if not path:
        return None
    with open(path, "rb") as f:
        data = f.read()
    if sniff(data) == AUDIO and data[:4] != b"OggS" and data[:3] != b"ID3" and data[:2] not in (b"\xff\xfb", b"\xff\xf3"):
        data = to_ogg(path, data)  # Studio only plays what Roblox delivers: OGG/MP3, not WAV/FLAC
    return data


CACHE = os.path.join(DATA, "cache")


def to_ogg(path, data):
    st = os.stat(path)
    out = os.path.join(CACHE, f"{hashlib.sha1(f'{os.path.realpath(path)}:{st.st_size}:{st.st_mtime_ns}'.encode()).hexdigest()}.ogg")
    if not os.path.exists(out):
        os.makedirs(CACHE, exist_ok=True)
        tmp = f"{out}.{os.getpid()}.{threading.get_ident()}.ogg"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-c:a", "libvorbis", "-q:a", "6", tmp])
        if r.returncode != 0:
            return data
        os.replace(tmp, out)
    with open(out, "rb") as f:
        return f.read()


def asset_type(asset_id, data):
    path = find(asset_id) or ""
    if path.endswith(".decal.rbxmx"):
        return DECAL
    return sniff(data)


def batch(body):
    out = []
    for req in json.loads(body or b"[]"):
        rid = req.get("requestId", "0")
        aid = req.get("assetId")
        data = read(aid) if aid is not None else None
        if data is None:
            out.append({"requestId": rid, "errors": [{"code": 404, "message": "Request asset was not found"}]})
        else:
            out.append({"location": LOCAL + str(aid), "requestId": rid, "isArchived": False,
                        "isRecordable": True, "assetTypeId": asset_type(aid, data), "contentRepresentationSpecifier": None})
    return out


def handle(method, host, path):
    """Returns (status, headers, payload) or None."""
    p = path.split("?")[0]
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    if p.startswith("/offline/"):
        aid = p.rsplit("/", 1)[1]
    elif p.rstrip("/") in ("/v1/asset", "/asset") or p.startswith("/v1/assetId/"):
        aid = q.get("id", q.get("ID", [None]))[0] if "/assetId/" not in p else p.split("/assetId/")[1].split("/")[0]
    else:
        return None
    if not p.startswith("/offline/"):
        # like the real endpoint: a redirect to the content (Studio's resolver expects one)
        if find(aid) is None:
            return 404, {}, {"errors": [{"code": 404, "message": "Request asset was not found"}]}
        return 302, {"Location": LOCAL + str(aid)}, b""
    data = read(aid)
    if data is None:
        return 404, {}, {"errors": [{"code": 404, "message": "Request asset was not found"}]}
    kind = asset_type(aid, data)
    ctype = {IMAGE: "image/png" if data[:4] == b"\x89PNG" else "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "application/octet-stream",
             AUDIO: "audio/ogg" if data[:4] == b"OggS" else "audio/mpeg"}.get(kind, "application/octet-stream")
    return 200, {"Content-Type": ctype, "roblox-assettypeid": str(kind)}, data
