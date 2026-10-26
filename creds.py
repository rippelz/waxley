#!/usr/bin/env python3
"""Read/write Studio's saved login in the offline prefix (Wine Credential Manager in user.reg).

Wine encrypts each credential blob with RC4 keyed by HKCU\\Software\\Wine\\Credential Manager\\EncryptionKey.

  creds.py show            list entries (secrets shown only as length)
  creds.py seed            replace every Roblox entry with fake local-only values
                           (mockblox accepts any token, so no real secret is needed)
Run only while the offline prefix's wineserver is stopped. WAXLEY_PREFIX picks another prefix (waxley-player).
"""
import os
import re
import sys
import time
import uuid

DATA = os.environ.get("WAXLEY_DATA", os.path.expanduser("~/.local/share/waxley"))
REG = os.path.join(os.environ.get("WAXLEY_PREFIX") or os.path.join(DATA, "prefix"), "user.reg")
ORIGIN = "https://www.roblox.com:RobloxStudioAuth"
SECRET = ("accessToken", "oauth2RefreshToken", ".ROBLOSECURITY", "Cookies")


def rc4(key, data):
    s, j = list(range(256)), 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out, i, j = bytearray(), 0, 0
    for b in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(b ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def hexval(text, name):
    m = re.search(rf'^"{name}"=hex:((?:[0-9a-f,]|\\\n\s*)*)', text, re.M)
    return bytes.fromhex(re.sub(r"[^0-9a-f]", "", m.group(1))) if m else None


def blocks(reg):
    return re.finditer(r"^\[Software\\\\Wine\\\\Credential Manager\\\\Generic: (?P<target>[^\]]+)\][^\n]*\n(?P<body>(?:(?!\[)[^\n]*\n)*)", reg, re.M)


def fmt_hex(name, data):
    return f'"{name}"=hex:' + ",".join(f"{b:02x}" for b in data)


def entry(target, key, blob):
    ft = int((time.time() + 11644473600) * 10_000_000).to_bytes(8, "little")
    return (f'[Software\\\\Wine\\\\Credential Manager\\\\Generic: {target}] {int(time.time())}\n'
            f'@="{target}"\n"Flags"=dword:00000000\n{fmt_hex("LastWritten", ft)}\n'
            f'{fmt_hex("Password", rc4(key, blob))}\n"Persist"=dword:00000002\n"Type"=dword:00000001\n\n')


def main():
    reg = open(REG, encoding="utf-8", errors="surrogateescape").read()
    key = hexval(reg.split("[Software\\\\Wine\\\\Credential Manager]", 1)[1], "EncryptionKey")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    found = {}
    for m in blocks(reg):
        blob = rc4(key, hexval(m["body"], "Password") or b"")
        found[m["target"]] = (m, blob)
    if cmd == "show":
        for t, (_, blob) in found.items():
            name = t.replace(ORIGIN, "")
            shown = f"<{len(blob)} bytes>" if any(s in name for s in SECRET) else blob.decode("utf-16-le", "replace") if blob[1:2] == b"\0" else blob
            print(f"{name}: {shown}")
        return
    if cmd == "seed":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import routes
        uid = str(routes.USER["id"])
        tok = routes.token_response()
        values = {"userid": uid,
                  "Cookies": ".ROBLOSECURITY;",
                  ".ROBLOSECURITY" + uid: "_WAXLEY-LOCAL-ONLY_" + uuid.uuid4().hex,
                  "accessToken" + uid: tok["access_token"],
                  "oauth2RefreshToken" + uid: tok["refresh_token"],
                  "expiresAtSecSinceEpoch" + uid: str(int(time.time()) - 60)}
        for t, (m, _) in found.items():
            if t.startswith(ORIGIN):
                reg = reg.replace(m.group(0), "")
        reg = reg.rstrip("\n") + "\n\n" + "".join(entry(ORIGIN + k, key, v.encode()) for k, v in values.items())
        open(REG, "w", encoding="utf-8", errors="surrogateescape").write(reg)
        print("seeded", ", ".join(values))


if __name__ == "__main__":
    main()
